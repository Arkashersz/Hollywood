"""Montagem determinística dos prompts finais e dos documentos 04–08.

O prompt que aparece no documento é exatamente o que é enviado ao gerador: ambos saem daqui.
O bloco de estilo e a descrição fixa de cada personagem são colados idênticos em todos os prompts.
"""
from __future__ import annotations

from pathlib import Path

NA = "não obtido"


def _cell(value) -> str:
    return str(value if value not in (None, "", []) else NA).replace("|", "\\|").replace("\n", "<br>")


def _code(text: str) -> str:
    fence = "````" if "```" in text else "```"
    return f"{fence}text\n{text}\n{fence}"


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (nada informado)"


# ---------------------------------------------------------------------------
# Documento 01 · seção automática
# ---------------------------------------------------------------------------
def identification(evidence: dict) -> str:
    p, m = evidence["probe"], evidence.get("metadata") or {}
    metrics = ", ".join(f"{label}: {m[key]}" for key, label in (("views", "visualizações"), ("likes", "curtidas"), ("comments", "comentários"),
                                                               ("shares", "compartilhamentos"), ("saves", "salvos")) if key in m)
    sound = " — ".join(x for x in (m.get("sound_title"), m.get("sound_author")) if x)
    rows = [
        ("Link / origem", evidence["source"]),
        ("Autor", m.get("author")),
        ("Título / legenda", m.get("caption")),
        ("Hashtags originais", " ".join(m.get("hashtags", []))),
        ("Postado (Brasília)", m.get("posted_brasilia")),
        ("Postado (Berlim)", m.get("posted_berlin")),
        ("Duração", f"{p['duration']} s"),
        ("Formato", f"{p['width']}x{p['height']}, {p['fps']} fps, vídeo {p['video_codec']}, áudio {p['audio_codec'] or 'ausente'}"),
        ("Métricas", f"{metrics} (consulta em {m.get('consulted_at')})" if metrics else None),
        ("Som (metadados)", sound),
    ]
    table = "\n".join(f"| {k} | {_cell(v)} |" for k, v in rows)
    note = "" if m else "\n\nOs metadados do post não foram obtidos; nenhum deles foi estimado."
    return ("## 1. Identificação\n\n*Preenchida automaticamente a partir do arquivo e dos metadados baixados; não passou por IA.*\n\n"
            f"| Campo | Valor |\n| --- | --- |\n{table}{note}")


# ---------------------------------------------------------------------------
# Prompts finais
# ---------------------------------------------------------------------------
def full_body_prompt(bible: dict, ch: dict) -> str:
    return (f"{bible['style_block']}\n\nCharacter: {ch['fixed_description']}\n\n"
            "Full-body character reference image: neutral standing pose, front view, the whole figure visible from head to feet, "
            "plain light-gray seamless background, no other characters, no text. Vertical 2:3 portrait format.")


def sheet_prompt(bible: dict, ch: dict) -> str:
    expressions = ", ".join(ch["expressions"]) or "neutral, happy, sad, angry, surprised, afraid"
    details = ", ".join(ch["details"]) or "outfit and accessories"
    return (f"{bible['style_block']}\n\nCharacter: {ch['fixed_description']}\n\n"
            "Character reference sheet in landscape 16:9 format. Use the attached image as the exact identity reference: same face, body, outfit and colors in every view. "
            f"Include: full body front view and side profile, a face close-up, facial expressions ({expressions}), and detail callouts ({details}). "
            "Plain neutral background, clean layout, no text or labels.")


def environment_prompt(bible: dict, env: dict) -> str:
    return (f"{bible['style_block']}\n\nEnvironment: {env['fixed_description']}\n\n"
            "Establishing image of the empty environment: no people, no characters, no text. Vertical 9:16 format.")


def scene_prompt(bible: dict, plan: dict, scene: dict) -> str:
    chars = {c["name"]: c for c in bible["characters"]}
    env = next(e for e in plan["environments"] if e["name"] == scene["environment"])
    cast = "\n".join(f"- {n}: {chars[n]['fixed_description']}" for n in scene["characters"]) or "- none"
    return (f"{bible['style_block']}\n\nInitial still frame for an image-to-video clip. Vertical 9:16 format.\n\n"
            f"Composition: {scene['composition']}\n\n"
            f"Characters (match the attached character sheets exactly):\n{cast}\n\n"
            f"Environment (match the attached environment image): {env['fixed_description']}\n\n"
            "Show only this initial stable moment: no motion blur, no subtitles, no captions, no text overlays.")


def clip_prompt(bible: dict, clip: dict) -> str:
    chars = {c["name"]: c for c in bible["characters"]}
    voices = "\n".join(f"- {n}: {chars[n]['voice']['descriptor_en']}" for n in clip["speakers"])
    voice_block = f"\n\nVoices (keep identical in every clip):\n{voices}" if voices else ""
    return f"{clip['prompt']}{voice_block}\n\nDuration: {clip['duration_s']} seconds. Vertical 9:16."


