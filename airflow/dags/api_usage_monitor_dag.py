""" 
api_usage_monitor_dag.py

Airflow DAG that orchestrates the full pipeline:

  1. scan_repos
     - Runs the Python scanner to walk repos, parse AST, and emit API-call
       events to Kafka topic `ast.api_calls`.

  2. spark_api_usage
     - Runs the Spark job api_usage_aggregator.py to consume Kafka events and
       write monthly aggregated usage stats to Parquet under results/aggregated_usage/.

  3. spark_deprecation_exposure
     - Runs the Spark job deprecation_exposure.py to join aggregated usage
       with deprecated_apis.json and write:
           - results/deprecated_usage/
           - results/exposure_scores/

  4. load_api_usage_to_db
     - Uses db_loader/load_api_usage.py to load aggregated Parquet into the
       Postgres table `api_usage`.

  5. load_exposure_scores_to_db
     - Uses db_loader/load_exposure_scores.py to load exposure scores Parquet
       into the Postgres table `repo_exposure`.

NOTE: Adjust PROJECT_DIR, Python binary, and spark-submit paths as needed for
your environment.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

import os
from airflow.datasets import Dataset

aggregated_dataset = Dataset("/opt/api-usage-monitor/results/aggregated_usage")
# ---------------------------------------------------------------------------
# Project / environment configuration
# ---------------------------------------------------------------------------

# Project root as seen from the Airflow worker container.
PROJECT_DIR = os.environ.get("PROJECT_DIR", "/opt/api-usage-monitor")

# Kafka broker address on the Docker network. Must match scanner_settings.json
# and docker-compose (service name "kafka" on port 9092).
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "kafka:9092")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "ast.api_calls")

PYTHON_BIN = os.environ.get("PYTHON_BIN", "python")

# Use spark-submit with Kafka connector packages so that
# spark.readStream.format("kafka") is available.
SPARK_SUBMIT = os.environ.get(
    "SPARK_SUBMIT",
    "spark-submit "
    "--packages "
    "org.apache.spark:spark-sql-kafka-0-10_2.13:4.0.1"
)


AGGREGATED_USAGE_PATH = os.environ.get(
    "AGGREGATED_USAGE_PATH",
    f"{PROJECT_DIR}/results/aggregated_usage",
)

AGGREGATED_CHECKPOINT = os.environ.get(
    "AGGREGATED_CHECKPOINT",
    f"{PROJECT_DIR}/results/checkpoints/api_usage",
)

DEPRECATED_USAGE_PATH = f"{PROJECT_DIR}/results/deprecated_usage"
EXPOSURE_SCORES_PATH = f"{PROJECT_DIR}/results/exposure_scores"
DEPRECATED_APIS_JSON = f"{PROJECT_DIR}/configs/deprecated_apis.json"

# ---------------------------------------------------------------------------
# Default DAG configuration
# ---------------------------------------------------------------------------

default_args = {
    "owner": "api-usage-monitor",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 0,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="api_usage_monitor_dag",
    description="End-to-end API usage and deprecation exposure monitor",
    default_args=default_args,
    start_date=datetime(2025, 11, 20),
    schedule_interval="@daily",
    catchup=False,
    tags=["api-usage", "kafka", "spark"],
) as dag:

    # -----------------------------------------------------------------------
    # 1) scan_repos: run the AST scanner against the repo(s)
    # -----------------------------------------------------------------------

    scan_repos = BashOperator(
        task_id="scan_repos",
        bash_command=(
            f"cd {PROJECT_DIR} && "
            f"{PYTHON_BIN} -m scanner.main "
            f"--settings configs/scanner_settings.json"
        ),
    )

    # -----------------------------------------------------------------------
    # 2) spark_api_usage: consume Kafka events and write aggregated usage
    # -----------------------------------------------------------------------

    spark_api_usage = BashOperator(
        task_id="spark_api_usage",
        bash_command=(
            f"cd {PROJECT_DIR} && "
            f"{SPARK_SUBMIT} "
            f" spark_jobs/api_usage_aggregator.py"
            f" --kafka-bootstrap-servers {KAFKA_BOOTSTRAP}"
            f" --kafka-topic {KAFKA_TOPIC}"
            f" --output-path {AGGREGATED_USAGE_PATH}"
            f" --checkpoint-path {AGGREGATED_CHECKPOINT}"
        ),
        outlets=[aggregated_dataset],
    )

    # -----------------------------------------------------------------------
    # 3) spark_deprecation_exposure: compute exposure scores from aggregated usage
    # -----------------------------------------------------------------------

    spark_deprecation_exposure = BashOperator(
        task_id="spark_deprecation_exposure",
        bash_command=(
            f"cd {PROJECT_DIR} && "
            f"{SPARK_SUBMIT} "
            f" spark_jobs/deprecation_exposure.py"
            f" --aggregated-path {AGGREGATED_USAGE_PATH}"
            f" --deprecated-apis-path {DEPRECATED_APIS_JSON}"
            f" --deprecated-usage-output {DEPRECATED_USAGE_PATH}"
            f" --repo-exposure-output {EXPOSURE_SCORES_PATH}"
        ),
    )

    # -----------------------------------------------------------------------
    # 4) load_api_usage_to_db: load aggregated usage Parquet into Postgres
    # -----------------------------------------------------------------------

    load_api_usage_to_db = BashOperator(
        task_id="load_api_usage_to_db",
        bash_command=(
            f"cd {PROJECT_DIR} && "
            f"{PYTHON_BIN} db_loader/load_api_usage.py"
            f" --config configs/db.json"
            f" --parquet-path {AGGREGATED_USAGE_PATH}"
            f" --table api_usage"
            f" --if-exists append"
        ),
        inlets=[aggregated_dataset],
    )

    # -----------------------------------------------------------------------
    # 5) load_exposure_scores_to_db: load exposure scores into Postgres
    # -----------------------------------------------------------------------

    load_exposure_scores_to_db = BashOperator(
        task_id="load_exposure_scores_to_db",
        bash_command=(
            f"cd {PROJECT_DIR} && "
            f"{PYTHON_BIN} db_loader/load_exposure_scores.py"
            f" --config configs/db.json"
            f" --parquet-path {EXPOSURE_SCORES_PATH}"
            f" --table repo_exposure"
            f" --if-exists append"
        ),
    )

    # -----------------------------------------------------------------------
    # Dependencies: full pipeline ordering
    # -----------------------------------------------------------------------

    scan_repos >> spark_api_usage >> spark_deprecation_exposure >> [
        load_api_usage_to_db,
        load_exposure_scores_to_db,
    ]
