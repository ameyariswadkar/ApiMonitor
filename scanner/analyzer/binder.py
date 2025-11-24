# analyzer/binder.py

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Optional

from scanner.analyzer.SourceFile import SourceFile
from scanner.analyzer.scope import Scope, ScopeType, Symbol
from scanner.parser.parseNodes import ImportNode, ModuleNode


@dataclass
class BinderConfig:
    """
    Minimal config stub for Binder.

    Right now we just keep a reference to the builtin scope. This mirrors, in a
    very small way, how pyright threads a builtins scope into binding/checking.
    """

    builtin_scope: Scope


class Binder:
    """
    Minimal analogue of pyright's binder.ts for this project.

    Responsibilities:

      - For each SourceFile, create a module scope whose parent is the builtin
        scope.
      - Populate that scope with symbols corresponding to:
          * imports (from your custom ModuleNode)
          * top-level function definitions
          * top-level class definitions
          * simple module-level variables

      - Attach the resulting scope to the SourceFile so later stages can inspect
        module-level symbols and you can dump scopes for debugging.

    Each symbol records a simple human-readable declaration string in
    `Symbol.declarations`. This is your first step toward a "Declaration" layer,
    kept intentionally lightweight.
    """

    def __init__(self, config: BinderConfig) -> None:
        self._builtin_scope = config.builtin_scope

    # ------------------------------------------------------------------ #
    # public API                                                         #
    # ------------------------------------------------------------------ #

    def bind_source_file(self, source_file: SourceFile) -> Scope:
        """
        Bind a single SourceFile: create its module scope, process imports and
        top-level definitions, attach scope to the SourceFile, and return it.
        """
        module: Optional[ModuleNode] = source_file.module

        # Even if parsing failed and we don't have a module node, we still
        # create a module scope so downstream code has something to hang on to.
        module_scope = Scope(scope_type=ScopeType.MODULE, parent=self._builtin_scope)

        if module is not None:
            self._bind_module(module, module_scope, source_file)

        # Always attach the scope, even if mostly empty.
        source_file.scope = module_scope
        return module_scope

    # ------------------------------------------------------------------ #
    # internal binding helpers                                           #
    # ------------------------------------------------------------------ #

    def _bind_module(
        self,
        module: ModuleNode,
        module_scope: Scope,
        source_file: SourceFile,
    ) -> None:
        """
        Bind imports and top-level definitions for a ModuleNode.

        From your custom parse tree we currently only have:
          - `module.imports` → ImportNode[]

        For richer symbol info (functions, classes, variables) we additionally
        parse the file text with stdlib `ast` and bind:

          - `def foo(...):`           → function symbol
          - `class Bar(...):`         → class symbol
          - `X = 123` / `X: int = 1`  → variable symbol
        """
        # 1) Bind imports from your custom ModuleNode.
        for imp in module.imports:
            self._bind_import_node(imp, module_scope, source_file)

        # 2) Bind top-level defs via stdlib ast (independent of your custom tree).
        self._bind_top_level_defs_via_ast(module_scope, source_file)

    # ---------------------- imports ----------------------------------- #

    def _bind_import_node(
        self,
        imp: ImportNode,
        scope: Scope,
        source_file: SourceFile,
    ) -> None:
        """
        Create symbols for an ImportNode and record a simple declaration string.

        For `import pkg as p`          -> symbol name: `p` (or `pkg`), target: `pkg`
        For `from pkg import x as y`   -> symbol name: `y` (or `x`), target: `pkg.x`
        For `from pkg import *`        -> symbol name: `pkg` (fallback), target: `pkg`
        """
        if imp.is_from_import:
            # from module import name as alias
            for alias in imp.aliases:
                # alias.name  : imported attribute (None for `from x import *`)
                # alias.alias : local name (if present)
                # alias.module: module string
                local_name = alias.alias or alias.name or alias.module

                if alias.name is not None:
                    # from <module> import <name> [as <alias>]
                    target = f"{alias.module}.{alias.name}"
                    decl_text = self._format_from_import_decl(
                        module=alias.module,
                        name=alias.name,
                        alias=alias.alias,
                    )
                else:
                    # from <module> import *  (or other weird cases)
                    target = alias.module
                    decl_text = self._format_from_import_star_decl(module=alias.module)

                sym = scope.add_symbol(local_name, kind="import", target=target)
                self._record_declaration_with_offset(sym, decl_text, source_file, imp)
        else:
            # import module as alias
            for alias in imp.aliases:
                # module import: alias.module is the full module name
                local_name = alias.alias or alias.module
                target = alias.module
                decl_text = self._format_plain_import_decl(module=alias.module, alias=alias.alias)

                sym = scope.add_symbol(local_name, kind="import", target=target)
                self._record_declaration_with_offset(sym, decl_text, source_file, imp)

    # ---------------------- top-level defs via ast -------------------- #

    def _bind_top_level_defs_via_ast(self, module_scope: Scope, source_file: SourceFile) -> None:
        """
        Use stdlib `ast` to find top-level function defs, class defs, and simple
        assignments in this file, and create corresponding symbols.

        This is intentionally lightweight and only looks at *module-level* nodes.
        """
        try:
            text = source_file.read_contents()
            tree = ast.parse(text, filename=str(source_file.path))
        except SyntaxError:
            # If stdlib ast fails, just skip; your custom parser already reported diagnostics.
            return

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._bind_function_def(node, module_scope, source_file)
            elif isinstance(node, ast.ClassDef):
                self._bind_class_def(node, module_scope, source_file)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                self._bind_assignment(node, module_scope, source_file)

    def _bind_function_def(
        self,
        node: ast.AST,
        scope: Scope,
        source_file: SourceFile,
    ) -> None:
        """
        Bind a top-level function definition: def foo(...): ...
        """
        name = getattr(node, "name", None)
        if not name:
            return

        sym = scope.add_symbol(name, kind="function", target=None)
        sig = f"def {name}(...)"
        self._record_declaration_with_line_col(sym, sig, source_file, node)

    def _bind_class_def(
        self,
        node: ast.ClassDef,
        scope: Scope,
        source_file: SourceFile,
    ) -> None:
        """
        Bind a top-level class definition: class Foo(...): ...
        """
        name = node.name
        sym = scope.add_symbol(name, kind="class", target=None)
        sig = f"class {name}"
        self._record_declaration_with_line_col(sym, sig, source_file, node)

    def _bind_assignment(
        self,
        node: ast.AST,
        scope: Scope,
        source_file: SourceFile,
    ) -> None:
        """
        Bind simple top-level assignments:

            X = expr
            X: int = expr
        """
        # Collect assigned names from Assign or AnnAssign.
        targets = []
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    targets.append(t.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                targets.append(node.target.id)

        for name in targets:
            sym = scope.add_symbol(name, kind="variable", target=None)
            decl = f"{name} = <value>"
            self._record_declaration_with_line_col(sym, decl, source_file, node)

    # ------------------------------------------------------------------ #
    # declaration helpers                                                #
    # ------------------------------------------------------------------ #

    def _record_declaration_with_offset(
        self,
        symbol: Symbol,
        decl_text: str,
        source_file: SourceFile,
        node: ImportNode,
    ) -> None:
        """
        Attach a declaration description using a character offset stored on the
        custom parse node (ImportNode).
        """
        offset = getattr(node, "start", None)
        if offset is not None:
            location = f"{source_file.path} @ offset {offset}"
        else:
            location = str(source_file.path)

        symbol.declarations.append(f"{decl_text}  ({location})")

    def _record_declaration_with_line_col(
        self,
        symbol: Symbol,
        decl_text: str,
        source_file: SourceFile,
        node: ast.AST,
    ) -> None:
        """
        Attach a declaration description using line/column info from stdlib ast nodes.
        """
        lineno = getattr(node, "lineno", None)
        col = getattr(node, "col_offset", None)

        if lineno is not None and col is not None:
            location = f"{source_file.path}:{lineno}:{col}"
        else:
            location = str(source_file.path)

        symbol.declarations.append(f"{decl_text}  ({location})")

    # ------------------------------------------------------------------ #
    # tiny formatting helpers for import decls                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_plain_import_decl(module: str, alias: Optional[str]) -> str:
        """
        Format a simple `import module [as alias]` declaration.
        """
        if alias:
            return f"import {module} as {alias}"
        return f"import {module}"

    @staticmethod
    def _format_from_import_decl(
        module: str,
        name: str,
        alias: Optional[str],
    ) -> str:
        """
        Format a `from module import name [as alias]` declaration.
        """
        if alias:
            return f"from {module} import {name} as {alias}"
        return f"from {module} import {name}"

    @staticmethod
    def _format_from_import_star_decl(module: str) -> str:
        """
        Format a `from module import *` declaration.
        """
        return f"from {module} import *"
