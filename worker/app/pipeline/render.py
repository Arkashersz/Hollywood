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
def current_look(entry: dict) -> str:
    """Descrição fixa de um personagem/ambiente + os ajustes que o usuário aprovou nas imagens (`approved_changes`).
    Sem isso, os prompts de cena e de vídeo continuariam descrevendo a aparência ANTERIOR ao ajuste."""
    text = entry["fixed_description"]
    changes = [str(c).strip() for c in entry.get("approved_changes") or [] if str(c).strip()]
    if not changes:
        return text
    return (f"{text.rstrip()} USER-APPROVED CHANGES (part of the current look; they override any conflicting detail above): "
            + "; ".join(changes) + ".")


def full_body_prompt(bible: dict, ch: dict) -> str:
    return (f"{bible['style_block']}\n\nCharacter: {current_look(ch)}\n\n"
            "Full-body character reference image: neutral standing pose, front view, the whole figure visible from head to feet, "
            "plain light-gray seamless background, no other characters, no text. Vertical 9:16 format.")


def sheet_prompt(bible: dict, ch: dict, with_reference: bool = True) -> str:
    expressions = ", ".join(ch["expressions"]) or "neutral, happy, sad, angry, surprised, afraid"
    details = ", ".join(ch["details"]) or "outfit and accessories"
    return (f"{bible['style_block']}\n\nCharacter: {current_look(ch)}\n\n"
            "Character reference sheet in vertical 9:16 portrait format, laid out as clean stacked rows. "
            + ("Use the attached image as the exact identity reference: same face, body, outfit and colors in every view. " if with_reference else
               "Design this character exactly as described above, with the same face, body, outfit, colors and accessories in every view. ")
            +
            f"Include: full body front view and side profile, a face close-up, facial expressions ({expressions}), and detail callouts ({details}). "
            "Plain neutral background, clean layout, no text or labels.")


def environment_prompt(bible: dict, env: dict) -> str:
    return (f"{bible['style_block']}\n\nEnvironment: {current_look(env)}\n\n"
            "Establishing image of the empty environment: no people, no characters, no text. Vertical 9:16 format.")


def scene_prompt(bible: dict, plan: dict, scene: dict) -> str:
    chars = {c["name"]: c for c in bible["characters"]}
    env = next(e for e in plan["environments"] if e["name"] == scene["environment"])
    cast = "\n".join(f"- {n}: {current_look(chars[n])}" for n in scene["characters"]) or "- none"
    if scene.get("pre_action"):   # workflow de animação: a foto do segundo zero, antes de a ação começar
        return (f"{bible['style_block']}\n\nInitial still frame (second zero) for an image-to-video clip. Vertical 9:16 format.\n\n"
                f"Static pre-action image: {scene['composition']}\n\n"
                f"Characters (match the attached character sheets exactly, same outfit and colors):\n{cast}\n\n"
                f"Environment (match the attached environment image): {current_look(env)}\n\n"
                "Show only the exact moment before the action starts: zero motion, pre-action pose, no particles, no effects, no transformation yet, "
                "no motion blur, no subtitles, no captions, no text overlays.")
    return (f"{bible['style_block']}\n\nInitial still frame for an image-to-video clip. Vertical 9:16 format.\n\n"
            f"Composition: {scene['composition']}\n\n"
            f"Characters (match the attached character sheets exactly):\n{cast}\n\n"
            f"Environment (match the attached environment image): {current_look(env)}\n\n"
            "Show only this initial stable moment: no motion blur, no subtitles, no captions, no text overlays.")


def clip_prompt(bible: dict, clip: dict) -> str:
    """Prompt enviado ao Grok. Clipes planejados com linha do tempo recebem as travas; os antigos mantêm o formato de antes."""
    return locked_clip_prompt(bible, clip) if clip.get("timeline") else _legacy_clip_prompt(bible, clip)


def _t(seconds: float) -> str:
    return f"{float(seconds):.1f}"


