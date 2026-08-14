from __future__ import annotations

import os
from pathlib import Path


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value or ""


PROJECT_ID = env("GCP_PROJECT_ID", required=True)
PUBSUB_TOPIC = env("PUBSUB_TOPIC", "video-jobs")
PUBSUB_SUBSCRIPTION = env("PUBSUB_SUBSCRIPTION", "video-workers")

DOWNLOAD_DIR = Path(env("DOWNLOAD_DIR", "/var/lib/yt2mf/jobs"))

MAX_CONCURRENT_DOWNLOADS = int(env("MAX_CONCURRENT_DOWNLOADS", "1"))
MAX_CONCURRENT_UPLOADS = int(env("MAX_CONCURRENT_UPLOADS", "1"))
MAX_CONCURRENT_COMPRESSION = int(env("MAX_CONCURRENT_COMPRESSION", "1"))
WORKER_MAX_JOBS = int(env("WORKER_MAX_JOBS", "1"))

JOB_TIMEOUT = int(env("JOB_TIMEOUT", "7200"))
TELEGRAM_TIMEOUT = int(env("TELEGRAM_TIMEOUT", "7200"))
PUBSUB_ACK_DEADLINE = int(env("PUBSUB_ACK_DEADLINE", "600"))
JOB_LEASE_SECONDS = int(env("JOB_LEASE_SECONDS", "1800"))
JOB_HEARTBEAT_SECONDS = int(env("JOB_HEARTBEAT_SECONDS", "300"))
MAX_JOB_ATTEMPTS = int(env("MAX_JOB_ATTEMPTS", "3"))

YTDLP_PATH = env("YTDLP_PATH", "yt-dlp")
DENO_PATH = env("DENO_PATH", "")
COOKIES_PATH = env("COOKIES_PATH", "")
IMPERSONATE_BROWSER = env("IMPERSONATE_BROWSER", "")
YTDLP_USER_AGENT = env("YTDLP_USER_AGENT", "")
YTDLP_REMOTE_COMPONENTS = env("YTDLP_REMOTE_COMPONENTS", "")

MFCMD_PATH = Path(env("MFCMD_PATH", "/opt/yt2mf/mfcmd.py"))
MEDIAFIRE_SESSION = env("MEDIAFIRE_SESSION", "/var/lib/yt2mf/session.json")

DATABASE_URL = env("DATABASE_URL", required=True)
BOT_TOKEN = env("BOT_TOKEN", required=True)

WORKER_ID = env("WORKER_ID", "")
LOG_LEVEL = env("LOG_LEVEL", "INFO")

# Optional GCS bootstrap for worker-local assets such as cookies/session.
GCS_BUCKET = env("GCS_BUCKET", "")
GCS_SESSION_OBJECT = env("GCS_SESSION_OBJECT", "session.json")
GCS_COOKIES_OBJECT = env("GCS_COOKIES_OBJECT", "youtube-cookies.txt")