# ---------------------------------------------------------------------------
# Caminhos padronizados (relativos à pasta do projeto)
# ---------------------------------------------------------------------------
def body_path(ch: dict) -> Path:
    return Path("imagens") / "personagens" / f"personagem-{ch['slug']}-corpo.png"


def sheet_path(ch: dict) -> Path:
    return Path("imagens") / "folhas" / f"folha-{ch['slug']}.png"


def env_path(env: dict) -> Path:
    return Path("imagens") / "ambientes" / f"ambiente-{env['slug']}.png"


def scene_path(scene_id: str) -> Path:
    return Path("imagens") / "cenas" / f"cena-{scene_id}.png"


def clip_path(n: int) -> Path:
    return Path("videos") / f"clipe-{n:02d}.mp4"


def visible(bible: dict) -> list[dict]:
    return [c for c in bible["characters"] if not c["voice_only"]]


# ---------------------------------------------------------------------------
# Documentos
# ---------------------------------------------------------------------------
def doc04(bible: dict) -> str:
    parts = ["## Bloco de estilo global", "Colado, idêntico, em todos os prompts de imagem.", _code(bible["style_block"]), bible["style_explanation_pt"]]
    for ch in bible["characters"]:
        parts += [f"## {ch['name']}", f"**Papel:** {ch['role_pt'] or NA}"]
        if ch["voice_only"]:
            parts.append("Personagem só de voz (sem imagem). Veja o Documento 06.")
            continue
        parts += ["**Descrição fixa** (imutável, colada em todo prompt em que aparece):", _code(ch["fixed_description"]),
                  "**Nunca pode mudar:**", _bullets(ch["never_change_pt"]),
                  f"**Prompt da imagem de corpo inteiro** → `{body_path(ch).as_posix()}`", _code(full_body_prompt(bible, ch))]
    parts.append("## O que você decide\nAo aprovar, o worker gera no ChatGPT uma imagem de corpo inteiro por personagem, com exatamente os prompts acima.")
    return "\n\n".join(p for p in parts if p)


def doc05(bible: dict) -> str:
    parts = ["Uma folha 16:9 por personagem, gerada a partir da imagem de corpo inteiro aprovada (anexada como referência)."]
    for ch in visible(bible):
        parts += [f"## {ch['name']}", f"**Referência anexada:** `{body_path(ch).as_posix()}`  \n**Arquivo:** `{sheet_path(ch).as_posix()}`",
                  "**A folha cobre:** corpo inteiro (frente e perfil), rosto em close, expressões e detalhes.",
                  "**Expressões:**", _bullets(ch["expressions"]), "**Detalhes:**", _bullets(ch["details"]),
                  "**Prompt:**", _code(sheet_prompt(bible, ch))]
    return "\n\n".join(parts)


def doc06(bible: dict) -> str:
    parts = ["A linha-descritor é colada em todo prompt de vídeo em que o personagem fala, para manter a mesma voz entre os clipes."]
    for ch in bible["characters"]:
        v = ch["voice"]
        parts += [f"## {ch['name']}", f"**Perfil:** {v['profile_pt'] or NA}",
                  f"**Linha-descritor (EN):** `{v['descriptor_en']}`", f"**Linha-descritor (DE):** `{v['descriptor_de'] or NA}`",
                  "**Regras de consistência:**", _bullets(v["rules_pt"])]
    parts.append("## Se a voz variar entre clipes\nAlternativa: gerar as falas em uma ferramenta de voz com uma voz fixa e sincronizar na edição. A decisão é sua.")
    return "\n\n".join(parts)


