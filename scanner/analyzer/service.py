# analyzer/service.py

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from scanner.analyzer.program import Program, ProgramConfig
from scanner.analyzer.SourceFile import Diagnostic
from scanner.common.config import load_scanner_settings  # reuse your scanner config
from scanner.common.kafka_producer import ApiCallProducer
from scanner.parser.parser import Parser

from typing import Dict
from scanner.analyzer.scope import Scope


@dataclass
class AnalysisResults:
    diagnostics: List[Diagnostic]
    files_in_program: int


class AnalyzerService:
    """
    Minimal analogue of pyright's AnalyzerService.

    Responsibilities:
      - construct Program + Parser
      - wire Kafka producer into Parser
      - expose a simple `run_analysis` API
    """

    def __init__(self, project_root: Path) -> None:
        # Reuse scanner settings for target libraries and Kafka config.
        settings = load_scanner_settings()
        producer = ApiCallProducer(settings.kafka)

        parser = Parser(settings=settings, producer=producer)
        config = ProgramConfig(project_root=project_root, target_libraries=settings.target_libraries)

        self._program = Program(config=config, parser=parser)

    # --------------------------------------------------------------- #
    # public API                                                      #
    # --------------------------------------------------------------- #

    def set_tracked_files(self, files: Iterable[Path], repo_name: str) -> None:
        self._program.set_tracked_files(files, repo_name)

    def run_analysis(self) -> AnalysisResults:
        """
        Parse all tracked files. The Parser will send Kafka events for
        each API call. We return basic diagnostic info.
        """
        self._program.analyze()
        diags = self._program.get_diagnostics()
        return AnalysisResults(
            diagnostics=diags,
            files_in_program=self._program.get_file_count(),
        )
    
    def get_scope_snapshot(self) -> Dict[str, dict]:
        """
        Return a mapping file_path -> scope_dict for sampling.
        """
        return self._program.dump_scopes()
