from __future__ import annotations

import json
import logging
from collections.abc import Callable

from google.cloud import pubsub_v1

from .config import (
    PROJECT_ID,
    PUBSUB_ACK_DEADLINE,
    PUBSUB_SUBSCRIPTION,
    PUBSUB_TOPIC,
    WORKER_MAX_JOBS,
)

logger = logging.getLogger(__name__)
_publisher = pubsub_v1.PublisherClient()
_topic = _publisher.topic_path(PROJECT_ID, PUBSUB_TOPIC)


def publish_job(job_id: int) -> str:
    payload = json.dumps({"job_id": int(job_id)}, separators=(",", ":")).encode()
    future = _publisher.publish(_topic, payload, job_id=str(job_id))
    return future.result(timeout=30)


def decode_job_id(data: bytes) -> int:
    payload = json.loads(data.decode("utf-8"))
    job_id = int(payload["job_id"])
    if job_id <= 0:
        raise ValueError("job_id must be positive")
    return job_id


def subscribe(callback: Callable[[int], None]):
    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(PROJECT_ID, PUBSUB_SUBSCRIPTION)

    def on_message(message):
        try:
            job_id = decode_job_id(message.data)
            logger.info("Received Pub/Sub message id=%s job=%s", message.message_id, job_id)
            callback(job_id)
            message.ack()
            logger.info("Acked job=%s", job_id)
        except Exception:
            logger.exception("Job callback failed; message will be redelivered")
            message.nack()

    future = subscriber.subscribe(
        subscription_path,
        callback=on_message,
        flow_control=pubsub_v1.types.FlowControl(max_messages=max(1, WORKER_MAX_JOBS)),
    )
    return subscriber, future
