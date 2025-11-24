import logging
import os

from pyspark.sql import SparkSession, DataFrame, functions as F, types as T


# ---------------------------------------------------------------------------
# Logging utilities
# ---------------------------------------------------------------------------

def get_logger(name: str = "api_usage_spark") -> logging.Logger:
    """
    Create or reuse a simple stdout logger.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Schema: how we expect Kafka JSON payloads to look
# ---------------------------------------------------------------------------

API_EVENT_SCHEMA = T.StructType(
    [
        # Adjust these names if your producer uses slightly different fields.
        T.StructField("repo_name", T.StringType(), True),
        T.StructField("file_path", T.StringType(), True),
        T.StructField("library", T.StringType(), True),
        T.StructField("symbol", T.StringType(), True),
        T.StructField("call_full_name", T.StringType(), True),
        # Event time of the API call; we will watermark / aggregate on this.
        T.StructField("event_time", T.TimestampType(), True),
    ]
)


# ---------------------------------------------------------------------------
# Small helper utilities
# ---------------------------------------------------------------------------

def ensure_dir(path: str) -> None:
    """
    Ensure a directory exists (mkdir -p).
    """
    if not path:
        return
    os.makedirs(path, exist_ok=True)
    logger.info("[INFO] Ensured directory: %s", path)


def build_spark(app_name: str) -> SparkSession:
    """
    Create a SparkSession with a few sensible defaults.
    """
    spark = (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )
    # Reduce Spark log noise a bit
    spark.sparkContext.setLogLevel("WARN")
    return spark


def add_month_bucket(df: DataFrame, time_col: str = "event_time") -> DataFrame:
    """
    Add a month bucket column computed from a timestamp column.
    """
    return df.withColumn("month_bucket", F.date_trunc("month", F.col(time_col)))


# ---------------------------------------------------------------------------
# Kafka value parsing
# ---------------------------------------------------------------------------

def parse_kafka_value(df: DataFrame) -> DataFrame:
    """
    Parse the Kafka 'value' (binary) column into structured columns using
    API_EVENT_SCHEMA.

    Assumes each Kafka message is a JSON object whose keys match the fields
    in API_EVENT_SCHEMA (repo_name, file_path, library, symbol, call_full_name,
    event_time).
    """
    json_col = F.col("value").cast("string")
    parsed = df.select(
        F.from_json(json_col, API_EVENT_SCHEMA).alias("event")
    ).select("event.*")

    return parsed
