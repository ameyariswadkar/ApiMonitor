from pathlib import Path
import json
import logging
import logging.config
from datetime import datetime, timezone

from scanner.common.config import load_scanner_settings
from scanner.common.kafka_producer import ApiCallProducer
from scanner.parser.parser import Parser

from scanner.analyzer.program import Program, ProgramConfig
from scanner.analyzer.binder import Binder, BinderConfig
from scanner.analyzer.scope import Scope, ScopeType


logger = logging.getLogger(__name__)


def main():
    # ------------------------------
    # logging config (unchanged)
    # ------------------------------
    from scanner.common.config import CONFIG_DIR
    log_cfg = CONFIG_DIR / "logging_config.json"
    if log_cfg.exists():
        logging.config.dictConfig(json.loads(log_cfg.read_text()))
    else:
        logging.basicConfig(level=logging.INFO)

    # ------------------------------
    # load settings & producer
    # ------------------------------
    settings = load_scanner_settings()
    producer = ApiCallProducer(settings.kafka)

    # ------------------------------
    # set up parser + binder + builtins
    # ------------------------------
    # NEW Parser: no settings / no Kafka; it only builds ModuleNode + imports.
    parser = Parser()

    # Minimal builtin scope for Binder
    builtin_scope = Scope(scope_type=ScopeType.BUILTIN, parent=None)
    binder = Binder(BinderConfig(builtin_scope=builtin_scope))

    # ------------------------------
    # per-repo analysis
    # ------------------------------
    for repo_path_str in settings.repos:
        repo_root = Path(repo_path_str).resolve()
        repo_name = repo_root.name

        logger.info("Analyzing repo %s at %s", repo_name, repo_root)

        # ProgramConfig: project_root drives "local vs external" classification.
        program_config = ProgramConfig(
            project_root=repo_root,
            # kept for compatibility; Program no longer uses this to filter calls
            target_libraries=getattr(settings, "target_libraries", []),
        )

        program = Program(
            config=program_config,
            parser=parser,
            binder=binder,
        )

        # Track all Python files in this repo
        py_files = list(repo_root.rglob("*.py"))
        if not py_files:
            logger.info("No Python files found in repo %s", repo_name)
            continue

        program.set_tracked_files(py_files, repo_name)
        program.analyze()

        # --------------------------
        # collect ALL external API calls
        # --------------------------
        api_calls = program.collect_api_calls(external_only=True)

        logger.info(
            "Repo %s: found %d external API calls across %d files",
            repo_name,
            len(api_calls),
            program.get_file_count(),
        )

        # --------------------------
        # emit Kafka events HERE
        # --------------------------
        now_iso = datetime.now(timezone.utc).isoformat()

        for call in api_calls:
            # compute display path relative to repo root
            try:
                display_file = call.file.relative_to(program.project_root)
                display_file = f"{call.repo}/{display_file}"
            except ValueError:
                # if for some reason it's not under project_root, fall back to absolute
                display_file = str(call.file)

            event = {
                "repo": call.repo,
                "file": display_file,   # <− now friendly path
                "symbol_called": call.symbol_called,
                "library": call.library,
                "signature_shape": call.signature_shape,
                "is_external": call.is_external,
                "defining_file": (
                    # also shorten defining_file if local
                    str(call.defining_file.relative_to(program.project_root))
                    if call.defining_file and call.defining_file.is_absolute()
                    else call.defining_file
                ),
                "import_module": call.import_module,
                "commit_hash": None,
                "timestamp": now_iso,
            }
            producer.send_api_call(event)


    # Ensure all messages are flushed before exit
    producer.flush()


if __name__ == "__main__":
    main()
