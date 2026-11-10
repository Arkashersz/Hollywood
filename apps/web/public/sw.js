// Service worker mínimo: torna o painel instalável. Não guarda nada em cache de propósito,
// para que o painel nunca mostre uma versão antiga nem dados do Supabase desatualizados.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
