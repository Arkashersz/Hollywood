#!/usr/bin/env python
"""Fetch TikTok metadata and an audio-bearing media copy through TikWM."""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def slugify(value: str) -> str:
    value = re.sub(r"https?://", "", value)
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()[:80] or "video"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="TikTok video URL")
    parser.add_argument("--out", default="work/tiktok-analysis", help="output directory")
    parser.add_argument("--name", default=None, help="output basename")
    parser.add_argument("--hd", action="store_true", help="prefer hdplay; it may be video-only")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = args.name or slugify(args.url)
    api = "https://www.tikwm.com/api/?" + urllib.parse.urlencode({"url": args.url, "hd": 1})
    raw = fetch(api)
    payload = json.loads(raw.decode("utf-8-sig"))
    data = payload.get("data") or {}
    media_url = data.get("hdplay") if args.hd else data.get("play")
    if not media_url:
        media_url = data.get("play") or data.get("hdplay")
    if not media_url:
        raise RuntimeError(f"TikWM returned no playable media URL: {payload.get('msg', 'unknown error')}")

    metadata = out / f"{name}_tikwm.json"
    video = out / f"{name}.mp4"
    metadata.write_bytes(raw)
    video.write_bytes(fetch(media_url))
    print(json.dumps({"video": str(video), "metadata": str(metadata), "duration": data.get("duration")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
