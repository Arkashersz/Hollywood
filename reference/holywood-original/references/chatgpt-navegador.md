# ChatGPT pelo navegador (Claude in Chrome)

Use para gerar as imagens de personagens, folhas, ambientes, cenas e capa.

Requisitos: Claude in Chrome conectado (`/chrome` com status "Enabled") e o usuário logado no ChatGPT neste Chrome. Se isso não for possível, entregue os prompts nos documentos e peça ao usuário que gere as imagens e as anexe (modo manual).

## Antes de começar

1. Verifique o status do Chrome. Abra uma **nova aba** para o ChatGPT; não reutilize abas do usuário. Ao terminar, feche as abas que você abriu.
2. Vá a chatgpt.com. Se aparecer login, verificação em duas etapas, CAPTCHA ou aviso de segurança, **pare** e peça ao usuário para resolver na janela do Chrome; depois continue. Nunca digite senhas nem tente passar por verificações.
3. Peça confirmação **uma vez por lote**: "Vou enviar N prompts ao ChatGPT (cada geração gasta a cota do seu plano). Posso?". Só envie depois do sim.

## Rotina por imagem

1. Chats: um chat novo por personagem (a folha 16:9 continua no mesmo chat); um chat por ambiente ou um só para os ambientes, para manter o estilo; um chat por cena, anexando as folhas dos personagens e a imagem do ambiente como referência (upload de arquivo pela extensão, até 10 MB por envio).
2. Cole o prompt do documento **exatamente como está**, sem reescrever, e envie.
3. Aguarde, acompanhando por capturas de tela, até a imagem aparecer completa (sem indicador de geração). Se nada mudar por cerca de 3 minutos, avise o usuário.
4. Capture a imagem: captura de tela com `save_to_disk` em `imagens/_previews/<nome>_vN.png`. Use zoom nos detalhes (rosto, mãos, texto) e compare com a descrição fixa; liste as divergências.
5. Mostre ao usuário: caminhos das prévias, o pedido para olhar também a aba do ChatGPT, e atualize o `.html` do documento com as prévias. Pergunte se aprova ou o que ajustar.
6. Ajustes: peça no mesmo chat, com mudanças pontuais; repita a descrição fixa se o ChatGPT começar a se desviar.
7. Imagem aprovada: peça permissão para baixar; baixe a imagem original (não a captura de tela) pela interface do ChatGPT. Ela cai na pasta de Downloads (veja `config.md`). Mova e renomeie para `imagens/<tipo>/<nome-padrão>.png`, confirme que o arquivo existe e abre, e registre no `estado.md`.

## Limites e falhas

- **Limite de uso ou cota:** pare. Registre no `estado.md` o que já foi gerado e o que falta, diga ao usuário o que o ChatGPT informou sobre quando volta (se informou) e ofereça continuar depois ou em modo manual.
- **Recusa por política de conteúdo:** ajuste o prompt uma vez para respeitar a política (descrição mais neutra). Se persistir, pare e informe. Não tente burlar filtros.
- Não altere configurações da conta, não aceite termos nem pop-ups além de fechar avisos inofensivos, não abra nem resuma outros chats do usuário, não use GPTs ou plugins externos.
- Se a interface mudar e você não achar um controle, descreva o que vê e pergunte. Não repita cliques às cegas: após 2 ou 3 falhas na mesma ação, pare.
- Capturas de tela podem mostrar dados pessoais da conta. Guarde só as imagens geradas (use zoom na área da imagem quando possível).
