from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from scanner.analyzer.SourceFile import SourceFile, Diagnostic
from scanner.analyzer.binder import Binder
from scanner.analyzer.importResolver import ImportResolver, ImportResolution
from scanner.analyzer.symbol import Symbol
from scanner.parser.parser import Parser


# ============================================================
# ProgramConfig — analogous to Pyright’s ConfigOptions
# ============================================================


@dataclass
class ProgramConfig:
    """
    Minimal analogue of Pyright’s ConfigOptions / ExecutionEnvironment.

    Extend later with:
      - excludes/includes
      - python version / platform
      - extra search paths

    NOTE: We keep target_libraries for backwards compatibility, but *this*
    Program class no longer relies on it to decide which calls are "external".
    Instead, we infer external vs local from the import graph (ImportResolver).
    """
    project_root: Path
    target_libraries: List[str] = field(default_factory=list)


# ============================================================
# ResolvedSymbol — result of single-name resolution
# ============================================================


@dataclass
class ResolvedSymbol:
    """
    Result of resolving a *name* in a particular file.

    Fields
    ------
    name:
        The identifier being resolved (e.g. "pd", "np", "foo").

    symbol:
        The Symbol object from the scope where it was found.

    defining_file:
        The file that "owns" the definition:
          - If it's a local definition: the current file.
          - If it's an imported alias and the module is local: that module file.
          - Otherwise: None.

    is_import:
        True if the symbol is an import alias (i.e. symbol.target is set).

    import_module:
        Fully-qualified import target if `is_import` is True.
        Example: "pandas", "pandas.read_csv".

    import_is_local:
        For imports only:
          - True  → module is inside project_root
          - False → stdlib / third-party / unresolved
          - None  → symbol wasn’t an import or we couldn’t match it
    """

    name: str
    symbol: Symbol
    defining_file: Optional[Path]
    is_import: bool
    import_module: Optional[str]
    import_is_local: Optional[bool]


# ============================================================
# QualifiedResolvedSymbol — for base.attr resolution
# ============================================================


@dataclass
class QualifiedResolvedSymbol:
    """
    Result of resolving a qualified reference like `base.attr`.

    Typical usage:
      - base = "pd", attr = "read_csv"
      - resolve_qualified_symbol(file, "pd", "read_csv")
    """

    base: ResolvedSymbol
    attr_name: str
    attr_symbol: Optional[Symbol]
    attr_defining_file: Optional[Path]


# ============================================================
# ApiCall — single API call site (for reporting / Kafka / CSV)
# ============================================================


@dataclass
class ApiCall:
    """
    Represents one API call site discovered in a file.

    repo:
        Logical repo name (from SourceFile.repo).

    file:
        Path to the Python file containing the call.

    symbol_called:
        A best-effort fully-qualified symbol string such as
        "urllib3.util.Timeout" or "socket.inet_aton".

    library:
        The top-level library/module name (first component of symbol_called),
        e.g. "urllib3" or "socket". Handy for aggregation.

    signature_shape:
        Light-weight "shape" of the call:
          {
            "positional_count": int,
            "keyword_args": [str, ...],
            "has_varargs": bool,
            "has_varkw": bool,
          }

    is_external:
        True if this call is classified as external (import that is not local).

    defining_file:
        If the symbol ultimately resolves to a local file, that file path.
        For external libs, usually None.

    import_module:
        Raw import target string from the symbol (e.g. "requests.api.get"),
        if this was an import alias; otherwise None.
    """

    repo: str
    file: Path
    symbol_called: str
    library: str
    signature_shape: Dict[str, object]
    is_external: bool
    defining_file: Optional[Path]
    import_module: Optional[str]


# ============================================================
# Program  — central orchestrator (like Pyright’s Program)
# ============================================================