def locked_clip_prompt(bible: dict, clip: dict) -> str:
    """Prompt com travas: animação segundo a segundo, câmera, falas com horário, silêncio de quem não fala, voz, visual e continuidade."""
    chars = {c["name"]: c for c in bible["characters"]}
    duration = float(clip["duration_s"])
    planned = min(float(clip.get("planned_s") or duration), duration)
    cast = [chars[n] for n in clip.get("cast") or [] if n in chars and not chars[n]["voice_only"]]
    on_screen = {c["name"] for c in cast}
    lines = clip.get("lines") or []
    speakers = list(dict.fromkeys(line["speaker"] for line in lines))
    parts = [f"Image-to-video clip, {clip['duration_s']} seconds, vertical 9:16. Start EXACTLY from the attached start image (same poses, positions and framing) "
             "and play it as ONE continuous shot: no cuts, no scene change, no new shot.",
             f"SCENE: {clip['prompt']}"]

    beats = [f"- {_t(b['start_s'])}–{_t(b['end_s'])} s: {b['action']}" for b in clip["timeline"]]
    if planned < duration - 0.05:   # o Grok só gera 6, 10 ou 15 s: o resto fica parado e é cortado na edição
        beats.append(f"- {_t(planned)}–{_t(duration)} s: everyone holds the final pose, still and silent; nothing new happens.")
    parts.append("ANIMATION LOCK (follow this timeline exactly, beat by beat; every movement happens only in its time range; nothing that is not written here happens):\n" + "\n".join(beats))
    parts.append(f"CAMERA LOCK: {clip.get('camera') or 'Locked-off static camera.'} No other camera movement, zoom, shake or cut.")

    if lines:
        def who(line: dict) -> str:
            gender = f" ({line['gender_en']})" if line.get("gender_en") else ""
            off = "" if line["speaker"] in on_screen else " (off-screen voice; no on-screen mouth moves)"
            return f"{line['speaker']}{gender}{off}"
        said = "\n".join(f"- {_t(line['start_s'])}–{_t(line['end_s'])} s · {who(line)}: \"{line['text']}\"" + (f" — delivery: {line['delivery']}" if line.get("delivery") else "")
                         for line in lines)
        rule = ("SPEAKING RULE: only the character whose line is playing moves the lips, in perfect lip sync, and only inside that time range. "
                "Every other character keeps the mouth CLOSED and stays silent, reacting only with eyes, eyebrows, head and body. "
                "Nobody speaks outside these ranges: no extra words, no off-screen voices, no humming, no background chatter.")
        silent = [c["name"] for c in cast if c["name"] not in speakers]
        if silent:
            rule += f" Silent for the whole clip, mouth closed: {', '.join(silent)}."
        parts.append(f"DIALOGUE LOCK (spoken in {clip.get('language_en') or 'German'}, word for word, in this order; nothing else is said):\n{said}\n{rule}")
    else:
        parts.append("DIALOGUE LOCK: this clip has NO dialogue. Every mouth stays closed: nobody speaks, whispers, sings or mumbles; only ambient sound.")

    voices = [chars[n] for n in speakers if n in chars]
    if voices:
        parts.append("VOICE LOCK (each voice is identical in every clip of this video and belongs only to its character; never swap or blend voices, "
                     "never change pitch, timbre, age, accent, energy or speed):\n" + "\n".join(f"- {c['name']}: {c['voice']['descriptor_en']}" for c in voices))
    if cast:
        parts.append("DESIGN LOCK (each character looks EXACTLY like the attached reference images in every frame, from the first to the last: same face, "
                     "body proportions, anatomy, hair, outfit, colors and accessories; nothing is added, removed, swapped or morphs):\n"
                     + "\n".join(f"- {c['name']}: {current_look(c)}" for c in cast))
    keep = [*(clip.get("keep") or []),
            f"Only these characters are in the shot: {', '.join(c['name'] for c in cast) or 'none'}. Nobody enters or leaves unless the timeline says so.",
            "Props keep their shape, size, color, position and the hand that holds them; nothing appears, disappears, duplicates or changes unless the timeline shows it.",
            "The environment stays exactly as in the start image (same layout, furniture, lighting and time of day); the background never morphs."]
    parts.append("CONTINUITY LOCK:\n" + "\n".join(f"- {k}" for k in keep))
    parts.append(f"SOUND: {clip.get('ambient') or 'Natural ambient sound of the scene.'} No background music.")
    parts.append("AVOID: subtitles, captions, on-screen text, logos, watermarks, extra characters, extra fingers or limbs, face or outfit changes, "
                 "sudden jumps, flicker, morphing, slow motion unless written above.")
    return "\n\n".join(parts)


