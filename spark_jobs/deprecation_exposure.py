#!/usr/bin/env python

"""
deprecation_exposure.py

PySpark job that:

  - Reads API usage aggregates (from api_usage_aggregator.py) as Parquet.
  - Reads a JSON config of deprecated APIs.
  - Joins usage with deprecation metadata.
  - Computes an exposure_score per (repo, symbol, time_window).
  - Aggregates to a per-repo exposure table.

Expected aggregated usage schema (Parquet):

  - repo           : string
  - symbol_called  : string   e.g. "pandas.read_csv"
  - time_window    : timestamp (month-bucket)
  - usage_count    : long

Expected deprecated_apis.json format (array of objects), e.g.:

[
  {
    "symbol": "pandas.read_table",
    "library": "pandas",
    "deprecated_in": "1.4",
    "removed_in": "2.0",
    "severity": 3,
    "replacement": "pandas.read_csv"
  },
  {
    "symbol": "numpy.matrix",
    "library": "numpy",
    "deprecated_in": "1.15",
    "removed_in": "2.0",
    "severity": 4,
    "replacement": "numpy.array"
  }
]

Run (example):

  spark-submit \
    --master local[4] \
    spark_jobs/deprecation_exposure.py \
      --aggregated-path results/aggregated_usage \
      --deprecated-apis-path configs/deprecated_apis.json \
      --deprecated-usage-output results/deprecated_usage \
      --repo-exposure-output results/exposure_scores
"""

from __future__ import annotations

import argparse

from pyspark.sql import functions as F

from spark_jobs.common import (
    build_spark,
    deprecated_api_schema,
    ensure_dir,
    logger,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Join API usage with deprecated API metadata and compute exposure scores."
    )
    parser.add_argument(
        "--aggregated-path",
        default="results/aggregated_usage",
        help="Input path for aggregated API usage Parquet (from api_usage_aggregator.py).",
    )
    parser.add_argument(
        "--deprecated-apis-path",
        default="configs/deprecated_apis.json",
        help="Path to deprecated_apis.json (array of objects).",
    )
    parser.add_argument(
        "--deprecated-usage-output",
        default="results/deprecated_usage",
        help="Output path for per-API deprecated usage Parquet.",
    )
    parser.add_argument(
        "--repo-exposure-output",
        default="results/exposure_scores",
        help="Output path for per-repo exposure scores Parquet.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Ensure output dirs exist (top-level)
    ensure_dir(args.deprecated_usage_output)
    ensure_dir(args.repo_exposure_output)

    spark = build_spark(app_name="DeprecationExposure")

    # ------------------------------------------------------------------ #
    # 1) Read aggregated usage                                           #
    # ------------------------------------------------------------------ #
    logger.info("Reading aggregated usage from %s", args.aggregated_path)
    usage_df = spark.read.parquet(args.aggregated_path)

    # ------------------------------------------------------------------ #
    # 2) Read deprecated APIs metadata                                   #
    # ------------------------------------------------------------------ #
    logger.info("Reading deprecated API metadata from %s", args.deprecated_apis_path)
    deprecated_schema = deprecated_api_schema()
    deprecated_df = spark.read.schema(deprecated_schema).json(args.deprecated_apis_path)

    # Default severity to 1 if missing
    deprecated_df = deprecated_df.withColumn(
        "severity", F.coalesce(F.col("severity"), F.lit(1))
    )

    # ------------------------------------------------------------------ #
    # 3) Join usage with deprecated APIs                                 #
    # ------------------------------------------------------------------ #
    # symbol_called (from usage) should match 'symbol' in deprecated list.
    joined_df = usage_df.join(
        deprecated_df,
        usage_df.symbol_called == deprecated_df.symbol,
        how="inner",
    )

    # Compute exposure_score = usage_count * severity
    joined_df = joined_df.withColumn(
        "exposure_score", F.col("usage_count") * F.col("severity")
    )

    # ------------------------------------------------------------------ #
    # 4) Per-API deprecated usage table                                  #
    # ------------------------------------------------------------------ #
    deprecated_usage_df = joined_df.select(
        usage_df.repo.alias("repo"),
        usage_df.symbol_called.alias("symbol_called"),
        usage_df.time_window.alias("time_window"),
        usage_df.usage_count.alias("usage_count"),
        deprecated_df.deprecated_in.alias("deprecated_in"),
        deprecated_df.removed_in.alias("removed_in"),
        deprecated_df.severity.alias("severity"),
        deprecated_df.replacement.alias("replacement"),
        joined_df.exposure_score.alias("exposure_score"),
    )

    logger.info("Writing per-API deprecated usage to %s", args.deprecated_usage_output)
    deprecated_usage_df.write.mode("overwrite").parquet(args.deprecated_usage_output)

    # ------------------------------------------------------------------ #
    # 5) Per-repo exposure scores                                        #
    # ------------------------------------------------------------------ #
    repo_exposure_df = (
        deprecated_usage_df.groupBy("repo", "time_window")
        .agg(
            F.sum("exposure_score").alias("exposure_score"),
            F.sum("usage_count").alias("total_deprecated_calls"),
            F.countDistinct("symbol_called").alias("unique_deprecated_apis"),
        )
        .select(
            "repo",
            "time_window",
            "exposure_score",
            "total_deprecated_calls",
            "unique_deprecated_apis",
        )
    )

    logger.info("Writing repo exposure scores to %s", args.repo_exposure_output)
    repo_exposure_df.write.mode("overwrite").parquet(args.repo_exposure_output)

    spark.stop()
    logger.info("DeprecationExposure job finished.")


if __name__ == "__main__":
    main()