def doc07(bible: dict, plan: dict) -> str:
    a = plan["areas"]
    env_rows = "\n".join(f"| {_cell(e['name'])} | {_cell(e['explanation_pt'])} | `{env_path(e).as_posix()}` |" for e in plan["environments"])
    shot_rows = "\n".join(f"| {s['id']} | {_cell(s['shot_pt'])} |" for s in plan["scenes"])
    story_rows = "\n".join(f"| {s['id']} | {_cell(s['environment'])} | {_cell(', '.join(s['characters']) or 'nenhum')} | {_cell(s['explanation_pt'])} | `{scene_path(s['id']).as_posix()}` |"
                           for s in plan["scenes"])
    parts = [
        "## 1. Character Reference", "Fichas, descrições fixas e folhas 16:9 aprovadas: Documentos 04 e 05. As folhas são anexadas como referência em toda imagem de cena.",
        "## 2. Environment / Set Design", f"| Ambiente | Descrição | Arquivo |\n| --- | --- | --- |\n{env_rows}",
    ]
    for e in plan["environments"]:
        parts += [f"### {e['name']}", "**Descrição fixa:**", _code(e["fixed_description"]), "**Prompt:**", _code(environment_prompt(bible, e))]
    parts += [
        "## 3. Lighting / Mood / Style", a["lighting_mood_style_pt"], "**Bloco de estilo global:**", _code(bible["style_block"]),
        "## 4. Area Flow Plan / Camera Movement", a["area_flow_plan_pt"],
        "## 5. Camera Work / Shot Flow", a["camera_work_pt"], f"| Cena | Plano, ângulo e movimento |\n| --- | --- |\n{shot_rows}",
        "## 6. Shooting Technique", a["shooting_technique_pt"],
        "## 7. Storyboard / Sequence Flow", "Cada imagem mostra só o quadro inicial estável; o movimento fica no Documento 08.",
        f"| Cena | Ambiente | Personagens | O que mostra | Arquivo |\n| --- | --- | --- | --- | --- |\n{story_rows}",
    ]
    for s in plan["scenes"]:
        refs = [sheet_path(c).as_posix() for c in bible["characters"] if c["name"] in s["characters"]]
        env = next(e for e in plan["environments"] if e["name"] == s["environment"])
        refs.append(env_path(env).as_posix())
        parts += [f"### Cena {s['id']}", "**Referências anexadas:** " + ", ".join(f"`{r}`" for r in refs), "**Prompt:**", _code(scene_prompt(bible, plan, s))]
    total = len(plan["environments"]) + len(plan["scenes"])
    parts += ["## 8. Shooting Technique & Notes", a["notes_pt"],
              f"## O que você decide\nAo aprovar, o worker gera no ChatGPT {total} imagens ({len(plan['environments'])} de ambiente e {len(plan['scenes'])} de cena), nesta ordem."]
    return "\n\n".join(parts)


def image_catalog(bible: dict, plan: dict) -> dict[str, dict]:
    """Imagens aprovadas que um clipe pode usar como referência: id -> {label, path}. O Claude escolhe pelos ids."""
    out: dict[str, dict] = {}
    for s in plan["scenes"]:
        out[f"cena-{s['id']}"] = {"label": f"Imagem-base da cena {s['id']} ({s['environment']}; {', '.join(s['characters']) or 'sem personagens'})", "path": scene_path(s["id"])}
    for e in plan["environments"]:
        out[f"ambiente-{e['slug']}"] = {"label": f"Ambiente {e['name']}", "path": env_path(e)}
    for c in visible(bible):
        out[f"folha-{c['slug']}"] = {"label": f"Folha 16:9 de {c['name']}", "path": sheet_path(c)}
        out[f"personagem-{c['slug']}"] = {"label": f"{c['name']} de corpo inteiro", "path": body_path(c)}
    return out


def doc08(bible: dict, clips: dict, max_clip: int, catalog: dict | None = None) -> str:
    parts = [
        f"**Total:** {len(clips['clips'])} clipes · **Soma das durações:** {clips['total_s']} s (meta: acima de 70 s) · **Máximo por clipe usado no planejamento:** {max_clip} s.",
        "O limite real de duração e os controles disponíveis devem ser conferidos na interface do Grok no momento da geração.",
        "## Ordem de criação", clips["order_note_pt"] or "Na ordem numérica dos clipes.",
        "## Como gerar cada clipe", "1. Abrir o Grok no modo de vídeo a partir de imagem.\n2. Anexar a imagem inicial indicada.\n3. Colar o prompt exatamente como está.\n4. Conferir os itens da lista ao receber o clipe e baixar o arquivo original.",
    ]
    for c in clips["clips"]:
        refs = [f"`{(catalog[r]['path'] if catalog and r in catalog else Path(r)).as_posix()}`" for r in c.get("reference_images") or []]
        if c["start"] == "previous_last_frame":
            refs.insert(0, f"último quadro do clipe {c['n'] - 1:02d} (extraído automaticamente)")
        start = ", ".join(refs) if refs else f"`{scene_path(c['scene']).as_posix()}`"
        parts += [f"## Clipe {c['n']:02d} · cena {c['scene']}",
                  f"**Duração:** {c['duration_s']} s  \n**Imagens anexadas (nesta ordem; a primeira é o quadro inicial):** {start}  \n**Arquivo:** `{clip_path(c['n']).as_posix()}`",
                  f"**Fala (DE):** {c['dialogue_de'] or '—'}  \n**Tradução (PT):** {c['dialogue_pt'] or '—'}",
                  "**Prompt:**", _code(clip_prompt(bible, c)),
                  f"**Continuidade:** {c['continuity_pt'] or NA}", "**Conferir ao receber:**", _bullets(c["check_pt"])]
    parts.append(f"## O que você decide\nAo aprovar, o worker gera no Grok {len(clips['clips'])} clipes, um por vez, com exatamente os prompts acima.")
    return "\n\n".join(parts)
