# API Usage Monitor

![CI](https://img.shields.io/badge/build-passing-brightgreen)
![Python](https://img.shields.io/badge/python-3.11-blue)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

------------------------------------------------------------------------

## Overview

**API Usage Monitor** is a distributed static analysis and data pipeline
system for tracking API usage and deprecation risk across large
codebases.

It combines: - A custom Python static analysis engine (inspired by
Pyright) - Kafka-based event streaming - Spark Structured Streaming for
aggregation - Airflow for orchestration - Postgres for persistence

------------------------------------------------------------------------

## Architecture

<pre> ```text

                    +----------------------------------+
                    |            Airflow               |
                    |   (DAG Orchestrator / Control)   |
                    +----------------+-----------------+
                                     |
        ----------------------------------------------------------------
        |              |                    |                   |
        v              v                    v                   v

+----------------+  +----------------+  +----------------+  +----------------+
|    Scanner     |  |     Kafka      |  |     Spark      |  |   DB Loaders   |
| (AST Analyzer) |  | (Event Stream) |  | (Aggregation)  |  |  (Postgres)    |
+--------+-------+  +--------+-------+  +--------+-------+  +--------+-------+
         |                   |                   |                   |
         v                   v                   v                   v
                 +----------------------------------------------+
                 |              Parquet Storage                 |
                 |      (aggregated_usage / exposure_scores)    |
                 +----------------------+-----------------------+
                                        |
                                        v
                                +---------------+
                                |   Postgres    |
                                +---------------+

  ``` </pre>
------------------------------------------------------------------------

## Features

-   Static analysis of Python codebases
-   API call extraction with symbol resolution
-   External vs internal dependency classification
-   Kafka event streaming
-   Time-based aggregation of API usage
-   Deprecation exposure scoring
-   End-to-end orchestration with Airflow

------------------------------------------------------------------------

## Getting Started

### Prerequisites

-   Docker & Docker Compose
-   Python 3.11 (optional for local dev)

------------------------------------------------------------------------

### Run with Docker

``` bash
git clone <your-repo>
cd ApiMonitor

docker-compose up --build
```

Airflow UI:

    http://localhost:8080

Default credentials:

    admin / admin

------------------------------------------------------------------------

## Pipeline

The Airflow DAG orchestrates:

1.  Scan repositories → emit API events
2.  Aggregate usage via Spark
3.  Compute deprecation exposure scores
4.  Load results into Postgres

------------------------------------------------------------------------

## Scanner

The scanner: - Parses Python AST - Builds symbol tables - Resolves
imports across files - Extracts API calls

------------------------------------------------------------------------

## Spark Jobs

### API Usage Aggregator

Aggregates API calls by: - library - symbol - time window

### Deprecation Exposure

Computes:

    exposure_score = usage_count * severity

------------------------------------------------------------------------

## Database Tables

### api_usage

-   repo
-   symbol_called
-   time_window
-   usage_count

### repo_exposure

-   repo
-   exposure_score
-   total_deprecated_calls
-   unique_deprecated_apis

------------------------------------------------------------------------

## Configuration

Scanner settings: - repos - kafka settings - include/exclude paths

------------------------------------------------------------------------

## Development

``` bash
pip install -r requirements.txt
python -m scanner.main
```

------------------------------------------------------------------------

## Project Structure

    scanner/        # static analysis engine
    spark_jobs/     # Spark pipelines
    db_loader/      # DB ingestion
    airflow/        # orchestration DAGs
    configs/        # configuration files

------------------------------------------------------------------------

## Roadmap

-   Real-time dashboards
-   CI/CD integration
-   Multi-language support
-   Visualization layer

