"""Análise determinística da referência: download, dados técnicos, quadros, cortes, áudio e transcrição.

Nada aqui usa IA generativa. O resultado são evidências que depois são interpretadas na etapa de texto.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .errors import Blocked

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
Log = Callable[[str], None]


def slugify(value: str, limit: int = 60) -> str:
    value = re.sub(r"https?://", "", str(value))
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:limit].strip("-") or "item"


VIDEO_DURATIONS = (6, 10, 15)   # durações que o Grok oferece


def snap_duration(requested: float | int | None, max_seconds: int = 15) -> int:
    """Menor duração do Grok que comporta o clipe pedido (6, 10 ou 15 s). Sem pedido: 6 s."""
    if requested is None or requested <= 0:
        return VIDEO_DURATIONS[0]
    for option in VIDEO_DURATIONS:
        if option <= max_seconds and requested <= option + 0.01:
            return option
    raise ValueError(f"Duração de {requested} s acima do máximo do Grok ({min(max_seconds, VIDEO_DURATIONS[-1])} s)")


def _run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)


def require_tools() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        raise Blocked("MISSING_INPUT", f"Ferramenta ausente no PC do worker: {', '.join(missing)}.", "Instale o FFmpeg e reinicie o worker.")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def fetch_source(source: str, outdir: Path, log: Log = print) -> tuple[Path, Path | None]:
    """Devolve (vídeo, metadados). `source` é URL http(s) ou caminho de um arquivo local."""
    outdir.mkdir(parents=True, exist_ok=True)
    video = outdir / "referencia.mp4"
    meta = next((p for p in (outdir / "referencia.info.json", outdir / "referencia_tikwm.json") if p.exists()), None)
    if video.exists() and video.stat().st_size > 0:
        return video, meta

    source = source.strip().strip('"')
    if not re.match(r"^https?://", source, re.I):
        local = Path(source)
        if not local.is_file():
            raise Blocked("SOURCE_UNAVAILABLE", f"Arquivo não encontrado no PC do worker: {source}")
        shutil.copyfile(local, video)
        return video, None

    errors: list[str] = []
    log("Baixando a referência com yt-dlp…")
    proc = _run([sys.executable, "-m", "yt_dlp", "-o", str(outdir / "referencia.%(ext)s"), "--merge-output-format", "mp4",
                 "--write-info-json", "--no-playlist", "--no-progress", "--", source])
    info = outdir / "referencia.info.json"
    if proc.returncode == 0 and video.exists() and has_audio(video):
        return video, info if info.exists() else None
    errors.append("yt-dlp: " + (proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "vídeo sem áudio ou não baixado"))

    log("yt-dlp não bastou; tentando a rota alternativa (TikWM)…")
    try:
        api = "https://www.tikwm.com/api/?" + urllib.parse.urlencode({"url": source, "hd": 1})
        raw = _http_get(api)
        data = (json.loads(raw.decode("utf-8-sig")).get("data") or {})
        media_url = data.get("play") or data.get("hdplay")
        if not media_url:
            raise RuntimeError("TikWM não devolveu URL de mídia")
        if media_url.startswith("/"):
            media_url = "https://www.tikwm.com" + media_url
        tikwm = outdir / "referencia_tikwm.json"
        tikwm.write_bytes(raw)
        video.write_bytes(_http_get(media_url))
        return video, tikwm
    except Exception as exc:  # noqa: BLE001 - registramos as duas falhas para o usuário
        errors.append(f"TikWM: {exc}")
    video.unlink(missing_ok=True)
    raise Blocked("SOURCE_UNAVAILABLE", "Não foi possível baixar a referência. " + " | ".join(errors))


def _http_get(url: str) -> bytes:
    if not re.match(r"^https://", url, re.I):
        raise RuntimeError("URL inesperada")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - https validado acima
        return response.read()


# ---------------------------------------------------------------------------
# Dados técnicos e metadados
# ---------------------------------------------------------------------------
def probe(video: Path) -> dict:
    proc = _run(["ffprobe", "-v", "error", "-show_entries",
                 "format=duration,bit_rate:stream=index,codec_type,codec_name,width,height,avg_frame_rate,bit_rate",
                 "-of", "json", str(video)])
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe falhou: {proc.stderr.strip()[:300]}")
    raw = json.loads(proc.stdout)
    streams = raw.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise RuntimeError("O arquivo não tem stream de vídeo.")
    num, _, den = str(v.get("avg_frame_rate", "0/1")).partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {
        "duration": round(float(raw.get("format", {}).get("duration") or 0), 2),
        "width": v.get("width"), "height": v.get("height"), "fps": round(fps, 2),
        "video_codec": v.get("codec_name"), "audio_codec": a.get("codec_name") if a else None,
        "has_audio": a is not None,
        "bit_rate": int(raw.get("format", {}).get("bit_rate") or 0) or None,
    }


def has_audio(video: Path) -> bool:
    try:
        return bool(probe(video)["has_audio"])
    except Exception:  # noqa: BLE001
        return False


def read_metadata(meta: Path | None) -> dict:
    """Normaliza os campos que realmente vieram no arquivo; o que não veio fica ausente."""
    if meta is None or not meta.exists():
        return {}
    raw = json.loads(meta.read_text(encoding="utf-8-sig"))
    if meta.name.endswith("_tikwm.json"):
        d = raw.get("data") or {}
        music = d.get("music_info") or {}
        out = {
            "caption": d.get("title"), "author": (d.get("author") or {}).get("unique_id"),
            "posted_ts": d.get("create_time"), "duration": d.get("duration"),
            "views": d.get("play_count"), "likes": d.get("digg_count"), "comments": d.get("comment_count"),
            "shares": d.get("share_count"), "saves": d.get("collect_count"),
            "sound_title": music.get("title"), "sound_author": music.get("author"),
        }
    else:
        out = {
            "caption": raw.get("description") or raw.get("title"), "author": raw.get("uploader") or raw.get("uploader_id"),
            "posted_ts": raw.get("timestamp"), "duration": raw.get("duration"),
            "views": raw.get("view_count"), "likes": raw.get("like_count"), "comments": raw.get("comment_count"),
            "shares": raw.get("repost_count"), "saves": raw.get("save_count"),
            "sound_title": raw.get("track"), "sound_author": raw.get("artist") or ", ".join(raw.get("artists") or []) or None,
        }
    out = {k: v for k, v in out.items() if v not in (None, "", [])}
    if out.get("caption"):
        out["hashtags"] = re.findall(r"#[\wÀ-ɏ]+", out["caption"])
    if out.get("posted_ts"):
        out.update(_posted_times(int(out["posted_ts"])))
    out["consulted_at"] = datetime.fromtimestamp(meta.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return out


def _posted_times(ts: int) -> dict:
    utc = datetime.fromtimestamp(ts, timezone.utc)
    out = {"posted_utc": utc.strftime("%Y-%m-%d %H:%M UTC")}
    try:
        from zoneinfo import ZoneInfo
        out["posted_brasilia"] = utc.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M")
        out["posted_berlin"] = utc.astimezone(ZoneInfo("Europe/Berlin")).strftime("%d/%m/%Y %H:%M")
    except Exception:  # noqa: BLE001 - sem tzdata só o UTC é informado
        pass
    return out


# ---------------------------------------------------------------------------
# Quadros
# ---------------------------------------------------------------------------
def _frame_at(cap, seconds: float):
    import cv2
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    target = max(0, min(count - 1, int(math.floor(seconds * fps))))
    for index in range(target, max(0, target - 5) - 1, -1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = cap.read()
        if ok:
            return frame, index / fps
    return None, None


def _tile(frame, width: int, label: str):
    import cv2
    from PIL import Image, ImageDraw
    h, w = frame.shape[:2]
    im = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).resize((width, max(1, round(width * h / w))))
    draw = ImageDraw.Draw(im)
    box = draw.textbbox((0, 0), label)
    draw.rectangle([0, 0, box[2] + 8, box[3] + 6], fill=(0, 0, 0))
    draw.text((4, 2), label, fill=(255, 255, 255))
    return im


def make_sheets(video: Path, outdir: Path, times: list[float], prefix: str, *, cols: int = 5, rows: int = 4,
                thumb_w: int = 270, labels: list[str] | None = None) -> list[Path]:
    """Folhas de contato com poucos quadros por imagem, para continuarem legíveis depois do upload."""
    import cv2
    from PIL import Image
    if not times:
        return []
    cap = cv2.VideoCapture(str(video))
    try:
        tiles = []
        for i, t in enumerate(times):
            frame, actual = _frame_at(cap, t)
            if frame is None:
                continue
            label = labels[i] if labels else f"{actual:.1f}s"
            tiles.append(_tile(frame, thumb_w, label))
    finally:
        cap.release()
    if not tiles:
        return []
    th = tiles[0].height
    per = cols * rows
    paths = []
    for n, start in enumerate(range(0, len(tiles), per), 1):
        chunk = tiles[start:start + per]
        used_rows = math.ceil(len(chunk) / cols)
        sheet = Image.new("RGB", (cols * thumb_w, used_rows * th), (0, 0, 0))
        for i, im in enumerate(chunk):
            sheet.paste(im, ((i % cols) * thumb_w, (i // cols) * th))
        path = outdir / f"{prefix}-{n:02d}.jpg"
        sheet.save(path, quality=88)
        paths.append(path)
    return paths


def detect_cuts(video: Path, threshold: float = 26.0) -> list[float]:
    """Candidatos a corte com sensibilidade fixa. São pistas, não âncoras nem contagem de cenas."""
    try:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import ContentDetector
        vid = open_video(str(video))
        manager = SceneManager()
        manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=10))
        manager.detect_scenes(vid, show_progress=False)
        def _seconds(tc) -> float:
            value = getattr(tc, "seconds", None)
            return float(value) if value is not None else float(tc.get_seconds())
        starts = [round(_seconds(scene[0]), 2) for scene in manager.get_scene_list()]
        return sorted(set([0.0, *starts]))
    except Exception:  # noqa: BLE001 - cai no detector simples abaixo
        return _detect_cuts_cv(video)


def _detect_cuts_cv(video: Path) -> list[float]:
    import cv2
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, round(fps / 4))
    prev = None
    scores: list[tuple[float, float]] = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            small = cv2.resize(frame, (120, max(1, round(frame.shape[0] * 120 / frame.shape[1]))))
            hist = cv2.calcHist([cv2.cvtColor(small, cv2.COLOR_BGR2HSV)], [0, 1, 2], None, [12, 12, 6], [0, 180, 0, 256, 0, 256])
            cv2.normalize(hist, hist)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if prev is not None:
                mad = float(cv2.mean(cv2.absdiff(gray, prev[0]))[0])
                bhat = float(cv2.compareHist(hist, prev[1], cv2.HISTCMP_BHATTACHARYYA))
                scores.append((idx / fps, mad / 35.0 + bhat * 2.0))
            prev = (gray, hist)
        idx += 1
    cap.release()
    starts = [0.0]
    for t, score in sorted(scores, key=lambda row: row[1], reverse=True):
        if score >= 2.0 and all(abs(t - s) >= 1.0 for s in starts):
            starts.append(round(t, 2))
    return sorted(starts)


# ---------------------------------------------------------------------------
# Áudio
# ---------------------------------------------------------------------------
def extract_audio(video: Path, out: Path) -> bool:
    proc = _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(out)])
    return proc.returncode == 0 and out.exists() and out.stat().st_size > 1000


def detect_silences(audio: Path) -> list[dict]:
    proc = _run(["ffmpeg", "-hide_banner", "-i", str(audio), "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
    ends = [(float(e), float(d)) for e, d in re.findall(r"silence_end: ([\d.]+) \| silence_duration: ([\d.]+)", proc.stderr)]
    return [{"start": round(max(0.0, s), 2), "end": round(e, 2), "duration": round(d, 2)} for s, (e, d) in zip(starts, ends)]


def transcribe(audio: Path, model_name: str, language: str | None) -> dict:
    import wave

    import numpy as np
    from faster_whisper import WhisperModel
    # O WAV já sai do ffmpeg em 16 kHz mono; lê-lo aqui evita depender do decodificador do PyAV.
    with wave.open(str(audio), "rb") as wav:
        if wav.getframerate() != 16000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise RuntimeError("Áudio fora do formato esperado (16 kHz, mono, 16 bits).")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=4, num_workers=1)
    segments, info = model.transcribe(samples, language=language, beam_size=5, vad_filter=True, condition_on_previous_text=False)
    return {
        "language": info.language, "language_probability": round(float(info.language_probability), 3),
        "segments": [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments],
    }


def last_frame(video: Path, out: Path) -> Path:
    """Último quadro de um clipe, para encadear a continuidade do clipe seguinte."""
    proc = _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-sseof", "-0.2", "-i", str(video), "-frames:v", "1", "-update", "1", str(out)])
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"Não foi possível extrair o último quadro de {video.name}: {proc.stderr.strip()[:200]}")
    return out


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------
def analyze(source: str, outdir: Path, *, whisper_model: str = "small", language: str | None = None,
            do_transcribe: bool = True, log: Log = print) -> dict:
    """Roda toda a análise local e grava `evidencias.json`. Reaproveita o que já existir."""
    require_tools()
    outdir.mkdir(parents=True, exist_ok=True)
    video, meta_path = fetch_source(source, outdir, log)
    info = probe(video)
    log(f"Vídeo: {info['duration']} s, {info['width']}x{info['height']}, {info['fps']} fps, áudio: {'sim' if info['has_audio'] else 'não'}.")

    duration = info["duration"]
    seconds = [float(i) for i in range(int(math.ceil(duration)))] or [0.0]
    for old in outdir.glob("folha-*.jpg"):
        old.unlink()
    sheets = make_sheets(video, outdir, seconds, "folha-1s")
    cuts = [c for c in detect_cuts(video) if c < duration]
    cut_sheets = make_sheets(video, outdir, [c + 0.15 for c in cuts], "folha-cortes", cols=4, rows=4, thumb_w=330,
                             labels=[f"C{i + 1:02d} {c:.2f}s" for i, c in enumerate(cuts)])
    # Gancho e fecho/CTA quadro a quadro (4 quadros por segundo nos 4 s iniciais e finais).
    hook_times = [i * 0.25 for i in range(16) if i * 0.25 < duration]
    end_start = max(0.0, duration - 4.0)
    end_times = [end_start + i * 0.25 for i in range(16) if end_start + i * 0.25 < duration]
    hook_sheets = make_sheets(video, outdir, hook_times, "folha-gancho", cols=4, rows=4, thumb_w=330, labels=[f"{t:.2f}s" for t in hook_times])
    end_sheets = make_sheets(video, outdir, end_times, "folha-final", cols=4, rows=4, thumb_w=330, labels=[f"{t:.2f}s" for t in end_times])
    log(f"{len(sheets)} folha(s) de 1 s, {len(cuts)} candidato(s) a corte, gancho e fecho a 4 quadros/s.")

    transcript: dict | None = None
    silences: list[dict] = []
    transcript_error = None
    audio = outdir / "audio.wav"
    if info["has_audio"] and extract_audio(video, audio):
        silences = detect_silences(audio)
        cached = outdir / "transcricao.json"
        if cached.exists():
            transcript = json.loads(cached.read_text(encoding="utf-8"))
        elif do_transcribe:
            log(f"Transcrevendo o áudio (modelo {whisper_model}; na primeira vez o modelo é baixado)…")
            try:
                transcript = transcribe(audio, whisper_model, language)
                cached.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:  # noqa: BLE001 - a análise segue sem transcrição, com o motivo registrado
                transcript_error = f"{type(exc).__name__}: {exc}"
                log(f"Transcrição indisponível: {transcript_error}")

    evidence = {
        "source": source, "video": video.name, "probe": info, "metadata": read_metadata(meta_path),
        "metadata_file": meta_path.name if meta_path else None,
        "sheets_1s": [p.name for p in sheets], "sheets_cuts": [p.name for p in cut_sheets],
        "sheets_hook": [p.name for p in hook_sheets], "sheets_end": [p.name for p in end_sheets],
        "cut_candidates": cuts, "cut_candidates_note": "Pistas de corte com sensibilidade fixa; não são âncoras nem contagem de cenas.",
        "silences": silences, "transcript": transcript, "transcript_error": transcript_error,
        "transcript_note": "Transcrição automática (ASR), não verificada com o áudio." if transcript else None,
    }
    (outdir / "evidencias.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence
