"""Erros que pedem intervenção humana. O worker nunca tenta contornar nenhum deles."""
from __future__ import annotations


class Blocked(Exception):
    """A etapa parou e só continua depois de uma ação do usuário."""

    def __init__(self, code: str, message: str, action: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.action = action or ACTIONS.get(code, "")

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "action": self.action, "blocked": True}


ACTIONS = {
    "CHROME_NOT_FOUND": "Instale o Google Chrome ou defina CHROME_PATH em worker/.env.",
    "SESSION_REQUIRED": "No PC do worker, rode `python -m app.cli login`, entre na conta e depois clique em Tentar novamente.",
    "CHALLENGE": "Resolva a verificação de segurança na janela do Chrome do worker e clique em Tentar novamente.",
    "LIMIT_REACHED": "O serviço informou limite de uso. Aguarde a renovação da cota e clique em Tentar novamente; os itens já gerados são mantidos.",
    "REFUSED": "O serviço recusou o pedido. Use Pedir ajustes para alterar o texto/prompt e gerar de novo.",
    "UI_CHANGED": "A interface do serviço não respondeu como esperado. Rode `python -m app.cli check` e ajuste worker/selectors.json; como alternativa, gere o item manualmente e salve o arquivo no caminho indicado.",
    "TIMEOUT": "A geração ficou sem progresso. Verifique a janela do Chrome do worker e clique em Tentar novamente.",
    "DOWNLOAD_FAILED": "Não foi possível baixar o arquivo original. Baixe manualmente, salve no caminho indicado e clique em Tentar novamente.",
    "SOURCE_UNAVAILABLE": "Não foi possível baixar o vídeo. Salve o .mp4 no PC do worker e crie o projeto informando o caminho do arquivo.",
    "BAD_OUTPUT": "A resposta veio fora do formato esperado duas vezes. Clique em Tentar novamente.",
    "MISSING_INPUT": "Falta um arquivo de uma etapa anterior no PC do worker.",
}
