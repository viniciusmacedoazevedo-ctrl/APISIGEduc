// APISIGEduc sem internet: guarda no aparelho só os lançamentos ainda não enviados
// (chamada e notas) e os envia quando a conexão volta. Depois de enviados, apaga.
// Cada pendente é do professor que o fez: só é enviado com a sessão dele.
(() => {
  const BANCO = "apisigeduc", LOJA = "pendentes";
  const usuario = document.body.dataset.usuario;
  if (!usuario || !("indexedDB" in window)) return;

  const abrir = () => new Promise((ok, erro) => {
    const r = indexedDB.open(BANCO, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(LOJA, { keyPath: "id", autoIncrement: true });
    r.onsuccess = () => ok(r.result);
    r.onerror = () => erro(r.error);
  });
  const transacao = async (modo, fn) => {
    const db = await abrir();
    return new Promise((ok, erro) => {
      const t = db.transaction(LOJA, modo);
      const resultado = fn(t.objectStore(LOJA));
      t.oncomplete = () => ok(resultado && resultado.result);
      t.onerror = () => erro(t.error);
    });
  };
  const todos = () => transacao("readonly", (s) => s.getAll());
  const guardar = (p) => transacao("readwrite", (s) => s.add(p));
  const apagar = (id) => transacao("readwrite", (s) => s.delete(id));
  const limpar = () => transacao("readwrite", (s) => s.clear());

  // aviso fixo com a quantidade de pendentes
  const faixa = document.createElement("div");
  faixa.className = "faixa-offline";
  faixa.setAttribute("role", "status");
  faixa.hidden = true;
  document.body.appendChild(faixa);
  const mostrar = (texto, botao) => {
    faixa.replaceChildren(document.createTextNode(texto));
    if (botao) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "botao pequeno"; b.textContent = botao;
      b.onclick = () => enviarPendentes(true);
      faixa.appendChild(b);
    }
    faixa.hidden = !texto;
  };
  const meus = async () => (await todos()).filter((p) => p.usuario === usuario);
  const atualizarFaixa = async () => {
    const n = (await meus()).length;
    mostrar(n ? `${n} lançamento(s) guardado(s) no aparelho, aguardando internet.` : "", n ? "Enviar agora" : null);
  };

  const conectado = async () => {
    try {
      const r = await fetch("/saude", { cache: "no-store", signal: AbortSignal.timeout(5000) });
      return r.ok;
    } catch { return false; }
  };

  let enviando = false;
  async function enviarPendentes(manual) {
    if (enviando) return;
    const lista = await meus();
    if (!lista.length || !(await conectado())) { if (manual) mostrar(`Ainda sem internet. ${lista.length} lançamento(s) no aparelho.`, "Tentar de novo"); return; }
    enviando = true;
    let enviados = 0, sessao = true;
    const csrf = document.querySelector("input[name=csrf_token]");
    for (const p of lista) {
      const corpo = new URLSearchParams();
      p.campos.forEach(([k, v]) => corpo.append(k, k === "csrf_token" && csrf ? csrf.value : v));
      try {
        const r = await fetch(p.url, { method: "POST", body: corpo, credentials: "same-origin" });
        if (r.url.includes("/login")) { sessao = false; break; }
        if (r.ok) { await apagar(p.id); enviados++; }
      } catch { break; }
    }
    enviando = false;
    await atualizarFaixa();
    if (!sessao) mostrar("Entre de novo na sua conta para enviar os lançamentos guardados no aparelho.");
    else if (enviados) {
      mostrar(`${enviados} lançamento(s) feito(s) sem internet foram enviados.`);
      setTimeout(atualizarFaixa, 6000);
    }
  }

  // formulários que funcionam sem internet: <form data-offline="descrição">
  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!form.dataset.offline || form.dataset.enviandoAgora) return;
    e.preventDefault();
    if (await conectado()) {
      form.dataset.enviandoAgora = "1";
      form.submit();  // caminho normal, com a mensagem do servidor
      return;
    }
    const campos = [...new FormData(form).entries()].filter(([, v]) => typeof v === "string");
    await guardar({ usuario, url: form.action, campos, descricao: form.dataset.offline, criado_em: new Date().toISOString() });
    window.onbeforeunload = null;
    form.dispatchEvent(new CustomEvent("guardado-offline"));
    document.querySelectorAll("button[data-texto]").forEach((b) => { b.disabled = false; b.textContent = b.dataset.texto; delete b.dataset.texto; });
    await atualizarFaixa();
    mostrar(`Sem internet: "${form.dataset.offline}" ficou guardado no aparelho e será enviado quando a conexão voltar.`, "Enviar agora");
  });

  // sair da conta apaga o que está no aparelho (avisa se ainda há pendentes)
  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!form.action.endsWith("/sair") || form.dataset.saindo) return;
    e.preventDefault();
    const n = (await meus()).length;
    if (n && !confirm(`Há ${n} lançamento(s) feitos sem internet que ainda não foram enviados. Sair apaga esses lançamentos do aparelho. Sair mesmo assim?`)) return;
    await limpar();
    if ("caches" in window) await caches.delete("apisigeduc-paginas");
    form.dataset.saindo = "1";
    form.submit();
  });

  window.addEventListener("online", () => enviarPendentes(false));
  setInterval(() => enviarPendentes(false), 60000);
  atualizarFaixa().then(() => enviarPendentes(false));
})();
