import json
import logging
from typing import Any, Dict, Optional

from kafka import KafkaProducer  # from the kafka-python package

from .config import KafkaSettings

logger = logging.getLogger(__name__)


class ApiCallProducer:
    """
    Thin wrapper around KafkaProducer for sending AST api_call events as JSON.
    """

    def __init__(self, settings: KafkaSettings):
        self._enabled = settings.enabled
        self._topic = settings.topic

        if not self._enabled:
            logger.info("Kafka disabled in config; will log events instead of sending.")
            self._producer = None
            return

        self._producer = KafkaProducer(
            bootstrap_servers=settings.bootstrap_servers,
            client_id=settings.client_id,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        logger.info(
            "Initialized KafkaProducer for topic %s at %s",
            settings.topic,
            settings.bootstrap_servers,
        )

    def send_api_call(self, event: Dict[str, Any]) -> None:
        if not self._enabled or self._producer is None:
            logger.info("api_call event (Kafka disabled): %s", event)
            return

        future = self._producer.send(self._topic, value=event)
        future.add_errback(self._on_send_error)

    def flush(self) -> None:
        if self._producer is not None:
            self._producer.flush()

    @staticmethod
    def _on_send_error(excp: BaseException) -> None:
        logger.error("Error while sending Kafka message: %s", excp, exc_info=True)
