# analyzer/parseTreeWalker.py

from __future__ import annotations

from scanner.parser.parseNodes import ModuleNode


class ParseTreeWalker:
    """
    Extremely small analogue of Pyright's ParseTreeWalker.

    For now it's just a base class with a `walk_module` hook that
    subclasses can override to traverse ModuleNode trees.
    """

    def walk_module(self, node: ModuleNode) -> None:
        """
        Entry point for walking a module. Override in subclasses.

        In the future, you can implement a more complete visitor here
        if you want binder-style behaviour.
        """
        # placeholder; your project currently doesn't require it.
        return