class Program:
    """
    Holds all source files, runs parser + binder + import resolution,
    and builds dependency info.

    == Pipeline (mimics pyright) ==
      set_tracked_files()      → SourceFile objects created
      analyze()                → parse → bind → resolve imports
      build_dependency_graph() → recompute import graph

    Fields:
      - _files        : path → SourceFile
      - _import_graph : path → List[ImportResolution]

    New responsibilities:
      - collect_api_calls(external_only=True) walks stdlib `ast` for Call
        nodes, uses resolve_symbol/ImportResolver to decide whether the
        call targets an EXTERNAL library (i.e. not under project_root),
        and returns structured ApiCall records.
    """

    def __init__(
        self,
        config: ProgramConfig,
        parser: Parser,
        binder: Optional[Binder] = None,
        import_resolver: Optional[ImportResolver] = None,
    ) -> None:
        self._config = config
        self._parser = parser
        self._binder = binder
        self._import_resolver = import_resolver or ImportResolver(config.project_root)

        self._files: Dict[Path, SourceFile] = {}  # path → SourceFile
        self._import_graph: Dict[Path, List[ImportResolution]] = {}

    # --------------------------------------------------------
    # Basic properties
    # --------------------------------------------------------

    @property
    def config(self) -> ProgramConfig:
        return self._config

    @property
    def project_root(self) -> Path:
        return self._config.project_root

    # --------------------------------------------------------
    # SourceFile management
    # --------------------------------------------------------

    def set_tracked_files(self, files: Iterable[Path], repo_name: str) -> None:
        new_paths = {p.resolve() for p in files}
        old_paths = set(self._files.keys())

        for removed in old_paths - new_paths:
            self._files.pop(removed, None)
            self._import_graph.pop(removed, None)

        for path in new_paths:
            abs_path = path.resolve()
            if abs_path not in self._files:
                self._files[abs_path] = SourceFile(path=abs_path, repo=repo_name)

    def add_tracked_file(self, path: Path, repo: str) -> SourceFile:
        abs_path = path.resolve()
        if abs_path in self._files:
            return self._files[abs_path]

        sf = SourceFile(path=abs_path, repo=repo)
        self._files[abs_path] = sf
        return sf

    def get_source_file(self, path: Path) -> Optional[SourceFile]:
        return self._files.get(path.resolve())

    def get_files(self) -> List[SourceFile]:
        return list(self._files.values())

    def get_file_count(self) -> int:
        return len(self._files)

    # --------------------------------------------------------
    # Main analysis pipeline  — parse → bind → import resolution
    # --------------------------------------------------------

    def analyze(self) -> None:
        for sf in self._files.values():
            self._parse_bind_resolve(sf)

    def analyze_single(self, path: Path) -> None:
        sf = self.get_source_file(path)
        if sf is None:
            raise KeyError(f"File not tracked in Program: {path}")
        self._parse_bind_resolve(sf)

    def _parse_bind_resolve(self, sf: SourceFile) -> None:
        sf.parse(self._parser)

        if self._binder is not None and sf.module is not None:
            self._binder.bind_source_file(sf)

        resolutions = self._import_resolver.resolve_imports_for_source_file(sf)
        sf.import_edges = resolutions
        self._import_graph[sf.path.resolve()] = resolutions

    # --------------------------------------------------------
    # Dependency graph / cross-file helpers
    # --------------------------------------------------------

    def build_dependency_graph(self) -> None:
        self._import_graph.clear()
        for sf in self._files.values():
            resolutions = self._import_resolver.resolve_imports_for_source_file(sf)
            sf.import_edges = resolutions
            self._import_graph[sf.path.resolve()] = resolutions

    def get_import_graph(self) -> Dict[Path, List[ImportResolution]]:
        return dict(self._import_graph)

    def get_imports_of(self, path: Path) -> List[ImportResolution]:
        return self._import_graph.get(path.resolve(), [])

    def get_dependents_of(self, path: Path) -> List[Path]:
        target = path.resolve()
        dependents: List[Path] = []

        for origin, edges in self._import_graph.items():
            for edge in edges:
                if edge.file is not None and edge.file.resolve() == target:
                    dependents.append(origin)
                    break

        return dependents

    # --------------------------------------------------------
    # Symbol-level cross-file lookup (single name)
    # --------------------------------------------------------

    def resolve_symbol(self, file: Path, name: str) -> Optional[ResolvedSymbol]:
        sf = self.get_source_file(file)
        if sf is None or sf.scope is None:
            return None

        sym = sf.scope.lookup(name)
        if sym is None:
            return None

        if sym.target:
            module_qual, _ = self._split_symbol_target(sym.target)
            edge = self._find_import_edge_for_module(sf, module_qual)

            if edge is not None:
                defining_file = edge.file if (edge.file and edge.is_local) else None
                return ResolvedSymbol(
                    name=name,
                    symbol=sym,
                    defining_file=defining_file,
                    is_import=True,
                    import_module=sym.target,
                    import_is_local=edge.is_local,
                )

            return ResolvedSymbol(
                name=name,
                symbol=sym,
                defining_file=None,
                is_import=True,
                import_module=sym.target,
                import_is_local=None,
            )

        return ResolvedSymbol(
            name=name,
            symbol=sym,
            defining_file=sf.path,
            is_import=False,
            import_module=None,
            import_is_local=None,
        )

    # --------------------------------------------------------
    # Qualified cross-file lookup: base.attr
    # --------------------------------------------------------

    def resolve_qualified_symbol(
        self,
        file: Path,
        base_name: str,
        attr_name: str,
    ) -> Optional[QualifiedResolvedSymbol]:
        base_res = self.resolve_symbol(file, base_name)
        if base_res is None:
            return None

        if not base_res.is_import or not base_res.import_module:
            return QualifiedResolvedSymbol(
                base=base_res,
                attr_name=attr_name,
                attr_symbol=None,
                attr_defining_file=None,
            )

        module_qual, _ = self._split_symbol_target(base_res.import_module)

        sf = self.get_source_file(file)
        if sf is None:
            return QualifiedResolvedSymbol(
                base=base_res,
                attr_name=attr_name,
                attr_symbol=None,
                attr_defining_file=None,
            )

        edge = self._find_import_edge_for_module(sf, module_qual)
        if edge is None or not edge.is_local or edge.file is None:
            return QualifiedResolvedSymbol(
                base=base_res,
                attr_name=attr_name,
                attr_symbol=None,
                attr_defining_file=None,
            )

        target_sf = self.get_source_file(edge.file)
        if target_sf is None or target_sf.scope is None:
            return QualifiedResolvedSymbol(
                base=base_res,
                attr_name=attr_name,
                attr_symbol=None,
                attr_defining_file=None,
            )

        attr_sym = target_sf.scope.lookup(attr_name)
        return QualifiedResolvedSymbol(
            base=base_res,
            attr_name=attr_name,
            attr_symbol=attr_sym,
            attr_defining_file=target_sf.path if attr_sym is not None else None,
        )

    # --------------------------------------------------------
    # API call collection (external libs)
    # --------------------------------------------------------

    def collect_api_calls(self, external_only: bool = True) -> List[ApiCall]:
        """
        Walk all tracked files, scan for ast.Call nodes, and return ApiCall
        records.

        external_only=True:
            Only return calls whose base name resolves to an IMPORT symbol
            whose module is NOT local to `project_root`. In other words:
            stdlib and third-party packages are treated as "external".

        external_only=False:
            Return all calls we can map to a symbol (local or imported).
        """
        calls: List[ApiCall] = []
        for sf in self._files.values():
            calls.extend(self._collect_api_calls_for_file(sf, external_only=external_only))
        return calls

    def _collect_api_calls_for_file(self, sf: SourceFile, external_only: bool) -> List[ApiCall]:
        try:
            text = sf.read_contents()
            tree = ast.parse(text, filename=str(sf.path))
        except SyntaxError:
            return []

        result: List[ApiCall] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            qualifier = self._extract_call_qualifier(node.func)
            if qualifier is None:
                continue

            base_name, attr_chain = qualifier
            if base_name is None:
                continue

            resolved = self.resolve_symbol(sf.path, base_name)
            if resolved is None:
                continue

            # "External" if it came from an import that is not local to project_root.
            is_external = False
            if resolved.is_import:
                is_external = not bool(resolved.import_is_local)

            if external_only and not is_external:
                continue

            symbol_called = self._build_symbol_called(resolved, attr_chain)
            if not symbol_called:
                continue

            signature_shape = self._compute_signature_shape(node)
            library = symbol_called.split(".", 1)[0]

            result.append(
                ApiCall(
                    repo=sf.repo,
                    file=sf.path,
                    symbol_called=symbol_called,
                    library=library,
                    signature_shape=signature_shape,
                    is_external=is_external,
                    defining_file=resolved.defining_file,
                    import_module=resolved.import_module,
                )
            )

        return result

    # --------------------------------------------------------
    # Internal helpers
    # --------------------------------------------------------

    @staticmethod
    def _split_symbol_target(target: str) -> tuple[str, Optional[str]]:
        if "." in target:
            base, attr = target.rsplit(".", 1)
            return base, attr
        return target, None

    @staticmethod
    def _find_import_edge_for_module(
        sf: SourceFile,
        module_name: str,
    ) -> Optional[ImportResolution]:
        for edge in sf.import_edges:
            if edge.module == module_name:
                return edge
        return None

    @staticmethod
    def _compute_signature_shape(node: ast.Call) -> Dict[str, object]:
        positional_count = sum(1 for arg in node.args if not isinstance(arg, ast.Starred))
        keyword_args = sorted(kw.arg for kw in node.keywords if kw.arg is not None)
        has_varargs = any(isinstance(arg, ast.Starred) for arg in node.args)
        has_varkw = any(kw.arg is None for kw in node.keywords)

        return {
            "positional_count": positional_count,
            "keyword_args": keyword_args,
            "has_varargs": has_varargs,
            "has_varkw": has_varkw,
        }

    @staticmethod
    def _extract_call_qualifier(func: ast.expr) -> Optional[Tuple[Optional[str], List[str]]]:
        if isinstance(func, ast.Name):
            return func.id, []

        if isinstance(func, ast.Attribute):
            attrs: List[str] = []
            current: ast.expr = func
            while isinstance(current, ast.Attribute):
                attrs.insert(0, current.attr)
                current = current.value

            if isinstance(current, ast.Name):
                root_name = current.id
                return root_name, attrs

        return None

    @staticmethod
    def _build_symbol_called(
        resolved: ResolvedSymbol,
        attr_chain: List[str],
    ) -> Optional[str]:
        if resolved.is_import and resolved.import_module:
            module_qual, imported_attr = Program._split_symbol_target(resolved.import_module)

            if imported_attr is not None:
                if attr_chain:
                    return f"{module_qual}.{imported_attr}." + ".".join(attr_chain)
                return resolved.import_module

            if attr_chain:
                return f"{module_qual}." + ".".join(attr_chain)
            return module_qual

        if attr_chain:
            return f"{resolved.name}." + ".".join(attr_chain)
        return resolved.name

    # --------------------------------------------------------
    # Diagnostics & scope dumping
    # --------------------------------------------------------

    def get_diagnostics(self) -> List[Diagnostic]:
        diags: List[Diagnostic] = []
        for sf in self._files.values():
            diags.extend(sf.diagnostics)
        return diags

    def dump_scopes(self) -> dict:
        result: Dict[str, object] = {}
        for sf in self._files.values():
            if sf.scope is not None:
                result[str(sf.path)] = sf.scope.to_dict()
        return result
