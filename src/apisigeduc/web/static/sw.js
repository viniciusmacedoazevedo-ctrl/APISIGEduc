// Service worker do APISIGEduc.
// Guarda só os arquivos fixos do app (estilo, ícones, página "sem conexão").
// As páginas com dados de alunos e notas NUNCA são guardadas no aparelho:
// vêm sempre do servidor, e sem conexão aparece a página "sem conexão".
const VERSAO = "apisigeduc-v4";
const FIXOS = [
  "/static/app.css",
  "/static/offline.html",
  "/static/icones/icone.svg",
  "/static/icones/icone-192.png",
  "/static/manifest.webmanifest",
];

self.addEventListener("install", (evento) => {
  evento.waitUntil(caches.open(VERSAO).then((cache) => cache.addAll(FIXOS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches.keys()
      .then((nomes) => Promise.all(nomes.filter((n) => n !== VERSAO).map((n) => caches.delete(n))))
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
        const rede = fetch(pedido).then((r) => { if (r.ok) cache.put(pedido, r.clone()); return r; }).catch(() => guardado);
        return guardado || rede;
      })
    );
    return;
  }
  if (pedido.mode === "navigate") {
    evento.respondWith(fetch(pedido).catch(() => caches.match("/static/offline.html")));
  }
});
