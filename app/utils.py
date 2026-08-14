from __future__ import annotations

import html
import re
import shutil
from pathlib import Path


def esc(value) -> str:
    return html.escape(str(value))


def safe_filename(name: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return result[:180] or "video.mp4"


def mb(path) -> float:
    return Path(path).stat().st_size / 1024 / 1024


def disk_stats(path: Path):
    return shutil.disk_usage(path)
