#!/usr/bin/env python
"""Render a reviewed anchor manifest to storyboard JPG, HTML, and Markdown."""

from __future__ import annotations

import argparse
import html
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont, ImageOps


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def font(size: int):
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def wrap(draw: ImageDraw.ImageDraw, value: str, selected_font, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in value.split():
        trial = (current + " " + word).strip()
        if current and draw.textlength(trial, font=selected_font) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [""]


def metadata_stats(meta: dict, duration: str, queried: str | None) -> str:
    if not meta:
        return f"Duracao: {duration}. Metricas nao fornecidas."
    data = meta.get("data") or meta
    created = data.get("create_time") or data.get("timestamp")
    posted = "indisponivel"
    if created:
        posted = datetime.fromtimestamp(int(created), timezone(timedelta(hours=-3))).strftime("%d/%m/%Y %H:%M:%S")
    views = data.get("play_count", data.get("view_count", "indisponivel"))
    likes = data.get("digg_count", data.get("like_count", "indisponivel"))
    comments = data.get("comment_count", "indisponivel")
    shares = data.get("share_count", "indisponivel")
    saves = data.get("collect_count", "indisponivel")
    suffix = f" Metricas consultadas em {queried}." if queried else ""
    return (
        f"Duracao: {duration}. Views: {views}; likes: {likes}; comentarios: {comments}; "
        f"compartilhamentos: {shares}; salvos: {saves}. Postado: {posted} (Brasilia).{suffix} "
        "Compartilhamentos nao equivalem necessariamente a reposts."
    )


def render_storyboard(data: dict, video: Path, out: Path) -> Path:
    anchors = data["anchors"]
    count = len(anchors)
    cols, tile_w, image_h, caption_h, gap, margin, header_h = 4, 288, 512, 100, 16, 24, 110
    rows = max(1, math.ceil(count / cols))
    canvas = Image.new(
        "RGB",
        (margin * 2 + cols * tile_w + (cols - 1) * gap, header_h + rows * (image_h + caption_h + gap) + margin),
        "#f0f3f5",
    )
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 15), data["title"], font=font(30), fill="#17221d")
    draw.text(
        (margin, 56),
        f"{count} ancoras provaveis | {data['slug']} | Quadros-proxy do video",
        font=font(18),
        fill="#334444",
    )

    cap = cv2.VideoCapture(str(video))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"could not open video: {video}")
        for index, anchor in enumerate(anchors):
            cap.set(cv2.CAP_PROP_POS_MSEC, float(anchor["sample"]) * 1000)
            ok, frame = cap.read()
            if not ok:
                raise ValueError(f"missing proxy frame at {anchor['sample']}s")
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            image = ImageOps.contain(image, (tile_w, image_h))
            x = margin + (index % cols) * (tile_w + gap)
            y = header_h + (index // cols) * (image_h + caption_h + gap)
            canvas.paste(image, (x + (tile_w - image.width) // 2, y))
            draw.rectangle((x, y, x + tile_w, y + 29), fill="#17221d")
            tile_time = anchor.get("tile_time", anchor["time"])
            draw.text((x + 7, y + 3), f"A{index + 1:02d} | {tile_time}", font=font(17), fill="white")
            draw.rectangle((x, y + image_h, x + tile_w, y + image_h + caption_h), fill="white")
            caption_lines = wrap(draw, anchor["label"], font(18), tile_w - 18)
            if len(caption_lines) > 3:
                raise ValueError(f"caption is too long for storyboard tile: {anchor['label']}")
            for line_index, line in enumerate(caption_lines):
                draw.text((x + 9, y + image_h + 8 + line_index * 21), line, font=font(18), fill="#17221d")
            draw.text(
                (x + 9, y + image_h + 78),
                f"Quadro-proxy: {float(anchor['sample']):.2f}s",
                font=font(14),
                fill="#52645c",
            )
    finally:
        cap.release()

    path = out / f"storyboard_{data['slug']}.jpg"
    canvas.save(path, quality=94, subsampling=0)
    return path


def render_markdown(data: dict, stats: str, storyboard: Path, out: Path) -> Path:
    anchors = data["anchors"]
    title = data.get("transcript_title", "Transcricao original e traducao")
    headers = data.get("transcript_headers", ["Tempo", "Original", "Traducao"])
    parts = [
        f"# {data['title']}",
        f"Fonte: {data['url']}",
        f"## Contagem\n\n{data['count_note']}",
        stats,
        f"## Contexto\n\n{data['context']}",
        f"## Personagens e objetos\n\n{data['ontology']}",
        f"## Cadeia causal\n\n{data['chain']}",
        "## Storyboard das imagens-ancora",
        "| Ancora | Tempo | Composicao inicial | Fala | Funcao e continuidade |",
        "|---|---|---|---|---|",
    ]
    for index, anchor in enumerate(anchors):
        parts.append(
            f"| A{index + 1:02d} | {anchor['time']} | {anchor['still']} | {anchor['speech']} | {anchor['cause']} |"
        )
    if data.get("timeline"):
        parts.extend(["## Ordem cronologica e retornos", "| Tempo | Ancora/tipo | Acontecimento |", "|---|---|---|"])
        parts.extend("| " + " | ".join(row) + " |" for row in data["timeline"])
    parts.append("## Image-to-video completo, por ancora")
    for index, anchor in enumerate(anchors):
        parts.append(f"### A{index + 1:02d} - {anchor['time']}\n\n{anchor['motion']}")
    parts.extend(["## " + title, data["transcript_note"], "| " + " | ".join(headers) + " |", "|---|---|---|"])
    parts.extend("| " + " | ".join(row) + " |" for row in data["transcript"])
    parts.append("## Continuidade e incertezas\n\n" + "\n\n".join(data["notes"]))
    parts.append(f"## Storyboard visual\n\n![Storyboard]({storyboard.as_posix()})")
    text = parts[0]
    for previous, current in zip(parts, parts[1:]):
        text += ("\n" if previous.startswith("|") and current.startswith("|") else "\n\n") + current
    path = out / f"analise_{data['slug']}.md"
    path.write_text(text, encoding="utf-8")
    return path


def render_html(data: dict, stats: str, storyboard: Path, out: Path) -> Path:
    esc = html.escape
    anchors = data["anchors"]
    transcript_title = data.get("transcript_title", "Transcricao original e traducao")
    transcript_headers = data.get("transcript_headers", ["Tempo", "Original", "Traducao"])
    parts = [
        f"<h1>{esc(data['title'])}</h1>",
        f"<p><a href=\"{esc(data['url'])}\">Video original</a></p>",
        f"<p><strong>{esc(data['count_note'])}</strong></p>",
        f"<p>{esc(stats)}</p>",
    ]
    for heading, key in [("Contexto", "context"), ("Personagens e objetos", "ontology"), ("Cadeia causal", "chain")]:
        parts.append(f"<h2>{heading}</h2><p>{esc(data[key])}</p>")
    parts.append(
        f"<h2>Storyboard visual</h2><a href=\"{storyboard.name}\"><img src=\"{storyboard.name}\" "
        f"alt=\"Storyboard com {len(anchors)} ancoras\"></a><h2>Analise completa por ancora</h2>"
    )
    if data.get("timeline"):
        parts.append("<h2>Ordem cronologica e retornos</h2><table><thead><tr><th>Tempo</th><th>Ancora/tipo</th><th>Acontecimento</th></tr></thead><tbody>")
        for row in data["timeline"]:
            parts.append("<tr>" + "".join("<td>" + esc(cell) + "</td>" for cell in row) + "</tr>")
        parts.append("</tbody></table>")
    for index, anchor in enumerate(anchors):
        parts.append(f"<section><h3>A{index + 1:02d} | {esc(anchor['time'])} | {esc(anchor['label'])}</h3>")
        parts.append(f"<p>Quadro-proxy em {float(anchor['sample']):.2f}s; nao e o arquivo-fonte da geracao.</p>")
        for heading, key in [("Imagem-ancora", "still"), ("Fala", "speech"), ("Causa e funcao", "cause")]:
            parts.append(f"<p><strong>{heading}:</strong> {esc(anchor[key])}</p>")
        parts.append("</section>")
    parts.append("<h2>Image-to-video completo, por ancora</h2>")
    for index, anchor in enumerate(anchors):
        parts.append(f"<section><h3>A{index + 1:02d} | {esc(anchor['time'])}</h3><p>{esc(anchor['motion'])}</p></section>")
    parts.append(f"<h2>{esc(transcript_title)}</h2><p>{esc(data['transcript_note'])}</p><table><thead><tr>")
    parts.append("".join("<th>" + esc(value) + "</th>" for value in transcript_headers) + "</tr></thead><tbody>")
    for row in data["transcript"]:
        parts.append("<tr>" + "".join("<td>" + esc(cell) + "</td>" for cell in row) + "</tr>")
    parts.append("</tbody></table><h2>Continuidade e incertezas</h2>")
    parts.extend("<p>" + esc(note) + "</p>" for note in data["notes"])
    style = (
        "body{font:16px/1.6 Arial,sans-serif;max-width:1120px;margin:30px auto;padding:0 20px;color:#192e28}"
        "h1{font-size:28px}h2{font-size:23px;margin-top:36px}h3{font-size:19px}img{max-width:100%;height:auto}"
        "section{border-bottom:1px solid #ccd8d2;padding:16px 0}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #cbd4ce;padding:10px;text-align:left;vertical-align:top}a{color:#146a56}"
        "p{overflow-wrap:anywhere}@media(max-width:600px){td,th{padding:5px;font-size:13px}}"
    )
    document = (
        "<!doctype html><html lang=\"pt-BR\"><meta charset=\"utf-8\"><meta name=\"viewport\" "
        f"content=\"width=device-width,initial-scale=1\"><title>{esc(data['title'])}</title><style>{style}</style>"
        + "".join(parts)
        + "</html>"
    )
    path = out / f"analise_{data['slug']}.html"
    path.write_text(document, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", help="reviewed JSON manifest")
    parser.add_argument("--video", required=True, help="finished source video used for proxy frames")
    parser.add_argument("--metadata", default=None, help="optional TikWM or yt-dlp metadata JSON")
    parser.add_argument("--out", default="outputs", help="output directory")
    args = parser.parse_args()

    manifest = Path(args.manifest)
    video = Path(args.video)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = load_json(manifest)
    meta = load_json(Path(args.metadata)) if args.metadata else {}
    stats = metadata_stats(meta, data["duration"], data.get("queried"))
    storyboard = render_storyboard(data, video, out)
    markdown = render_markdown(data, stats, storyboard, out)
    report = render_html(data, stats, storyboard, out)
    print(json.dumps({"storyboard": str(storyboard), "markdown": str(markdown), "html": str(report)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
