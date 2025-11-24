# analyzer/scope.py

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Iterable, List, Optional

from scanner.analyzer.symbol import Symbol, SymbolTable

# ============================================================
# ScopeType — rough analogue of Pyright's ScopeType 
# ============================================================


class ScopeType(Enum):
    """Rough analogue of ScopeType in pyright's scope.ts, trimmed for this project."""

    BUILTIN = auto()
    MODULE = auto()
    CLASS = auto()
    FUNCTION = auto()
    COMPREHENSION = auto()


# ============================================================
# Scope — owns a symbol table and child scopes 
# ============================================================


@dataclass
class Scope:
    """
    Minimal analogue of Pyright's Scope.

    Each Scope has:
      - scope_type : kind of scope (module / function / class / builtin / comprehension)
      - parent     : lexical parent scope (None for top-level builtins)
      - symbols    : SymbolTable (name -> Symbol from analyzer.symbol)
      - children   : nested scopes (functions, classes, comprehensions, etc.)
    """

    scope_type: ScopeType
    parent: Optional["Scope"] = None

    symbols: SymbolTable = field(default_factory=dict)
    children: List["Scope"] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.parent is not None:
            self.parent.children.append(self)

    # ------------------------------------------------------------------
    # Symbol management
    # ------------------------------------------------------------------

    def add_symbol(
        self,
        name: str,
        kind: str = "variable",
        target: Optional[str] = None,
        declaration: Optional[Any] = None,
    ) -> Symbol:
        """
        Add or update a symbol in this scope and return it.

        Parameters
        ----------
        name:
            Identifier name in this scope.

        kind:
            High-level category ("import", "variable", "function", "class", ...).
            For now, this is used only for debugging / to_dict() output by
            storing it as a lightweight declaration tag if no richer
            declaration object is provided.

        target:
            For imports, the fully-qualified target (e.g. "pandas",
            "pandas.read_csv"). For non-import symbols, this is typically None.

        declaration:
            Optional declaration object (e.g. an AST node or small dataclass).
            If provided, it is appended to the symbol's declarations list.
        """
        sym = self.symbols.get(name)
        if sym is None:
            sym = Symbol(name=name, target=target)
            self.symbols[name] = sym
        else:
            # If the symbol already exists but has no target yet, adopt the new one.
            if target is not None and sym.target is None:
                sym.target = target

        if declaration is not None:
            sym.add_declaration(declaration)
        else:
            # Record the "kind" as a lightweight declaration tag so it
            # shows up in dumps/debug output.
            sym.add_declaration(f"<{kind}>")

        return sym

    def get_symbol(self, name: str) -> Optional[Symbol]:
        """Look up a symbol in this scope only."""
        return self.symbols.get(name)

    def lookup(self, name: str) -> Optional[Symbol]:
        """
        Look up a symbol starting from this scope and walking up parents.
        """
        scope: Optional[Scope] = self
        while scope is not None:
            sym = scope.symbols.get(name)
            if sym is not None:
                return sym
            scope = scope.parent
        return None

    def iter_symbols(self) -> Iterable[Symbol]:
        """Iterate over symbols defined in this scope (no parents)."""
        return self.symbols.values()

    # ------------------------------------------------------------------
    # Debug / sampling helpers (for Program.dump_scopes)
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """
        Return a JSON-serializable representation of this scope and its children.

        This is used by Program.dump_scopes() so you can inspect what the
        Binder produced without depending on internal Symbol/Scope classes.
        """
        return {
            "scope_type": self.scope_type.name,
            "symbols": [
                {
                    "name": s.name,
                    "target": s.target,
                    "flags": int(s.flags),
                    "declarations": [repr(d) for d in s.declarations],
                }
                for s in self.symbols.values()
            ],
            "children": [child.to_dict() for child in self.children],
        }
