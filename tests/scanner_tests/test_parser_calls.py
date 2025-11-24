# tests/test_parser_calls.py

import tempfile
import textwrap
import unittest
from pathlib import Path

from scanner.parser.parser import Parser


class DummySettings:
    """Minimal settings object: only what Parser needs."""
    def __init__(self, target_libraries):
        self.target_libraries = target_libraries


class DummyProducer:
    """Capture events instead of sending them to Kafka."""
    def __init__(self):
        self.events = []

    def send_api_call(self, event):
        self.events.append(event)

    def flush(self):
        pass


class TestParserCalls(unittest.TestCase):
    def _write_temp_py(self, code: str) -> Path:
        tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
        tmp_path = Path(tmp.name)
        tmp.write(textwrap.dedent(code).encode("utf-8"))
        tmp.close()
        return tmp_path

    def test_simple_pandas_call(self):
        code = """
        import pandas as pd

        def load():
            df = pd.read_csv("file.csv")
            return df
        """

        file_path = self._write_temp_py(code)
        repo_name = "test_repo"

        settings = DummySettings(target_libraries=["pandas", "numpy", "requests"])
        producer = DummyProducer()
        parser = Parser(settings=settings, producer=producer)

        module = parser.parse_file(file_path, repo_name)

        # 1) Check that a CallNode was recorded on the ModuleNode
        self.assertEqual(len(module.calls), 1)
        call = module.calls[0]
        self.assertEqual(call.fq_name, "pandas.read_csv")
        self.assertEqual(call.positional_count, 1)
        self.assertEqual(call.keyword_args, [])

        # 2) Check that an event was emitted to the producer
        self.assertEqual(len(producer.events), 1)
        event = producer.events[0]
        self.assertEqual(event["repo"], repo_name)
        self.assertEqual(event["file"], str(file_path))
        self.assertEqual(event["symbol_called"], "pandas.read_csv")
        self.assertEqual(event["signature_shape"]["positional_count"], 1)
        self.assertEqual(event["signature_shape"]["keyword_args"], [])

    def test_from_import_alias_call(self):
        code = """
        from pandas import read_csv as rc

        def load2():
            df = rc("file.csv", sep=";")
            return df
        """

        file_path = self._write_temp_py(code)
        repo_name = "test_repo"

        settings = DummySettings(target_libraries=["pandas"])
        producer = DummyProducer()
        parser = Parser(settings=settings, producer=producer)

        module = parser.parse_file(file_path, repo_name)

        # Should resolve rc -> pandas.read_csv
        self.assertEqual(len(module.calls), 1)
        call = module.calls[0]
        self.assertEqual(call.fq_name, "pandas.read_csv")
        self.assertEqual(call.positional_count, 1)
        self.assertEqual(call.keyword_args, ["sep"])

        # Event check
        self.assertEqual(len(producer.events), 1)
        event = producer.events[0]
        self.assertEqual(event["symbol_called"], "pandas.read_csv")
        self.assertEqual(event["signature_shape"]["keyword_args"], ["sep"])


if __name__ == "__main__":
    unittest.main()
