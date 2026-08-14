from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from .config import (
    DOWNLOAD_DIR,
    JOB_HEARTBEAT_SECONDS,
    JOB_LEASE_SECONDS,
    MAX_CONCURRENT_COMPRESSION,
    MAX_CONCURRENT_DOWNLOADS,
    MAX_CONCURRENT_UPLOADS,
    MAX_JOB_ATTEMPTS,
)
from .database import (
    add_history,
    clear_job_file,
    complete_job,
    fail_job,
    get_job,
    update_job,
    heartbeat_job,
)
from .downloader import download
from .uploader import mediafire, telegram
from .compressor import compress_video
from .utils import mb

logger = logging.getLogger(__name__)


class JobManager:
    """Process one claimed database job on a worker VM."""

    def __init__(self, bot, worker_id: str):
        self.bot = bot
        self.worker_id = worker_id
        self.download_sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        self.upload_sem = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)
        self.compression_sem = asyncio.Semaphore(MAX_CONCURRENT_COMPRESSION)

    async def _cancelled(self, job_id: int) -> bool:
        job = await asyncio.to_thread(get_job, job_id)
        return not job or job["status"] == "cancelled"

    async def _heartbeat_loop(self, job_id: int, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=JOB_HEARTBEAT_SECONDS)
                return
            except asyncio.TimeoutError:
                ok = await asyncio.to_thread(heartbeat_job, job_id, self.worker_id, JOB_LEASE_SECONDS)
                if not ok:
                    logger.warning("Lost lease for job=%s", job_id)
                    return

    async def process(self, job_id: int) -> None:
        job = await asyncio.to_thread(get_job, job_id)
        if not job or job["status"] == "cancelled":
            return

        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(job_id, heartbeat_stop))
        try:
            if job["format"] == "telegram_compress":
                await self.process_telegram_compression(job_id)
            else:
                await self._process_download_job(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            current = await asyncio.to_thread(get_job, job_id)
            attempts = int(current["attempts"]) if current else MAX_JOB_ATTEMPTS
            retry = attempts < MAX_JOB_ATTEMPTS
            await asyncio.to_thread(fail_job, job_id, self.worker_id, message, retry)
            if current:
                try:
                    await self.bot.send_message(
                        current["chat_id"],
                        f"❌ Job #{job_id} {'failed; retrying' if retry else 'failed'}\n\n{message[-3000:]}",
                    )
                except Exception:
                    logger.exception("Could not send failure message for job=%s", job_id)
            raise
        finally:
            heartbeat_stop.set()
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    async def _process_download_job(self, job: dict) -> None:
        job_id = int(job["id"])
        job_dir = DOWNLOAD_DIR / str(job_id)
        try:
            await asyncio.to_thread(update_job, job_id, status="downloading")
            async with self.download_sem:
                file_path = await download(job["url"], job["format"] or "best", job_dir)
            if await self._cancelled(job_id):
                return

            size_mb = mb(file_path)
            await asyncio.to_thread(
                update_job,
                job_id,
                file_path=str(file_path),
                size_mb=size_mb,
                status="uploading",
            )

            async with self.upload_sem:
                if job["destination"] == "telegram":
                    await telegram(self.bot, job["chat_id"], file_path, f"✅ Job #{job_id}")
                else:
                    url = await mediafire(file_path)
                    await self.bot.send_message(
                        job["chat_id"],
                        f"✅ <b>آپلود با موفقیت انجام شد</b>\n\n🔗 {url}",
                        parse_mode="HTML",
                    )

            completed = await asyncio.to_thread(
                complete_job,
                job_id,
                self.worker_id,
                str(file_path),
                size_mb,
            )
            if not completed:
                raise RuntimeError("Job ownership was lost before completion")
            await asyncio.to_thread(
                add_history,
                job["user_id"],
                job["url"],
                job["title"],
                job["destination"],
                str(file_path),
                size_mb,
            )
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)
            try:
                await asyncio.to_thread(clear_job_file, job_id)
            except Exception:
                logger.exception("Failed to clear DB file path for job=%s", job_id)

    async def process_telegram_compression(self, job_id: int) -> None:
        job = await asyncio.to_thread(get_job, job_id)
        if not job or job["status"] == "cancelled":
            return

        job_dir = DOWNLOAD_DIR / str(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        compressed_path: Path | None = None
        try:
            await asyncio.to_thread(update_job, job_id, status="downloading")
            file_id = (job["url"] or "").removeprefix("telegram:")
            if not file_id:
                raise RuntimeError("Telegram file_id is missing")
            telegram_file = await self.bot.get_file(file_id)
            source = job_dir / "source"
            await telegram_file.download_to_drive(custom_path=str(source))
            if not source.is_file():
                raise RuntimeError("Telegram source file was not downloaded")

            source_mb = mb(source)
            await asyncio.to_thread(
                update_job,
                job_id,
                file_path=str(source),
                size_mb=source_mb,
                status="compressing",
            )
            async with self.compression_sem:
                compressed_path = await compress_video(source)
            if not compressed_path.is_file():
                raise RuntimeError("Compressed video was not created")

            output_mb = mb(compressed_path)
            await asyncio.to_thread(
                update_job,
                job_id,
                file_path=str(compressed_path),
                size_mb=output_mb,
                status="uploading",
            )
            async with self.upload_sem:
                await telegram(
                    self.bot,
                    job["chat_id"],
                    compressed_path,
                    f"✅ Job #{job_id}\n📦 {output_mb:.2f} MB",
                )

            completed = await asyncio.to_thread(
                complete_job,
                job_id,
                self.worker_id,
                str(compressed_path),
                output_mb,
            )
            if not completed:
                raise RuntimeError("Job ownership was lost before completion")
            await asyncio.to_thread(
                add_history,
                job["user_id"],
                job["url"],
                job["title"],
                "telegram",
                "",
                output_mb,
            )
        finally:
            if compressed_path:
                compressed_path.unlink(missing_ok=True)
            shutil.rmtree(job_dir, ignore_errors=True)
            try:
                await asyncio.to_thread(clear_job_file, job_id)
            except Exception:
                logger.exception("Failed to clear DB file path for job=%s", job_id)
