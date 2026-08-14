from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .config import (
    COOKIES_PATH,
    DENO_PATH,
    IMPERSONATE_BROWSER,
    JOB_TIMEOUT,
    YTDLP_PATH,
    YTDLP_REMOTE_COMPONENTS,
    YTDLP_USER_AGENT,
)


async def run(command: list[str], timeout: int = 3600, cwd: Path | None = None):
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise TimeoutError(f"Command timed out after {timeout}s")
    return process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


def add_common_options(command: list[str]) -> list[str]:
    if DENO_PATH:
        command += ["--js-runtimes", f"deno:{DENO_PATH}"]
    if YTDLP_REMOTE_COMPONENTS:
        command += ["--remote-components", YTDLP_REMOTE_COMPONENTS]
    if COOKIES_PATH:
        command += ["--cookies", COOKIES_PATH]
    if IMPERSONATE_BROWSER:
        command += ["--impersonate", IMPERSONATE_BROWSER]
    if YTDLP_USER_AGENT:
        command += ["--user-agent", YTDLP_USER_AGENT]
    return command


def format_selector(quality: str) -> str:
    if quality == "audio":
        return "bestaudio/best"
    if quality in {"360", "480", "720", "1080"}:
        return f"bv*[height<={quality}][ext=mp4]+ba[ext=m4a]/b[height<={quality}][ext=mp4]/b"
    return "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"


async def info(url: str):
    command = add_common_options([
        YTDLP_PATH, "--dump-single-json", "--no-playlist", "--no-warnings",
    ])
    rc, stdout, stderr = await run(command + [url], timeout=120)
    if rc != 0:
        raise RuntimeError((stderr or stdout)[-5000:])
    return json.loads(stdout)


async def search(query: str, limit: int = 5):
    query = query.strip()
    if not query:
        raise ValueError("Search query is empty.")
    limit = max(1, min(limit, 30))
    command = add_common_options([
        YTDLP_PATH, "--dump-single-json", "--flat-playlist",
        "--playlist-end", str(limit), "--no-warnings", "--skip-download",
    ])
    rc, stdout, stderr = await run(command + [f"ytsearch{limit}:{query}"], timeout=120)
    if rc != 0:
        raise RuntimeError((stderr or stdout)[-5000:])
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Invalid JSON returned by yt-dlp search.") from exc
    results = []
    for entry in data.get("entries") or []:
        if not entry:
            continue
        video_id = entry.get("id") or ""
        url = entry.get("webpage_url") or entry.get("url")
        if not url and video_id:
            url = f"https://www.youtube.com/watch?v={video_id}"
        if not url:
            continue
        results.append({
            "id": video_id,
            "title": entry.get("title") or "Unknown",
            "url": url,
            "duration": entry.get("duration") or 0,
            "channel": entry.get("channel") or entry.get("uploader") or "",
        })
    return results


async def download(url: str, quality: str, job_dir: Path) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    output = job_dir / "%(title).180s.%(ext)s"
    command = add_common_options([
        YTDLP_PATH, "--no-playlist", "--no-warnings", "--restrict-filenames",
        "-f", format_selector(quality), "-o", str(output),
    ])
    if quality == "audio":
        command += ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        command += ["--merge-output-format", "mp4"]
    rc, stdout, stderr = await run(command + [url], timeout=JOB_TIMEOUT, cwd=job_dir)
    if rc != 0:
        raise RuntimeError((stderr or stdout)[-5000:])
    files = [
        p for p in job_dir.iterdir()
        if p.is_file() and p.stat().st_size > 0
        and (quality != "audio" or p.suffix.lower() == ".mp3")
    ]
    if not files:
        raise RuntimeError("yt-dlp produced no output file.")
    return max(files, key=lambda p: p.stat().st_mtime)