def _legacy_clip_prompt(bible: dict, clip: dict) -> str:
    chars = {c["name"]: c for c in bible["characters"]}
    voices = "\n".join(f"- {n}: {chars[n]['voice']['descriptor_en']}" for n in clip["speakers"])
    voice_block = f"\n\nVoices (keep identical in every clip):\n{voices}" if voices else ""
    cast = [chars[n] for n in clip.get("cast") or [] if n in chars and not chars[n]["voice_only"]]
    look = ("\n\nCharacter consistency (each character must look EXACTLY like the attached reference images in every frame: same face, hair, outfit, colors and accessories "
            "such as glasses or hats; change nothing unless the action above says so):\n" + "\n".join(f"- {c['name']}: {current_look(c)}" for c in cast)) if cast else ""
    lines = clip.get("lines") or []   # workflow de animação: falas na ordem, com nome e gênero de quem fala
    talk = ""
    if lines:
        spoken = "\n".join(f"- {l['speaker']}{' (' + l['gender_en'] + ')' if l.get('gender_en') else ''}: \"{l['text']}\"" for l in lines)
        talk = f"\n\nDialogue (speak in {clip.get('language_en') or 'the original language'}, each line exactly as written, in this order):\n{spoken}"
    action = f"Animation & Camera Action: {clip['prompt']}" if clip.get("language_en") else clip["prompt"]
    return f"{action}{talk}{voice_block}{look}\n\nDuration: {clip['duration_s']} seconds. Vertical 9:16."


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
def doc04(bible: dict, direct: bool = False) -> str:
    parts = ["## Bloco de estilo global", "Colado, idêntico, em todos os prompts de imagem.", _code(bible["style_block"]), bible["style_explanation_pt"]]
    for ch in bible["characters"]:
        parts += [f"## {ch['name']}", f"**Papel:** {ch['role_pt'] or NA}"]
        if ch.get("reused"):
            parts.append("**Já existe (parte anterior):** descrição, voz e imagem reaproveitadas; este personagem não é gerado de novo.")
        elif ch.get("variant_of"):
            parts.append(f"**Variante de figurino de {ch['variant_of']}:** mesmo rosto, corpo e voz, com o visual novo abaixo; ganha folha própria.")
        elif ch.get("new_look"):
            parts.append("**Visual novo nesta parte:** mesmo personagem e mesma voz; a folha é gerada de novo com a descrição abaixo.")
        if ch["voice_only"]:
            parts.append("Personagem só de voz (sem imagem). Veja o Documento 06.")
            continue
        parts += ["**Descrição fixa** (imutável, colada em todo prompt em que aparece):", _code(current_look(ch)),
                  "**Nunca pode mudar:**", _bullets(ch["never_change_pt"])]
        if not direct:
            parts += [f"**Prompt da imagem de corpo inteiro** → `{body_path(ch).as_posix()}`", _code(full_body_prompt(bible, ch))]
    parts.append("## O que você decide\nAo aprovar, o worker gera no ChatGPT direto a folha 9:16 de cada personagem (Documento 05), sem imagem de corpo inteiro antes." if direct else
                 "## O que você decide\nAo aprovar, o worker gera no ChatGPT uma imagem de corpo inteiro por personagem, com exatamente os prompts acima.")
    return "\n\n".join(p for p in parts if p)


def doc05(bible: dict, direct: bool = False) -> str:
    parts = ["Uma folha 9:16 por personagem, gerada direto da descrição fixa (sem imagem de corpo inteiro antes)." if direct else
             "Uma folha 9:16 por personagem, gerada a partir da imagem de corpo inteiro aprovada (anexada como referência)."]
    for ch in visible(bible):
        parts += [f"## {ch['name']}", (f"**Arquivo:** `{sheet_path(ch).as_posix()}`" if direct else f"**Referência anexada:** `{body_path(ch).as_posix()}`  \n**Arquivo:** `{sheet_path(ch).as_posix()}`"),
                  "**A folha cobre:** corpo inteiro (frente e perfil), rosto em close, expressões e detalhes.",
                  "**Expressões:**", _bullets(ch["expressions"]), "**Detalhes:**", _bullets(ch["details"]),
                  "**Prompt:**", _code(sheet_prompt(bible, ch, not direct))]
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
        "## 1. Character Reference", "Fichas, descrições fixas e folhas 9:16 aprovadas: Documentos 04 e 05. As folhas são anexadas como referência em toda imagem de cena.",
        "## 2. Environment / Set Design", f"| Ambiente | Descrição | Arquivo |\n| --- | --- | --- |\n{env_rows}",
    ]
    for e in plan["environments"]:
        if e.get("reused"):
            parts.append("**Já existe (parte anterior):** a imagem deste ambiente é reaproveitada e não é gerada de novo.")
        parts += [f"### {e['name']}", "**Descrição fixa:**", _code(current_look(e)), "**Prompt:**", _code(environment_prompt(bible, e))]
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


def image_catalog(bible: dict, plan: dict, direct: bool = False) -> dict[str, dict]:
    """Imagens aprovadas que um clipe pode usar como referência: id -> {label, path}. O Claude escolhe pelos ids."""
    out: dict[str, dict] = {}
    for s in plan["scenes"]:
        out[f"cena-{s['id']}"] = {"label": f"Imagem-base da cena {s['id']} ({s['environment']}; {', '.join(s['characters']) or 'sem personagens'})", "path": scene_path(s["id"])}
    for e in plan["environments"]:
        out[f"ambiente-{e['slug']}"] = {"label": f"Ambiente {e['name']}", "path": env_path(e)}
    for c in visible(bible):
        out[f"folha-{c['slug']}"] = {"label": f"Folha 9:16 de {c['name']}", "path": sheet_path(c)}
        if not direct:
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
                  f"**Fala (DE):** {(c['dialogue_de'] or '—').replace(chr(10), '  ' + chr(10))}  \n**Tradução (PT):** {c['dialogue_pt'] or '—'}",
                  "**Prompt:**", _code(clip_prompt(bible, c)),
                  f"**Continuidade:** {c['continuity_pt'] or NA}", "**Conferir ao receber:**", _bullets(c["check_pt"])]
    parts.append(f"## O que você decide\nAo aprovar, o worker gera no Grok {len(clips['clips'])} clipes, um por vez, com exatamente os prompts acima.")
    return "\n\n".join(parts)
