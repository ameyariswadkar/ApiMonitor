# analyzer/symbol.py

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntFlag, auto
from typing import Dict, List, Optional, Protocol, runtime_checkable, Any


# ============================================================
# SymbolFlags — rough analogue of Pyright's SymbolFlags
# ============================================================


class SymbolFlags(IntFlag):
    """
    Bitflags describing properties of a symbol.

    This mirrors the structure of Pyright's SymbolFlags enum but we only
    *use* a tiny subset right now. The rest are here so that the shape of
    the API looks familiar if you cross-reference symbol.ts.
    """

    NONE = 0

    # Indicates that the symbol is unbound at the start of execution.
    INITIALLY_UNBOUND = 1 << 0

    # Indicates that the symbol is not visible from other files.
    EXTERNALLY_HIDDEN = 1 << 1

    # Indicates that the symbol is a class member of a class.
    CLASS_MEMBER = 1 << 2

    # Indicates that the symbol is an instance member of a class.
    INSTANCE_MEMBER = 1 << 3

    # Indicates that the symbol is specified in the __slots__ declaration.
    SLOTS_MEMBER = 1 << 4

    # Considered “private” to the module/class.
    PRIVATE_MEMBER = 1 << 5

    # Not considered for protocol matching.
    IGNORED_FOR_PROTOCOL_MATCH = 1 << 6

    # Symbol is a ClassVar.
    CLASS_VAR = 1 << 7

    # Symbol is included in __all__.
    IN_DUNDER_ALL = 1 << 8

    # Private import in a py.typed module.
    PRIVATE_PY_TYPED_IMPORT = 1 << 9

    # InitVar (PEP 557).
    INIT_VAR = 1 << 10

    # NamedTuple field.
    NAMED_TUPLE_MEMBER = 1 << 11

    # Exempt from override checks.
    IGNORED_FOR_OVERRIDE_CHECKS = 1 << 12

    # Final variable defined in a class body.
    FINAL_VAR_IN_CLASS_BODY = 1 << 13


# ============================================================
# "Declaration" protocol — placeholder for now
# ============================================================

@runtime_checkable
class DeclarationLike(Protocol):
    """
    Very loose protocol representing a declaration.

    In real Pyright, this is a structured object with:
      - type (variable, function, class, etc.)
      - node reference
      - inferred type info
      - flags like isFinal, typeAliasName, etc.

    For our current project, you can start by storing simple strings
    or small dataclasses and tighten this up later.
    """

    def __repr__(self) -> str:  # pragma: no cover - protocol stub
        ...


# ============================================================
# Symbol — analogue of Pyright's Symbol class
# ============================================================

_next_symbol_id: int = 1


def _allocate_symbol_id() -> int:
    global _next_symbol_id
    sid = _next_symbol_id
    _next_symbol_id += 1
    return sid


@dataclass
class Symbol:
    """
    Represents an association between a name and some declarations.

    This is a *rough* analogue of Pyright's Symbol:

      - `name`:
          The identifier text (key in the symbol table).
      - `flags`:
          Bitflags describing the role (import, class member, etc.).
          We currently don't set these in Binder/Scope yet, but the
          field is here so we can grow into it.
      - `target`:
          For imports, the fully-qualified target, e.g. "pandas" or
          "pandas.read_csv". For non-imports, you can leave this None.
      - `declarations`:
          One or more “declarations” where this symbol appears. Right
          now you can store simple strings, AST nodes, or tiny
          dataclasses – whatever is convenient.
    """

    name: str
    flags: SymbolFlags = SymbolFlags.NONE
    target: Optional[str] = None
    declarations: List[DeclarationLike | Any] = field(default_factory=list)

    # Internal unique ID, similar in spirit to Pyright's symbol ID.
    _id: int = field(default_factory=_allocate_symbol_id, init=False, repr=False)

    # --------------------------------------------------------
    # Basic helpers
    # --------------------------------------------------------

    def add_declaration(self, decl: DeclarationLike | Any) -> None:
        """
        Register one more declaration site for this symbol.

        In a future, more Pyright-like system you'd dedupe declarations
        and treat typed declarations differently. For now, we just append.
        """
        self.declarations.append(decl)

    # Convenience helpers that mirror common Pyright queries.
    # These aren't heavily used yet, but having the shape matches
    # symbol.ts and will help when you add more analysis.

    def is_externally_hidden(self) -> bool:
        return bool(self.flags & SymbolFlags.EXTERNALLY_HIDDEN)

    def is_class_member(self) -> bool:
        return bool(self.flags & SymbolFlags.CLASS_MEMBER)

    def is_instance_member(self) -> bool:
        return bool(self.flags & SymbolFlags.INSTANCE_MEMBER)

    def is_class_var(self) -> bool:
        return bool(self.flags & SymbolFlags.CLASS_VAR)

    def is_final_var_in_class_body(self) -> bool:
        return bool(self.flags & SymbolFlags.FINAL_VAR_IN_CLASS_BODY)

    def __str__(self) -> str:
        return f"Symbol(name={self.name!r}, flags={int(self.flags)}, target={self.target!r})"


# ============================================================
# SymbolTable — map from name to Symbol (Pyright-style)
# ============================================================

SymbolTable = Dict[str, Symbol]
