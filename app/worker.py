from __future__ import annotations

import asyncio
import logging
import os
import socket

from telegram import Bot

from .config import (
    BOT_TOKEN,
    JOB_LEASE_SECONDS,
    PUBSUB_SUBSCRIPTION,
    WORKER_ID,
    WORKER_MAX_JOBS,
)
from .database import claim_job, init_db, recover_expired_jobs
from .job_manager import JobManager
from .pubsub import subscribe

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("yt2mf.worker")


def get_worker_id() -> str:
    if WORKER_ID:
        return WORKER_ID
    hostname = socket.gethostname()
    instance_id = os.getenv("GCE_INSTANCE_ID") or os.getenv("INSTANCE_ID")
    return f"{hostname}-{instance_id}" if instance_id else hostname


async def main():
    worker_id = get_worker_id()
    logger.info("Starting worker=%s subscription=%s", worker_id, PUBSUB_SUBSCRIPTION)
    await asyncio.to_thread(init_db)
    recovered = await asyncio.to_thread(recover_expired_jobs)
    if recovered:
        logger.warning("Recovered expired jobs: %s", recovered)

    bot = Bot(token=BOT_TOKEN)
    manager = JobManager(bot=bot, worker_id=worker_id)
    loop = asyncio.get_running_loop()
    semaphore = asyncio.Semaphore(max(1, WORKER_MAX_JOBS))

    async def process_message(job_id: int):
        async with semaphore:
            job = await asyncio.to_thread(claim_job, job_id, worker_id, JOB_LEASE_SECONDS)
            if not job:
                logger.info("Job=%s unavailable/already claimed", job_id)
                return
            logger.info("Claimed job=%s worker=%s attempt=%s", job_id, worker_id, job["attempts"])
            await manager.process(job_id)

    def pubsub_callback(job_id: int):
        future = asyncio.run_coroutine_threadsafe(process_message(job_id), loop)
        future.result()

    subscriber, streaming_future = subscribe(pubsub_callback)
    try:
        await asyncio.to_thread(streaming_future.result)
    finally:
        subscriber.close()
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
