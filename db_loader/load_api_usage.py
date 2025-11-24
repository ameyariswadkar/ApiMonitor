"""
db_loader/load_api_usage.py

Load aggregated API usage data (written by spark_jobs/api_usage_aggregator.py)
from Parquet files into a Postgres table using SQLAlchemy + pandas.

Expected Parquet schema (from api_usage_aggregator.py):

    repo           : string
    symbol_called  : string   e.g. "pandas.read_csv"
    time_window    : timestamp (month-bucket)
    usage_count    : long

Target SQL table (recommended schema):

    CREATE TABLE IF NOT EXISTS api_usage (
        id              SERIAL PRIMARY KEY,
        repo            TEXT NOT NULL,
        symbol_called   TEXT NOT NULL,
        time_window     TIMESTAMP NOT NULL,
        usage_count     BIGINT NOT NULL,
        last_updated    TIMESTAMP NOT NULL DEFAULT NOW()
    );

You can either pre-create this table, or let pandas.to_sql create it automatically
(without indexes) the first time.

Run (example):

    python -m db_loader.load_api_usage \
        --parquet-path results/aggregated_usage \
        --table api_usage \
        --if-exists append
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from db_loader.connection import get_engine


logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(h)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load aggregated API usage Parquet files into a Postgres table."
    )
    parser.add_argument(
        "--parquet-path",
        default="results/aggregated_usage",
        help="Directory containing aggregated Parquet files (from api_usage_aggregator.py).",
    )
    parser.add_argument(
        "--table",
        default="api_usage",
        help="Target SQL table name. Default: api_usage",
    )
    parser.add_argument(
        "--if-exists",
        choices=["fail", "replace", "append"],
        default="append",
        help="Behavior if the table already exists. Default: append",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=5_000,
        help="Optional chunk size for batched to_sql writes. Default: 5000",
    )
    return parser.parse_args()


def load_parquet_to_db(
    parquet_path: str | Path,
    table: str,
    if_exists: str = "append",
    chunksize: int | None = 5_000,
) -> None:
    """
    Read all Parquet files under parquet_path into a pandas DataFrame,
    then write them into the given SQL table.
    """
    root = Path(parquet_path)
    if not root.exists():
        raise FileNotFoundError(f"Parquet path does not exist: {root}")

    # Collect all .parquet files; if api_usage_aggregator wrote multiple parts,
    # this will pick up all of them.
    parquet_files = sorted(root.rglob("*.parquet"))
    if not parquet_files:
        logger.info("No Parquet files found under %s, nothing to load.", root)
        return

    logger.info("Found %d Parquet file(s) under %s", len(parquet_files), root)

    # Concatenate all Parquet parts into a single DataFrame.
    dfs = []
    for p in parquet_files:
        logger.info("Reading %s", p)
        df_part = pd.read_parquet(p)
        dfs.append(df_part)

    df = pd.concat(dfs, ignore_index=True)
    logger.info("Total rows to load: %d", len(df))

    # Optional: ensure column names match SQL schema expectations
    expected_cols = {"repo", "symbol_called", "time_window", "usage_count"}
    missing = expected_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing expected columns in Parquet data: {missing}")

    # Get SQLAlchemy engine and write to DB
    engine = get_engine()
    logger.info(
        "Writing to table '%s' with if_exists='%s', chunksize=%s",
        table,
        if_exists,
        chunksize,
    )

    df.to_sql(
        table,
        engine,
        if_exists=if_exists,
        index=False,
        chunksize=chunksize,
    )

    logger.info("Successfully loaded %d rows into '%s'.", len(df), table)


def main() -> None:
    args = parse_args()
    load_parquet_to_db(
        parquet_path=args.parquet_path,
        table=args.table,
        if_exists=args.if_exists,
        chunksize=args.chunksize,
    )


if __name__ == "__main__":
    main()
