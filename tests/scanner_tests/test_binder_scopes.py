# tests/test_binder_scopes.py

import tempfile
import textwrap
import unittest
from pathlib import Path

from scanner.parser.parser import Parser
from scanner.analyzer.program import Program, ProgramConfig
from scanner.analyzer.scope import ScopeType
from scanner.analyzer.SourceFile import SourceFile

from scanner.analyzer.binder import Binder, BinderConfig
from scanner.analyzer.scope import Scope


class DummySettings:
    def __init__(self, target_libraries):
        self.target_libraries = target_libraries


class DummyProducer:
    def send_api_call(self, event):
        # No-op for binder tests
        pass

    def flush(self):
        pass


class TestBinderScopes(unittest.TestCase):
    def _write_temp_py(self, code: str) -> Path:
        tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
        tmp_path = Path(tmp.name)
        tmp.write(textwrap.dedent(code).encode("utf-8"))
        tmp.close()
        return tmp_path

    def test_module_scope_import_symbols(self):
        code = """
        import pandas as pd
        from numpy import array as nparray
        """

        file_path = self._write_temp_py(code)
        repo_name = "test_repo"

        # Parser to populate ModuleNode
        settings = DummySettings(target_libraries=["pandas", "numpy"])
        producer = DummyProducer()
        parser = Parser(settings=settings, producer=producer)

        source_file = SourceFile(path=file_path, repo=repo_name)
        source_file.parse(parser)

        # Builtin scope + binder
        builtin_scope = Scope(scope_type=ScopeType.BUILTIN, parent=None)
        binder = Binder(BinderConfig(builtin_scope=builtin_scope))

        module_scope = binder.bind_source_file(source_file)

        # Basic assertions
        self.assertEqual(module_scope.scope_type, ScopeType.MODULE)
        self.assertIs(module_scope.parent, builtin_scope)

        # Symbols should exist for 'pd' and 'nparray'
        pd_symbol = module_scope.get_symbol("pd")
        self.assertIsNotNone(pd_symbol)
        self.assertEqual(pd_symbol.kind, "import")
        self.assertEqual(pd_symbol.target, "pandas")

        np_symbol = module_scope.get_symbol("nparray")
        self.assertIsNotNone(np_symbol)
        self.assertEqual(np_symbol.kind, "import")
        self.assertEqual(np_symbol.target, "numpy.array")

    def test_binder_handles_no_module(self):
        """
        If parsing fails (module is None), binder should still give
        us a module scope.
        """
        # We can simulate this by constructing a SourceFile with module=None directly.
        file_path = Path("fake.py")
        source_file = SourceFile(path=file_path, repo="test_repo")
        source_file.module = None

        builtin_scope = Scope(scope_type=ScopeType.BUILTIN, parent=None)
        binder = Binder(BinderConfig(builtin_scope=builtin_scope))

        scope = binder.bind_source_file(source_file)
        self.assertEqual(scope.scope_type, ScopeType.MODULE)
        self.assertIs(source_file.scope, scope)


if __name__ == "__main__":
    unittest.main()
