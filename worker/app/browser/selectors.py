"""Seletores das interfaces. Ficam em um só lugar porque os sites mudam sem aviso.

Para corrigir sem mexer no código, crie `worker/selectors.json` com o mesmo formato
(ex.: {"chatgpt": {"send": "button[aria-label='Enviar']"}}) e rode `python -m app.cli check`.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULTS: dict[str, dict[str, str]] = {
    "chatgpt": {
        "composer": "#prompt-textarea, div[contenteditable='true'][role='textbox'], textarea[placeholder]",
        "send": "[data-testid='send-button'], button[aria-label*='Send' i], button[aria-label*='Enviar' i], button[aria-label*='Senden' i]",
        "stop": "[data-testid='stop-button'], button[aria-label*='Stop' i], button[aria-label*='Interromper' i], button[aria-label^='Parar' i]",
        "file_input": "input[type='file']",
        "turn": "article[data-testid^='conversation-turn'], section[data-testid^='conversation-turn'], [data-testid^='conversation-turn-'], [data-chatgpt-search-message-ids]",
        "assistant": "[data-message-author-role='assistant']",
        "user": "[data-message-author-role='user'], [data-user-message-bubble='true']",
        # Só é usado pelo Python (aceita :text-is do Playwright). Conferido na página sem login em 09/11/2026.
        "login": "[data-testid='login-button'], a[href*='/auth/login'], button:text-is('Entrar'), button:text-is('Log in'), "
                 "button:text-is('Anmelden'), button:text-is('Cadastre-se grátis'), button:text-is('Sign up for free')",
        "alert": "[role='alert'], [data-testid*='toast']",
    },
    "grok": {
        "composer": "textarea, div[contenteditable='true']",
        "send": "button[type='submit'], button[aria-label*='Submit' i], button[aria-label*='Send' i], button[aria-label*='Enviar' i]",
        # Só o campo de anexo da caixa de criação: a página tem outros (cartões "Editar texto", "Edição Precisa"…) que NÃO servem.
        "file_input": "form input[type='file']",
        # Miniatura que aparece na caixa de criação depois de anexar a imagem inicial.
        "attachment": "form img",
        "login": "a[href*='sign-in'], a[href*='/login'], a[href*='accounts.x.ai']",
        "video": "video",
        "download": "button[aria-label*='Download' i], a[download], button[aria-label*='Baixar' i], button[aria-label*='Transferir' i]",
        "alert": "[role='alert'], [data-sonner-toast]",
        # Botão que coloca a caixa em modo vídeo (clicado antes de anexar; vazio = não clicar).
        "video_mode": "button[aria-label='Vídeo'], button[aria-label='Video']",
        # Opções do modo vídeo (conferidas na conta em 11/11/2026): botões que abrem menus de itens `menuitemradio`.
        "resolution_button": "button[aria-label='Resolução do vídeo'], button[aria-label='Video resolution']",
        "duration_button": "button[aria-label='Duração do vídeo'], button[aria-label='Video duration']",
        "aspect_button": "button[aria-label='Aspecto de proporção'], button[aria-label='Aspect ratio']",
        "menu_option": "[role='menuitemradio']",
    },
}


def load(path: Path | None = None) -> dict[str, dict[str, str]]:
    merged = {service: dict(values) for service, values in DEFAULTS.items()}
    if path and path.is_file():
        overrides = json.loads(path.read_text(encoding="utf-8"))
        for service, values in overrides.items():
            if service in merged and isinstance(values, dict):
                merged[service].update({k: str(v) for k, v in values.items()})
    return merged
