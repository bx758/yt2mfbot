from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from .config import MEDIAFIRE_SESSION, MFCMD_PATH, TELEGRAM_TIMEOUT


async def mediafire(path) -> str:
    if not MEDIAFIRE_SESSION:
        raise RuntimeError("MEDIAFIRE_SESSION is not configured")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(MFCMD_PATH),
        "-s", str(MEDIAFIRE_SESSION),
        "-f", str(Path(path).resolve()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), TELEGRAM_TIMEOUT)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise TimeoutError(f"MediaFire upload timed out after {TELEGRAM_TIMEOUT}s")
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    if process.returncode != 0:
        raise RuntimeError((err or out)[-5000:])
    urls = [line.strip() for line in out.splitlines() if line.strip().startswith("https://www.mediafire.com/")]
    if not urls:
        raise RuntimeError(out[-5000:] or "MediaFire URL was not returned")
    return urls[-1]


async def telegram(bot, chat_id: int, path, caption: str = ""):
    with open(path, "rb") as file:
        return await bot.send_document(
            chat_id=chat_id,
            document=file,
            filename=Path(path).name,
            caption=caption,
            read_timeout=TELEGRAM_TIMEOUT,
            write_timeout=TELEGRAM_TIMEOUT,
            connect_timeout=60,
            pool_timeout=60,
        )
