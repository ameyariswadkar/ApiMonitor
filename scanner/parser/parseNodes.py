# parser/parseNodes.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional


class ParseNodeType:
    MODULE = "Module"
    CALL = "Call"
    IMPORT = "Import"
    IMPORT_FROM = "ImportFrom"


@dataclass
class ParseNodeBase:
    node_type: str
    start: int
    end: int
    parent: Optional["ParseNodeBase"] = None


@dataclass
class CallNode(ParseNodeBase):
    """
    Represents a function or method call like pd.read_csv(...).
    """
    # Fully-qualified symbol, once resolved with aliases, e.g. "pandas.read_csv".
    fq_name: str = ""
    # Raw text name (e.g. 'read_csv', 'get', etc.)
    name: str = ""
    # Module / object base, e.g. 'pd' or 'pandas' or 'requests'
    base: Optional[str] = None
    # Simple call-shape info (for Spark)
    positional_count: int = 0
    keyword_args: List[str] = field(default_factory=list)


@dataclass
class ImportAlias:
    """
    One alias in an import statement.

    Examples:
      - 'import pandas as pd'  =>  module='pandas', alias='pd'
      - 'from pandas import read_csv as rc' => module='pandas', name='read_csv', alias='rc'
    """
    module: str
    name: Optional[str] = None
    alias: Optional[str] = None


@dataclass
class ImportNode(ParseNodeBase):
    """
    Represents `import x [as y]` or `from x import y`.
    """
    module: str = ""
    aliases: List[ImportAlias] = field(default_factory=list)
    is_from_import: bool = False


@dataclass
class ModuleNode(ParseNodeBase):
    """
    Root node for a file. Holds call nodes and import nodes we care about.
    """
    file_path: str = ""
    repo: str = ""
    calls: List[CallNode] = field(default_factory=list)
    imports: List[ImportNode] = field(default_factory=list)
