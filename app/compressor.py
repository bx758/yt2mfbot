from __future__ import annotations

import asyncio
from pathlib import Path

from .downloader import run


async def compress_video(source: Path, crf: int = 28, preset: str = "veryfast") -> Path:
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    output = source.with_name(f"{source.stem}.compressed.mp4")
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(output),
    ]
    rc, stdout, stderr = await run(command, timeout=7200, cwd=source.parent)
    if rc != 0:
        raise RuntimeError((stderr or stdout)[-5000:])
    return output
