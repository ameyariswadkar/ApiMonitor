"""
db_loader/connection.py

Central place to construct a SQLAlchemy engine for the Postgres database
used by the API usage monitor.

Reads connection settings from configs/db.json, e.g.:

{
  "postgres": {
    "host": "localhost",
    "port": 5432,
    "database": "codeintel",
    "user": "myuser",
    "password": "mypassword"
  }
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


# Adjust this if your repo layout changes:
# api-usage-monitor/
#   configs/
#     db.json
#   db_loader/
#     connection.py  <-- here
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "configs"
DB_CONFIG_PATH = CONFIG_DIR / "db.json"


def _load_db_config() -> Dict[str, Any]:
    """
    Load the Postgres config block from configs/db.json.

    Raises FileNotFoundError or KeyError if things are missing, which is OK:
    you'll see a clear error on startup.
    """
    if not DB_CONFIG_PATH.exists():
        raise FileNotFoundError(f"DB config file not found: {DB_CONFIG_PATH}")

    with DB_CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if "postgres" not in data:
        raise KeyError("Expected 'postgres' key in db.json")

    return data["postgres"]


def build_connection_url(cfg: Dict[str, Any]) -> str:
    """
    Build a SQLAlchemy Postgres URL from a config dict.

    Expected keys:
      - host
      - port
      - database
      - user
      - password
    """
    host = cfg.get("host", "localhost")
    port = cfg.get("port", 5432)
    database = cfg["database"]
    user = cfg["user"]
    password = cfg["password"]

    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


_engine: Engine | None = None


def get_engine() -> Engine:
    """
    Return a singleton SQLAlchemy Engine.

    Usage:
      from db_loader.connection import get_engine
      engine = get_engine()
      df.to_sql("api_usage", engine, if_exists="append", index=False)
    """
    global _engine
    if _engine is None:
        cfg = _load_db_config()
        url = build_connection_url(cfg)
        _engine = create_engine(url)
    return _engine
