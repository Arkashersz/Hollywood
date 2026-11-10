# Navegador existente — ChatGPT e Grok

O Claude Code opera o Chrome existente por meio da extensão Claude in Chrome. Não criar perfil novo e não automatizar credenciais.

## Serviços
- Imagens, character sheets, ambientes, imagens-base e capas: ChatGPT.
- Vídeos e image-to-video: Grok.
- Flow está descontinuado neste workflow.

## Preparação
1. Confirmar extensão conectada e Chrome habilitado no Claude Code.
2. Usar abas novas para os serviços, sem inspecionar conversas não relacionadas.
3. Login, MFA e CAPTCHA são sempre responsabilidade do usuário.
4. Antes de cada lote, mostrar número de gerações, serviço e referências; esperar autorização explícita.

## Rotina por item
1. Abrir o serviço correspondente no Chrome existente.
2. Conferir que a conta está autenticada e que não há bloqueio ou limite.
3. Anexar somente as referências pertencentes ao projeto.
4. Enviar o prompt aprovado exatamente como está no pacote.
5. Aguardar a geração terminar. Se ficar sem progresso por aproximadamente 3 minutos, parar e informar.
6. Baixar o arquivo original quando disponível; não usar captura de tela como entrega final.
7. Salvar no destino indicado, verificar que o arquivo existe e abre, e registrar no `result.json`.

## Regras de interrupção
- Login, CAPTCHA, MFA, aviso de segurança, limite de uso, recusa ou alteração relevante de interface: parar e pedir intervenção.
- Duas tentativas malsucedidas na mesma ação: parar e registrar diagnóstico.
- Não clicar às cegas, não contornar controles e não automatizar publicação no TikTok.

## Grok
Validar na interface o formato, duração, disponibilidade de referência e controles no momento do uso. Não assumir limites fixos. Se o Grok não oferecer download ou a automação não for confiável, deixar o item aguardando download manual.
