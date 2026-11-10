#!/usr/bin/env python
"""Analyze TikTok/Shorts-style AI videos into evidence artifacts.

This script downloads a URL when possible, creates contact sheets, extracts audio,
runs optional ASR, and writes unreviewed boundary candidates, not image anchors.
Original source-image counts require visual review and remain estimates.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, encoding="utf-8", errors="replace", env=env, check=check)


def install_deps(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    pkgs = ["yt-dlp", "opencv-python", "pillow", "scenedetect", "faster-whisper"]
    run([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(target), *pkgs])


def add_dep_path(target: Path) -> None:
    if target.exists():
        sys.path.insert(0, str(target))
        os.environ["PYTHONPATH"] = str(target) + os.pathsep + os.environ.get("PYTHONPATH", "")


def slugify(value: str) -> str:
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:80] or "video"


def download_video(source: str, outdir: Path, name: str | None) -> tuple[Path, Path | None]:
    outdir.mkdir(parents=True, exist_ok=True)
    if Path(source).exists():
        src = Path(source)
        dest = outdir / (name or src.stem)
        dest = dest.with_suffix(src.suffix)
        if src.resolve() != dest.resolve():
            dest.write_bytes(src.read_bytes())
        return dest, None

    base = name or "%(uploader_id)s_%(id)s"
    template = str(outdir / f"{base}.%(ext)s")
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "-o",
        template,
        "--merge-output-format",
        "mp4",
        "--write-info-json",
        "--no-playlist",
        source,
    ]
    run(cmd)
    mp4s = sorted(outdir.glob(f"{base if name else '*'}*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mp4s:
        raise FileNotFoundError("download completed but no mp4 was found")
    video = mp4s[0]
    info = video.with_suffix(".info.json")
    return video, info if info.exists() else None


def probe(video: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate",
            "-of",
            "json",
            str(video),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


def read_sample(cap, seconds: float, lower: float = 0.0, upper: float | None = None):
    """Read a bounded frame, retrying earlier frames near EOF; return actual time."""
    import cv2

    fps = cap.get(cv2.CAP_PROP_FPS)
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if not fps or fps <= 0 or count <= 0:
        return None, None
    first = max(0, math.ceil(lower * fps))
    last = count - 1
    if upper is not None:
        last = min(last, math.ceil(upper * fps) - 1)
    if first > last:
        return None, None
    target = max(first, min(last, math.floor(seconds * fps)))
    # Never retry into the previous candidate interval or silently leave a blank tile.
    for index in range(target, max(first, target - 4) - 1, -1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = cap.read()
        if ok:
            return frame, index / fps
    return None, None


def mark_missing(draw, x: int, y: int, width: int, height: int, label: str) -> None:
    draw.rectangle([x, y, x + width - 1, y + height - 1], fill=(60, 60, 60))
    draw.text((x + 3, y + 3), label + "\nSAMPLE UNAVAILABLE", fill=(255, 255, 255))


def make_sheet(video: Path, out: Path, interval: float = 1.0, cols: int = 8, thumb_w: int = 144) -> None:
    if interval <= 0 or cols < 1 or thumb_w < 1:
        raise ValueError("interval, cols, and thumb_w must be positive")
    import cv2
    from PIL import Image, ImageDraw, ImageFont

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    dur = frames / fps if fps else 0
    ok, frame = cap.read()
    if not ok:
        cap.release()
        raise RuntimeError(f"could not read {video}")
    h, w = frame.shape[:2]
    thumb_h = round(thumb_w * h / w)
    times = [i * interval for i in range(math.ceil(dur / interval)) if i * interval < dur]
    rows = max(1, math.ceil(len(times) / cols))
    sheet = Image.new("RGB", (cols * thumb_w, rows * thumb_h), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for i, t in enumerate(times):
        x = (i % cols) * thumb_w
        y = (i // cols) * thumb_h
        frame, actual = read_sample(cap, t)
        if frame is None:
            mark_missing(draw, x, y, thumb_w, thumb_h, f"{t:.2f}s requested")
            continue
        im = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).resize((thumb_w, thumb_h))
        sheet.paste(im, (x, y))
        draw.rectangle([x, y, x + 70, y + 14], fill=(0, 0, 0))
        draw.text((x + 2, y + 2), f"{actual:.2f}s", fill=(255, 255, 255), font=font)
    cap.release()
    sheet.save(out, quality=90)


def detect_scenes(video: Path, threshold: float = 26.0) -> list[float]:
    """Return candidate boundaries at a fixed sensitivity, never a target count."""
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("scene threshold must be finite and positive")
    try:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import ContentDetector
    except Exception:
        return detect_scenes_cv(video)
    vid = open_video(str(video))
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=10))
    manager.detect_scenes(vid, show_progress=False)
    starts = [round(scene[0].seconds, 2) for scene in manager.get_scene_list()]
    return sorted(set([0.0, *starts]))


def detect_scenes_cv(video: Path) -> list[float]:
    """Fallback scene-start hints using OpenCV histogram/gray-frame deltas."""
    try:
        import cv2
    except Exception:
        return []
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = frame_count / fps if fps else 0
    step = max(1, round(fps / 4))
    prev_gray = None
    prev_hist = None
    scores: list[tuple[float, float]] = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            t = idx / fps
            small = cv2.resize(frame, (120, max(1, round(frame.shape[0] * 120 / frame.shape[1]))))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1, 2], None, [12, 12, 6], [0, 180, 0, 256, 0, 256])
            cv2.normalize(hist, hist)
            if prev_gray is not None and prev_hist is not None:
                mad = float(cv2.mean(cv2.absdiff(gray, prev_gray))[0])
                bhat = float(cv2.compareHist(hist, prev_hist, cv2.HISTCMP_BHATTACHARYYA))
                score = mad / 35.0 + bhat * 2.0
                scores.append((t, score))
            prev_gray, prev_hist = gray, hist
        idx += 1
    cap.release()
    if not scores:
        return [0.0]
    # Absolute score floor avoids manufacturing cuts from a percentile of quiet motion.
    threshold = 2.0
    starts = [0.0]
    min_gap = 1.0
    for t, score in sorted(scores, key=lambda row: row[1], reverse=True):
        if score < threshold:
            continue
        if all(abs(t - existing) >= min_gap for existing in starts):
            starts.append(round(t, 2))
    return sorted(t for t in starts if 0 <= t < duration)


def make_keyframe_sheet(video: Path, starts: list[float], out: Path, cols: int = 4, thumb_w: int = 220) -> None:
    if not starts:
        return
    import cv2
    from PIL import Image, ImageDraw, ImageFont

    cap = cv2.VideoCapture(str(video))
    ok, frame = cap.read()
    if not ok:
        cap.release()
        raise RuntimeError(f"could not read {video}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps if fps else 0
    starts = sorted(set(t for t in starts if math.isfinite(t) and 0 <= t < duration))
    if not starts:
        cap.release()
        return
    h, w = frame.shape[:2]
    thumb_h = round(thumb_w * h / w)
    rows = max(1, math.ceil(len(starts) / cols))
    sheet = Image.new("RGB", (cols * thumb_w, rows * thumb_h), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else duration
        sample = start + min(0.15, (end - start) / 2)
        x = (i % cols) * thumb_w
        y = (i // cols) * thumb_h
        frame, actual = read_sample(cap, sample, lower=start, upper=end)
        if frame is None:
            mark_missing(draw, x, y, thumb_w, thumb_h, f"C{i+1:02d} {start:.2f}s hint")
            continue
        im = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).resize((thumb_w, thumb_h))
        sheet.paste(im, (x, y))
        draw.rectangle([x, y, x + thumb_w - 1, y + 30], fill=(0, 0, 0))
        draw.text((x + 3, y + 3), f"C{i+1:02d} boundary hint {start:.2f}s\nsample {actual:.2f}s / NOT AN ANCHOR", fill=(255, 255, 255), font=font)
    cap.release()
    sheet.save(out, quality=92)


def make_boundary_strips(video: Path, times: list[float], out: Path, thumb_w: int = 180) -> None:
    """Render local continuity evidence, one row per disputed boundary."""
    import cv2
    from PIL import Image, ImageDraw

    if not times:
        return
    if any(not math.isfinite(t) or t < 0 for t in times):
        raise ValueError("boundary times must be finite and nonnegative")
    cap = cv2.VideoCapture(str(video))
    try:
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError(f"could not read {video}")
        thumb_h = round(thumb_w * frame.shape[0] / frame.shape[1])
        offsets = [-0.6, -0.3, 0.0, 0.3, 0.6]
        sheet = Image.new("RGB", (len(offsets) * thumb_w, len(times) * thumb_h))
        draw = ImageDraw.Draw(sheet)
        for row, boundary in enumerate(times):
            for col, offset in enumerate(offsets):
                x, y = col * thumb_w, row * thumb_h
                frame, actual = read_sample(cap, boundary + offset)
                label = f"hint {boundary:.2f}s / {offset:+.1f}s"
                if frame is None:
                    mark_missing(draw, x, y, thumb_w, thumb_h, label)
                    continue
                im = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).resize((thumb_w, thumb_h))
                sheet.paste(im, (x, y))
                draw.rectangle([x, y, x + thumb_w - 1, y + 30], fill=(0, 0, 0))
                draw.text((x + 3, y + 3), f"{label}\nactual {actual:.2f}s", fill=(255, 255, 255))
        sheet.save(out, quality=92)
    finally:
        cap.release()


def extract_audio(video: Path, out: Path) -> bool:
    proc = run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(out)],
        check=False,
    )
    return proc.returncode == 0 and out.exists()


def transcribe(audio: Path, out: Path, model_name: str = "base", language: str | None = None, initial_prompt: str | None = None) -> bool:
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return False
    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=4, num_workers=1)
    segments, info = model.transcribe(
        str(audio),
        language=language,
        initial_prompt=initial_prompt,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
        word_timestamps=True,
    )
    result = {
        "language": info.language,
        "language_probability": info.language_probability,
        "segments": [
            {
                "start": round(s.start, 2),
                "end": round(s.end, 2),
                "text": s.text.strip(),
                "words": [
                    {"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word}
                    for w in (s.words or [])
                ],
            }
            for s in segments
        ],
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="TikTok URL or local video file")
    parser.add_argument("--out", default="work/tiktok-analysis", help="output directory")
    parser.add_argument("--name", default=None, help="output basename")
    parser.add_argument("--install-deps", action="store_true", help="install deps into output .deps")
    parser.add_argument("--no-transcribe", action="store_true", help="skip ASR")
    parser.add_argument("--language", default=None, help="force ASR language, e.g. pt or en")
    parser.add_argument("--model", default="base", help="faster-whisper model name or local path")
    parser.add_argument("--initial-prompt", default=None, help="evidenced names/terms to prime ASR")
    parser.add_argument("--scene-threshold", type=float, default=26.0, help="fixed ContentDetector sensitivity; not an anchor count")
    parser.add_argument("--boundary-times", type=float, nargs="+", help="disputed timestamps for local continuity strips")
    args = parser.parse_args()

    outdir = Path(args.out)
    deps = outdir / ".deps"
    if args.install_deps:
        install_deps(deps)
    add_dep_path(deps)

    name = args.name or slugify(args.source)
    video, info = download_video(args.source, outdir, args.name)
    stem = video.stem

    (outdir / f"{stem}_probe.json").write_text(json.dumps(probe(video), indent=2), encoding="utf-8")
    make_sheet(video, outdir / f"{stem}_1s_sheet.jpg")
    starts = detect_scenes(video, threshold=args.scene_threshold)
    (outdir / f"{stem}_scene_starts.txt").write_text("\n".join(str(s) for s in starts), encoding="utf-8")
    detected_sheet = outdir / f"{stem}_detected_scenes.jpg"
    make_keyframe_sheet(video, starts, detected_sheet)
    boundary_sheet = outdir / f"{stem}_boundary_strips.jpg"
    if args.boundary_times:
        make_boundary_strips(video, args.boundary_times, boundary_sheet)

    audio = outdir / f"{stem}_audio.wav"
    if extract_audio(video, audio) and not args.no_transcribe:
        transcribe(
            audio,
            outdir / f"{stem}_transcript.json",
            model_name=args.model,
            language=args.language,
            initial_prompt=args.initial_prompt,
        )

    summary = {
        "video": str(video),
        "info_json": str(info) if info else None,
        "probe_json": str(outdir / f"{stem}_probe.json"),
        "one_second_sheet": str(outdir / f"{stem}_1s_sheet.jpg"),
        "detected_scene_sheet": str(detected_sheet) if detected_sheet.exists() else None,
        "scene_starts": starts,
        "scene_starts_kind": "unreviewed_boundary_candidates_not_image_anchors",
        "scene_threshold": args.scene_threshold,
        "boundary_strip_sheet": str(boundary_sheet) if args.boundary_times else None,
        "audio": str(audio) if audio.exists() else None,
        "transcript_json": str(outdir / f"{stem}_transcript.json"),
        "note": "C labels and scene_starts are boundary hints, not anchors. Review continuity and reuses; source-image counts are estimates. ASR needs audio/caption verification.",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
