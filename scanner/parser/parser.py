# NEW parser/parser.py

from __future__ import annotations
import ast
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, List

from scanner.parser.parseNodes import (
    ModuleNode,
    ImportNode,
    ImportAlias,
    ParseNodeType,
)

class _ModuleOnlyVisitor(ast.NodeVisitor):
    """
    This parser NO LONGER filters by `target_libraries`.
    It NO LONGER emits calls.
    It ONLY collects imports into ModuleNode.
    """
    def __init__(self, module: ModuleNode):
        self.module = module

    def visit_Import(self, node: ast.Import) -> None:
        imp = ImportNode(
            node_type=ParseNodeType.IMPORT,
            start=node.lineno,
            end=node.end_lineno or node.lineno,
            module="",
            aliases=[],
            is_from_import=False,
        )
        for alias in node.names:
            mod = alias.name
            asname = alias.asname or alias.name.split(".")[0]
            imp.aliases.append(ImportAlias(module=mod, name=None, alias=asname))
        self.module.imports.append(imp)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None:
            return
        imp = ImportNode(
            node_type=ParseNodeType.IMPORT_FROM,
            start=node.lineno,
            end=node.end_lineno or node.lineno,
            module=node.module,
            aliases=[],
            is_from_import=True,
        )
        for alias in node.names:
            name   = alias.name
            asname = alias.asname or name
            imp.aliases.append(ImportAlias(module=node.module, name=name, alias=asname))
        self.module.imports.append(imp)


class Parser:
    """
    NEW Parser:
    - NO Kafka
    - NO target_libraries
    - ONLY produces ModuleNode
    """

    def __init__(self):
        pass

    def parse_file(self, file_path: Path, repo_name: str) -> ModuleNode:
        source = file_path.read_text(encoding="utf-8")
        tree   = ast.parse(source, filename=str(file_path))

        module = ModuleNode(
            node_type=ParseNodeType.MODULE,
            start=1,
            end=source.count("\n") + 1,
            file_path=str(file_path),
            repo=repo_name,
        )

        visitor = _ModuleOnlyVisitor(module)
        visitor.visit(tree)

        # RETURN ONLY THE MODULE. NO EVENTS, NO FILTERING.
        return module
