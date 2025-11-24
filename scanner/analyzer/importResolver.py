# analyzer/importResolver.py

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from scanner.analyzer.SourceFile import SourceFile


@dataclass
class ImportResolution:
    """
    A single logical import edge.

    module:
        The fully-qualified module name we think this import refers to
        (e.g. "pkg.sub.mod").
    file:
        The resolved Python file on disk, if we found one under project_root.
        None if we couldn't resolve it (stdlib, third-party, or unresolved).
    is_local:
        True if `file` is a local project file (i.e. under project_root).
    imported_names:
        For `from mod import a, b`, this will be ["a", "b"].
        For `import pkg.sub`, this is typically empty.
    """

    module: str
    file: Optional[Path]
    is_local: bool
    imported_names: List[str] = field(default_factory=list)


class ImportResolver:
    """
    Minimal import resolver.

    It is intentionally much smaller than pyright's ImportResolver:
      - only looks under a single project_root
      - uses simple heuristics to compute module names & file paths
      - ignores stub/py.typed complexity

    This is enough to build a cross-file dependency graph for your project.
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root.resolve()

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def resolve_imports_for_source_file(self, source_file: "SourceFile") -> List[ImportResolution]:
        """
        Scan a SourceFile's contents with `ast` and return a list of
        ImportResolution objects.

        This does not depend on your custom parser at all, so it can
        happily coexist with your ModuleNode tree.
        """
        text = source_file.read_contents()
        try:
            tree = ast.parse(text, filename=str(source_file.path))
        except SyntaxError:
            # If stdlib ast can't parse (weird syntax), just say "no imports".
            return []

        resolutions: List[ImportResolution] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name  # e.g. "pkg.sub.module"
                    file_path = self._resolve_module_to_path(module)
                    is_local = self._is_under_project_root(file_path) if file_path else False
                    resolutions.append(
                        ImportResolution(
                            module=module,
                            file=file_path,
                            is_local=is_local,
                            imported_names=[],  # plain "import pkg.sub" – no specific names
                        )
                    )

            elif isinstance(node, ast.ImportFrom):
                base_module = self._compute_base_module_for_from(
                    source_file_path=source_file.path,
                    module=node.module,
                    level=node.level,
                )
                if not base_module:
                    # Could not compute a sensible module name; skip.
                    continue

                file_path = self._resolve_module_to_path(base_module)
                is_local = self._is_under_project_root(file_path) if file_path else False
                imported_names = [alias.name for alias in node.names]

                resolutions.append(
                    ImportResolution(
                        module=base_module,
                        file=file_path,
                        is_local=is_local,
                        imported_names=imported_names,
                    )
                )

        return resolutions

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    def _is_under_project_root(self, path: Optional[Path]) -> bool:
        if path is None:
            return False
        try:
            path.resolve().relative_to(self._project_root)
            return True
        except ValueError:
            return False

    def _resolve_module_to_path(self, module: str) -> Optional[Path]:
        """
        Heuristically map "pkg.sub.mod" -> project_root/pkg/sub/mod.py
        or project_root/pkg/sub/mod/__init__.py.
        """
        if not module:
            return None

        parts = module.split(".")
        candidate_file = self._project_root.joinpath(*parts).with_suffix(".py")
        if candidate_file.is_file():
            return candidate_file.resolve()

        # Try package __init__.py
        candidate_pkg = self._project_root.joinpath(*parts) / "__init__.py"
        if candidate_pkg.is_file():
            return candidate_pkg.resolve()

        return None

    def _module_name_for_path(self, path: Path) -> Optional[str]:
        """
        Compute a "dotted" module name from a file path relative to project_root.

        Example:
            project_root = /repo
            path         = /repo/pkg/sub/mod.py
            -> "pkg.sub.mod"
        """
        try:
            rel = path.resolve().relative_to(self._project_root)
        except ValueError:
            return None

        if rel.suffix != ".py":
            return None

        return ".".join(rel.with_suffix("").parts)

    def _compute_base_module_for_from(
        self,
        source_file_path: Path,
        module: Optional[str],
        level: int,
    ) -> Optional[str]:
        """
        Compute the effective module name for a `from ... import ...` node.

        - level == 0: absolute import, `module` is used as-is.
        - level > 0 : relative import, interpreted relative to the current
                      file's package.
        """
        if level == 0:
            # Absolute import: from pkg.sub import x
            return module or None

        # Relative import: from . import x, from .utils import y, from ..pkg import z, etc.
        current_mod = self._module_name_for_path(source_file_path)
        if not current_mod:
            return None

        # Split current module name into parts: "pkg.sub.mod" -> ["pkg", "sub", "mod"]
        parts = current_mod.split(".")

        # The current file is a module, so its package path is everything except
        # the last component.
        package_parts = parts[:-1]

        # `level` counts how many dots:
        #   from .   import x → level = 1 (stay at same package)
        #   from ..  import x → level = 2 (go up one package)
        #   from ... import x → level = 3 (go up two packages)
        #
        # So the number of "ups" is (level - 1).
        ups = max(level - 1, 0)
        if ups > len(package_parts):
            # Too many ".." – can't go that far up.
            return None

        base_parts = package_parts[: len(package_parts) - ups]
        if module:
            base_parts.extend(module.split("."))

        if not base_parts:
            return None

        return ".".join(base_parts)
