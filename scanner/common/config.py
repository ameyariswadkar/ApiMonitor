import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
import os



BASE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BASE_DIR.parent 
CONFIG_DIR = BASE_DIR / "configs"


@dataclass
class KafkaSettings:
    enabled: bool
    bootstrap_servers: str
    topic: str
    client_id: str


@dataclass
class ScannerSettings:
    repos: List[str]
    include_extensions: List[str]
    exclude_dirs: List[str]
    target_libraries: List[str]
    kafka: KafkaSettings


def load_json(name: str) -> dict:
    """
    Try to load a JSON config from a few sensible locations:

    1. scanner/configs/<name>         (old behavior)
    2. <PROJECT_ROOT>/configs/<name>  (if you ever add a top-level configs/)
    3. <PROJECT_ROOT>/<name>          (e.g. ApiMonitor/scanner_settings.json)
    4. SCANNER_CONFIG_DIR env var     (optional override)
    """
    candidates = []

    # 1) existing behavior
    candidates.append(CONFIG_DIR / name)

    # 2) top-level configs folder
    candidates.append(PROJECT_ROOT / "configs" / name)

    # 3) file sitting directly at project root
    candidates.append(PROJECT_ROOT / name)

    # 4) explicit override via env var
    env_dir = os.environ.get("SCANNER_CONFIG_DIR")
    if env_dir:
        candidates.append(Path(env_dir) / name)

    for path in candidates:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)

    tried = "\n  ".join(str(p) for p in candidates)
    raise FileNotFoundError(
        f"Could not find {name}. Tried:\n  {tried}"
    )



def load_scanner_settings() -> ScannerSettings:
    data = load_json("scanner_settings.json")
    kafka_data = data.get("kafka", {})
    kafka = KafkaSettings(
        enabled=kafka_data.get("enabled", False),
        bootstrap_servers=kafka_data.get("bootstrap_servers", "localhost:9092"),
        topic=kafka_data.get("topic", "ast.api_calls"),
        client_id=kafka_data.get("client_id", "api-usage-scanner"),
    )
    return ScannerSettings(
        repos=data.get("repos", []),
        include_extensions=data.get("include_extensions", [".py"]),
        exclude_dirs=data.get("exclude_dirs", []),
        target_libraries=data.get("target_libraries", []),
        kafka=kafka,
    )
