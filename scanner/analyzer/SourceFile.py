# analyzer/SourceFile.py

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from scanner.parser.parseNodes import ModuleNode
from scanner.parser.parser import Parser

from scanner.analyzer.scope import Scope

if TYPE_CHECKING:
    # Only for type checkers – avoids runtime import cycles.
    from scanner.analyzer.importResolver import ImportResolution


@dataclass
class Diagnostic:
    """Very small diagnostic structure (message + location)."""

    message: str
    file: str
    line: int
    column: int


@dataclass
class SourceFile:
    """
    Minimal analogue of pyright's SourceFile.

    It represents a single Python file, its contents, its parsed ModuleNode,
    and any diagnostics produced during parsing.

    Fields:
      - path          : filesystem path of this file
      - repo          : logical repo name (label you pass from Program)
      - module        : root ModuleNode from your custom parser (or None on error)
      - diagnostics   : parse/other diagnostics attached to this file
      - _contents_cache: cached text contents so we don't re-read disk
      - scope         : bound Scope for this module (set by Binder)
      - import_edges  : list of ImportResolution objects for this file
                        (filled by ImportResolver via Program)
    """

    path: Path
    repo: str

    module: Optional[ModuleNode] = None
    diagnostics: List[Diagnostic] = field(default_factory=list)
    _contents_cache: Optional[str] = None

    # Filled in by analyzer/binder.py
    scope: Optional[Scope] = None

    # Filled in by analyzer/importResolver.py
    import_edges: List["ImportResolution"] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # file contents                                                      #
    # ------------------------------------------------------------------ #

    def read_contents(self) -> str:
        """
        Return the file's text contents, cached after the first read.

        This mirrors the way pyright caches file text to avoid repeated
        disk I/O for the same SourceFile.
        """
        if self._contents_cache is None:
            self._contents_cache = self.path.read_text(encoding="utf-8")
        return self._contents_cache

    def invalidate_contents(self) -> None:
        """
        Clear cached contents and parse results.

        This is similar in spirit to pyright's invalidation when a file
        changes in the editor.
        """
        self._contents_cache = None
        self.module = None
        self.diagnostics.clear()

    # ------------------------------------------------------------------ #
    # parsing                                                            #
    # ------------------------------------------------------------------ #

    def parse(self, parser: Parser) -> None:
        """
        Parse the file using the given Parser and populate `module`
        and `diagnostics`.

        For now, diagnostics are only parse errors; you can extend this
        later with semantic checks.
        """
        self.diagnostics.clear()

        try:
            self.module = parser.parse_file(self.path, self.repo)
        except SyntaxError as e:
            # Very simple syntax diagnostic; you can fancy this up later.
            self.module = None
            self.diagnostics.append(
                Diagnostic(
                    message=str(e),
                    file=str(self.path),
                    line=getattr(e, "lineno", 0) or 0,
                    column=getattr(e, "offset", 0) or 0,
                )
            )
        except Exception as e:  # pragma: no cover (defensive)
            self.module = None
            self.diagnostics.append(
                Diagnostic(
                    message=f"Unexpected error while parsing: {e}",
                    file=str(self.path),
                    line=0,
                    column=0,
                )
            )
