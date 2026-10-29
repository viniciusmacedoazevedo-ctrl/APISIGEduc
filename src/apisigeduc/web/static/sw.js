// Service worker do APISIGEduc.
// Guarda os arquivos fixos do app e, para lançar sem internet, as telas de chamada e
// de notas já abertas (lista da turma) e as fotos dos estudantes. Os lançamentos feitos
// sem internet ficam em offline.js até serem enviados. Sair da conta apaga tudo.
const VERSAO = "apisigeduc-v7";
const PAGINAS = "apisigeduc-paginas";
// telas que podem abrir sem internet: a turma (abas), a chamada e a grade de notas
const OFFLINE = [/^\/turmas\/\d+$/, /^\/turmas\/\d+\/chamada$/, /^\/notas\/\d+\/turmas\/\d+$/];
const FOTO = /^\/alunos\/[^/]+\/foto$/;
const FIXOS = [
  "/static/app.css",
  "/static/offline.html",
  "/static/icones/icone.svg",
  "/static/icones/icone-192.png",
  "/static/manifest.webmanifest",
  "/static/offline.js",
];

self.addEventListener("install", (evento) => {
  evento.waitUntil(caches.open(VERSAO).then((cache) => cache.addAll(FIXOS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches.keys()
      .then((nomes) => Promise.all(nomes.filter((n) => n !== VERSAO && n !== PAGINAS).map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (evento) => {
  const pedido = evento.request;
  if (pedido.method !== "GET") return;
  const url = new URL(pedido.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    // arquivos fixos: do aparelho, atualizando em segundo plano
    evento.respondWith(
      caches.open(VERSAO).then(async (cache) => {
        const guardado = await cache.match(pedido);
        const rede = fetch(pedido).then((r) => { if (r.ok) { const copia = r.clone(); cache.put(pedido, copia); } return r; }).catch(() => guardado);
        return guardado || rede;
      })
    );
    return;
  }
  if (FOTO.test(url.pathname)) {
    // foto: do aparelho se já guardada; senão da rede, guardando
    evento.respondWith(
      caches.open(PAGINAS).then(async (cache) => {
        const guardado = await cache.match(pedido);
        if (guardado) return guardado;
        const r = await fetch(pedido);
        if (r.ok) await cache.put(pedido, r.clone());
        return r;
      })
    );
    return;
  }
  if (pedido.mode === "navigate") {
    const podeGuardar = OFFLINE.some((p) => p.test(url.pathname));
    evento.respondWith(
      fetch(pedido)
        .then((r) => {
          // só telas de lançamento, e nunca o redirecionamento para o login; a cópia
          // é feita já, antes de a página consumir a resposta
          if (podeGuardar && r.ok && !r.redirected) {
            const copia = r.clone();
            evento.waitUntil(caches.open(PAGINAS).then((c) => c.put(url.pathname, copia)));
          }
          return r;
        })
        .catch(async () => (podeGuardar && (await caches.match(url.pathname))) || caches.match("/static/offline.html"))
    );
  }
});
