#!/usr/bin/env python

"""
api_usage_aggregator.py

PySpark job that:
  - Consumes AST API-call events from a Kafka topic.
  - Parses JSON payloads from Kafka 'value'.
  - Aggregates usage by (library, symbol, day).
  - Writes results as Parquet to results/aggregated_usage/.
"""

import argparse

from pyspark.sql import functions as F

from common import (
    build_spark,
    ensure_dir,
    add_month_bucket,
    parse_kafka_value,
    get_logger,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Streaming pipeline pieces
# ---------------------------------------------------------------------------

def build_source_stream(spark, kafka_bootstrap_servers, kafka_topic):
    """
    Build the raw Kafka streaming DataFrame.
    """
    return (
        spark.readStream
        .format("kafka")
        .option("kafka.bootstrap.servers", kafka_bootstrap_servers)
        .option("subscribe", kafka_topic)
        .option("startingOffsets", "earliest")
        .load()
    )


def build_aggregated_usage_stream(
    spark,
    kafka_bootstrap_servers,
    kafka_topic,
    output_path,
    checkpoint_path,
):
    """
    End-to-end streaming pipeline:

      Kafka -> parse JSON -> add time buckets -> aggregate -> write to Parquet.
    """
    logger.info("Building source stream from topic=%s", kafka_topic)

    raw = build_source_stream(
        spark,
        kafka_bootstrap_servers=kafka_bootstrap_servers,
        kafka_topic=kafka_topic,
    )

    # Parse Kafka 'value' JSON into structured columns.
    events = parse_kafka_value(raw)

    # Make sure we have a proper timestamp column to use for watermarking.
    events = events.withColumn(
        "event_time_ts", F.col("event_time").cast("timestamp")
    )

    # Optional: add a month bucket (not strictly required, but handy for partitioning).
    events = add_month_bucket(events, time_col="event_time_ts")

    # IMPORTANT: Watermark so that 'append' output mode is allowed for aggregation.
    events = events.withWatermark("event_time_ts", "1 hour")

    # Aggregate by day / library / symbol.
    agg = (
        events.groupBy(
            F.window("event_time_ts", "1 day").alias("event_day"),
            F.col("library"),
            F.col("symbol"),
        )
        .agg(F.count("*").alias("num_calls"))
    )

    # Flatten the window struct into start/end columns to make Parquet nicer.
    result = (
        agg
        .withColumn("event_day_start", F.col("event_day").start)
        .withColumn("event_day_end", F.col("event_day").end)
        .drop("event_day")
    )

    # Ensure output + checkpoint directories exist.
    ensure_dir(output_path)
    ensure_dir(checkpoint_path)

    logger.info(
        "Writing streaming aggregation to %s (checkpoint: %s)",
        output_path,
        checkpoint_path,
    )

    query = (
        result.writeStream
        .outputMode("append")         # allowed because we set a watermark
        .format("parquet")
        .option("path", output_path)
        .option("checkpointLocation", checkpoint_path)
        .partitionBy("library")       # optional; groups files by library
        .trigger(once=True)  
    )

    return query.start()


# ---------------------------------------------------------------------------
# CLI + main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Aggregate API usage from Kafka with Spark Structured Streaming.")

    parser.add_argument(
        "--kafka-bootstrap-servers",
        required=True,
        help="Kafka bootstrap servers, e.g. 'kafka:9092'.",
    )
    parser.add_argument(
        "--kafka-topic",
        required=True,
        help="Kafka topic to consume from, e.g. 'ast.api_calls'.",
    )
    parser.add_argument(
        "--output-path",
        required=True,
        help="Filesystem path to write aggregated Parquet files to.",
    )
    parser.add_argument(
        "--checkpoint-path",
        required=True,
        help="Filesystem path for Spark streaming checkpoints.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    logger.info("Starting ApiUsageAggregator job")

    spark = build_spark("ApiUsageAggregator")

    query = build_aggregated_usage_stream(
        spark,
        kafka_bootstrap_servers=args.kafka_bootstrap_servers,
        kafka_topic=args.kafka_topic,
        output_path=args.output_path,
        checkpoint_path=args.checkpoint_path,
    )

    query.awaitTermination()
    # We normally won't reach here in a long-running streaming job,
    # but it's safe to keep.
    spark.stop()
    logger.info("ApiUsageAggregator job finished.")


if __name__ == "__main__":
    main()
