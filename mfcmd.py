#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mediafire import MediaFireApi, MediaFireUploader


def upload_file(session_file: str, file_path: str) -> None:
    session_path = Path(session_file)
    target_path = Path(file_path).resolve()
    if not session_path.is_file():
        raise SystemExit(f"Session file not found: {session_path}")
    if not target_path.is_file():
        raise SystemExit(f"Target file not found: {target_path}")

    api = MediaFireApi()
    try:
        with session_path.open("r", encoding="utf-8") as fd:
            api.session = json.load(fd)
    except Exception as exc:
        raise SystemExit(f"Error loading session: {exc}") from exc

    uploader = MediaFireUploader(api)
    try:
        with target_path.open("rb") as fd:
            result = uploader.upload(fd, target_path.name)
        quickkey = getattr(result, "quickkey", None)
        if not quickkey and hasattr(result, "action_upload"):
            action = result.action_upload
            quickkey = getattr(action, "quickkey", None) if action else None
        if not quickkey and isinstance(result, dict):
            action = result.get("action_upload") or {}
            quickkey = result.get("quickkey") or action.get("quickkey")
        if not quickkey:
            raise RuntimeError(f"MediaFire did not return a quickkey: {result}")
        print(f"https://www.mediafire.com/file/{quickkey}")
    except Exception as exc:
        print(f"MediaFire upload failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--session", required=True)
    parser.add_argument("-f", "--file", required=True)
    args = parser.parse_args()
    upload_file(args.session, args.file)
