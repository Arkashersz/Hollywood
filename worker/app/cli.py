"""Comandos de preparação e diagnóstico do worker.

  python -m app.cli login      abre o Chrome do worker para você entrar no ChatGPT e no Grok
  python -m app.cli check      confere sessão e seletores dos dois serviços (não envia nada)
  python -m app.cli analyze X  roda só a análise local de um link ou .mp4 (sem navegador, sem Supabase)
  python -m app.cli local X    roda o pipeline inteiro sem Supabase, pedindo aprovação no terminal
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import config, media
from .brain import ClaudeBrain
from .browser import selectors
from .browser.chatgpt import ChatGPT
from .browser.grok import Grok
from .browser.session import Browser, open_login_window
from .errors import Blocked
from .pipeline import stages
from .pipeline.core import Ctx, LocalSink


def cmd_login(_args) -> int:
    open_login_window([config.CHATGPT_URL, config.GROK_URL])
    print("O Chrome do worker foi aberto com um perfil próprio em:", config.BROWSER_PROFILE_DIR)
    print("1. Entre na sua conta do ChatGPT e na do Grok (login, MFA e CAPTCHA são sempre feitos por você).")
    print("2. Feche TODAS as janelas desse Chrome quando terminar.")
    print("3. Rode `python -m app.cli check` para conferir.")
    return 0


async def _check() -> int:
    sel = selectors.load(config.SELECTORS_FILE)
    browser = Browser()
    ok_all = True
    claude = ClaudeBrain(config.RUNTIME_DIR).executable
    print(f"Claude (cérebro): {'encontrado em ' + claude if claude else 'NÃO ENCONTRADO — instale o Claude Code e faça login com `claude`'}")
    ok_all &= bool(claude) or config.BRAIN == "chatgpt"
    try:
        for key, client in (("chatgpt", ChatGPT(browser, sel)), ("grok", Grok(browser, sel))):
            ok, message = await client.check_session()
            ok_all &= ok
            print(f"\n{client.service}: {'OK' if ok else 'ATENÇÃO'} — {message}")
            page = await browser.page(key)
            print(f"  página: {page.url}")
            for name, selector in sel[key].items():
                if not selector:
                    print(f"  - {name:<11} (não definido)")
                    continue
                try:
                    loc = page.locator(selector)
                    total = await loc.count()
                    shown = sum([await loc.nth(i).is_visible() for i in range(min(total, 40))])
                    count = f"{total} ({shown} visíveis)"
                except Exception as exc:  # noqa: BLE001
                    count = f"seletor inválido: {exc}"
                # stop/turn/assistant só existem durante ou depois de uma conversa; login precisa ter 0 visíveis
                expected = {"composer": "1 visível", "file_input": ">=1 (oculto é normal)", "send": ">=1 (pode surgir só depois de digitar)",
                            "login": "0 visíveis", "video_mode": "1 visível"}.get(name, "só aparece em uso")
                print(f"  - {name:<11} encontrados: {count:<16} esperado: {expected}")
    except Blocked as exc:
        print(f"\nParado ({exc.code}): {exc.message}\n→ {exc.action}")
        ok_all = False
    finally:
        await browser.close()
    print("\nNenhuma mensagem foi enviada aos serviços. Seletores podem ser ajustados em", config.SELECTORS_FILE)
    return 0 if ok_all else 1


def cmd_check(_args) -> int:
    return asyncio.run(_check())


def cmd_analyze(args) -> int:
    out = Path(args.out) if args.out else config.RUNTIME_DIR / "analises" / media.slugify(args.source)
    try:
        evidence = media.analyze(args.source, out, whisper_model=config.WHISPER_MODEL, language=config.REFERENCE_LANGUAGE,
                                 do_transcribe=not args.no_transcribe)
    except Blocked as exc:
        print(f"Parado ({exc.code}): {exc.message}\n→ {exc.action}")
        return 1
    print(json.dumps({k: evidence[k] for k in ("probe", "metadata", "cut_candidates", "sheets_1s", "sheets_cuts")}, ensure_ascii=False, indent=2))
    print("Arquivos em:", out)
    return 0


async def _local(args) -> int:
    project_dir = config.RUNTIME_DIR / "local" / media.slugify(args.source)
    sel = selectors.load(config.SELECTORS_FILE)
    browser = Browser()
    images = ChatGPT(browser, sel)
    ctx = Ctx(project_id="local", source=args.source, dir=project_dir, sink=LocalSink(echo=True),
              brain=images if config.BRAIN == "chatgpt" else ClaudeBrain(project_dir), images=images,
              video=Grok(browser, sel), max_clip=config.GROK_MAX_CLIP_SECONDS, whisper_model=config.WHISPER_MODEL,
              language=config.REFERENCE_LANGUAGE, pause=lambda: asyncio.sleep(config.PAUSE_BETWEEN_ITEMS))
    print("Pasta do projeto:", project_dir)
    try:
        for stage_key in stages.ORDER:
            ctx.stage_key, ctx.feedback, ctx.version = stage_key, None, 1
            while True:
                try:
                    result = await stages.run_stage(ctx)
                except Blocked as exc:
                    print(f"\nParado ({exc.code}): {exc.message}\n→ {exc.action}")
                    if input("Tentar de novo esta etapa? [s/N] ").strip().lower() != "s":
                        return 1
                    continue
                print(f"\n== {stage_key}: {result.get('summary', '')}")
                for warning in result.get("warnings", []):
                    print("   aviso:", warning)
                nxt = result.get("next_generation")
                if nxt:
                    print(f"   Aprovar autoriza gerar {nxt['count']} {nxt['unit']} no {nxt['service']}.")
                answer = input("Revise os arquivos na pasta. [a]provar, [r]evisar com comentário, [p]arar: ").strip().lower()
                if answer == "a":
                    break
                if answer == "r":
                    ctx.feedback, ctx.version = input("Ajuste desejado: ").strip() or None, ctx.version + 1
                    continue
                return 0
    finally:
        await browser.close()
    print("\nPipeline concluído. Arquivos em:", project_dir)
    return 0


def cmd_local(args) -> int:
    return asyncio.run(_local(args))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="TikTok Studio — preparação e diagnóstico do worker")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="abre o Chrome do worker para login manual").set_defaults(fn=cmd_login)
    sub.add_parser("check", help="confere sessão e seletores (não envia mensagens)").set_defaults(fn=cmd_check)
    analyze = sub.add_parser("analyze", help="análise local de um link ou .mp4")
    analyze.add_argument("source")
    analyze.add_argument("--out")
    analyze.add_argument("--no-transcribe", action="store_true")
    analyze.set_defaults(fn=cmd_analyze)
    local = sub.add_parser("local", help="pipeline completo sem Supabase, com aprovação no terminal")
    local.add_argument("source")
    local.set_defaults(fn=cmd_local)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
