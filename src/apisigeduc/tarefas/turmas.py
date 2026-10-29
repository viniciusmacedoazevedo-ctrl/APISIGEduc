"""Leitura das turmas e estudantes no Portal do Professor do SIGEduc.

Caminho: Módulos -> Portal do Professor -> tabela "Minhas Turmas" -> clicar no
código da turma (abre a Escola Digital) -> menu Turmas > Estudantes -> cartões
com o nome e "Matrícula: 000000".

A tabela de turmas é encontrada pelos nomes das colunas, não pela posição, para
resistir a mudanças de layout. Os nomes aceitos estão em COLUNAS_*.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .. import seletores as S
from ..cliente import SigeducCliente
from ..modelos import CODIGO_TURMA, Estudante, TurmaPortal
from .base import Relatorio

log = logging.getLogger(__name__)

# campo -> trechos aceitos no cabeçalho (sem acento, minúsculo), em ordem de prioridade
COLUNAS_TURMAS = {
    "codigo": ["turma"],
    "escola": ["escola", "unidade"],
    "etapa": ["etapa", "nivel", "modalidade"],
    "ano_serie": ["ano/serie", "serie", "ano"],
    "componente": ["componente", "disciplina"],
    "qtd_estudantes": ["estudantes", "alunos", "qtd", "quantidade", "matriculados"],
}
COLUNAS_ESTUDANTES = {
    "matricula": ["matricula"],
    "nome": ["nome", "estudante", "aluno"],
}

# Todas as tabelas da página, linha a linha. Células mescladas (colspan) são
# repetidas para os índices dos cabeçalhos baterem com os das linhas.
_JS_TABELAS = """
() => Array.from(document.querySelectorAll('table')).map((tabela) =>
  Array.from(tabela.rows).map((tr) => {
    const celulas = [];
    for (const c of tr.cells) {
      const texto = (c.innerText || c.textContent || '').replace(/\\s+/g, ' ').trim();
      for (let i = 0; i < (c.colSpan || 1); i++) celulas.push(texto);
    }
    return celulas;
  })
)
"""

# Cartões de estudante: cada elemento mais interno com "Matrícula: 123" e, subindo,
# o maior bloco que contém só essa matrícula (o cartão). O nome é a primeira linha
# do cartão que não é um rótulo (Matrícula, Usuário, E-mail).
#
# Foto: as imagens só carregam quando aparecem na tela (ou ficam em data-src), por
# isso cada cartão é rolado até a tela e as imagens recebem o endereço real antes de
# esperar o carregamento. A maior imagem do cartão (a outra é o ícone pequeno ao lado
# do nome) é redesenhada em JPEG pequeno pelo próprio navegador ("dados"); se o
# navegador não permitir, fica só o endereço para baixar depois ("foto").
_JS_CARTOES = """
async (lado) => {
  const padrao = /Matr[ií]cula\\s*:?\\s*(\\d{4,})/i;
  const contar = (t) => (t.match(/Matr[ií]cula\\s*:?\\s*\\d{4,}/gi) || []).length;
  const esperar = (ms) => new Promise((r) => setTimeout(r, ms));
  const internos = Array.from(document.body.querySelectorAll('*')).filter((el) =>
    padrao.test(el.innerText || '') &&
    !Array.from(el.children).some((f) => padrao.test(f.innerText || '')));
  const cartoes = internos.map((el) => {
    let cartao = el;
    while (cartao.parentElement && contar(cartao.parentElement.innerText || '') === 1) cartao = cartao.parentElement;
    return { el, cartao };
  });
  const enderecoReal = (img) => {
    for (const a of ['data-src', 'data-original', 'data-lazy-src', 'data-url', 'lazy-src']) {
      const v = img.getAttribute(a);
      if (v && !/^data:/.test(v)) return v;
    }
    return '';
  };
  const imagens = [];
  for (const { cartao } of cartoes) {
    cartao.scrollIntoView({ block: 'center' });
    for (const img of cartao.querySelectorAll('img')) {
      img.loading = 'eager';
      const real = enderecoReal(img);
      if (real && img.getAttribute('src') !== real) img.setAttribute('src', real);
      imagens.push(img);
    }
    await esperar(20);
  }
  const carregada = (img) => img.complete && img.naturalWidth > 0;
  const prazo = Date.now() + 20000;
  while (Date.now() < prazo && imagens.some((img) => img.getAttribute('src') && !img.complete)) await esperar(200);
  window.scrollTo(0, 0);

  const fundo = (cartao) => {
    for (const el of [cartao, ...cartao.querySelectorAll('*')]) {
      const m = getComputedStyle(el).backgroundImage.match(/url\\(["']?(https?:[^"')]+)/);
      const r = el.getBoundingClientRect();
      if (m && r.width >= 30 && r.height >= 30) return m[1];
    }
    return '';
  };
  return cartoes.map(({ el, cartao }) => {
    const linhas = (cartao.innerText || '').split('\\n').map((l) => l.replace(/^[\\s•·●○◦-]+/, '').trim()).filter(Boolean);
    const nome = linhas.find((l) => !/^(matr[ií]cula|usu[aá]rio|e-?mail)\\b/i.test(l)) || '';
    const matricula = (el.innerText.match(padrao) || [])[1] || '';
    const todas = Array.from(cartao.querySelectorAll('img'));
    const candidatas = todas.filter((img) => carregada(img) && img.naturalWidth >= 30 && img.naturalHeight >= 30)
      .map((img) => {
        const r = img.getBoundingClientRect();
        return { img, area: (r.width * r.height) || (img.naturalWidth * img.naturalHeight) };
      }).sort((a, b) => b.area - a.area);
    let foto = '', dados = '', situacao = 'sem imagem';
    if (candidatas.length && candidatas[0].area >= 900) {
      const img = candidatas[0].img;
      foto = img.currentSrc || img.src || '';
      situacao = 'ok';
      try {
        const escala = Math.min(1, lado / Math.max(img.naturalWidth, img.naturalHeight));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(img.naturalWidth * escala));
        canvas.height = Math.max(1, Math.round(img.naturalHeight * escala));
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        dados = canvas.toDataURL('image/jpeg', 0.85);
      } catch (e) { dados = ''; }  // imagem de outro endereço: baixar pelo Python
    } else if (todas.some((img) => img.getAttribute('src') && img.complete && !img.naturalWidth)) {
      situacao = 'imagem quebrada';
    } else {
      foto = fundo(cartao);
      if (foto) situacao = 'ok';
    }
    if (!/^https?:/.test(foto)) foto = '';
    return { matricula, nome, foto, dados, situacao };
  });
}
"""


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def mapear_colunas(cabecalhos: list[str], colunas: dict[str, list[str]]) -> dict[str, int]:
    """Associa cada campo ao índice da coluna cujo cabeçalho contém um dos trechos."""
    normalizados = [_normalizar(c) for c in cabecalhos]
    mapa: dict[str, int] = {}
    for campo, trechos in colunas.items():
        for trecho in trechos:
            indice = next(
                (i for i, c in enumerate(normalizados) if trecho in c and i not in mapa.values()), None
            )
            if indice is not None:
                mapa[campo] = indice
                break
    return mapa


def achar_tabela(tabelas: list[list[list[str]]], colunas: dict[str, list[str]], obrigatorias: set[str]):
    """Primeira tabela com uma linha de cabeçalho que tem as colunas obrigatórias.

    Devolve (mapa campo->índice, linhas de dados abaixo do cabeçalho).
    """
    for linhas in tabelas:
        for i, cabecalho in enumerate(linhas[:6]):
            mapa = mapear_colunas(cabecalho, colunas)
            if obrigatorias <= mapa.keys():
                return mapa, [linha for linha in linhas[i + 1 :] if len(linha) >= 2]
    vistos = [linhas[0] for linhas in tabelas if linhas and any(linhas[0])]
    raise LookupError(f"Tabela com as colunas {sorted(obrigatorias)} não encontrada. Cabeçalhos na página: {vistos}")


def _celula(linha: list[str], mapa: dict[str, int], campo: str) -> str:
    i = mapa.get(campo)
    return linha[i] if i is not None and i < len(linha) else ""


def _inteiro(texto: str) -> int | None:
    achado = re.search(r"\d+", texto)
    return int(achado.group()) if achado else None


def turmas_da_tabela(tabelas: list[list[list[str]]]) -> list[TurmaPortal]:
    mapa, linhas = achar_tabela(tabelas, COLUNAS_TURMAS, {"codigo", "componente"})
    turmas = []
    for linha in linhas:
        # o código (ex.: EFV6A) fica na coluna Turma; se o ícone ocupar essa
        # posição, procura no restante da linha
        achado = CODIGO_TURMA.search(_celula(linha, mapa, "codigo").upper()) or next(
            (m for m in (CODIGO_TURMA.search(c.upper()) for c in linha) if m), None
        )
        if not achado:
            continue
        codigo = achado.group(0)
        turmas.append(
            TurmaPortal(
                codigo=codigo,
                escola=_celula(linha, mapa, "escola"),
                etapa=_celula(linha, mapa, "etapa"),
                ano_serie=_celula(linha, mapa, "ano_serie"),
                componente=_celula(linha, mapa, "componente"),
                qtd_estudantes=_inteiro(_celula(linha, mapa, "qtd_estudantes")),
            )
        )
    return turmas


def _foto_dos_dados(dados_url: str) -> bytes | None:
    """Imagem que o navegador devolveu como data:image/jpeg;base64,..."""
    import base64

    if not dados_url or "," not in dados_url:
        return None
    try:
        dados = base64.b64decode(dados_url.split(",", 1)[1], validate=True)
    except ValueError:
        return None
    return dados if _tipo_imagem(dados) and len(dados) <= FOTO_TAMANHO_MAXIMO else None


def estudantes_dos_cartoes(cartoes: list[dict]) -> list[Estudante]:
    vistos: dict[str, Estudante] = {}
    for cartao in cartoes:
        nome = re.sub(r"\s+", " ", cartao.get("nome", "")).strip()
        matricula = cartao.get("matricula", "")
        if matricula and nome and matricula not in vistos:
            foto = _foto_dos_dados(cartao.get("dados", "") or "")
            vistos[matricula] = Estudante(
                matricula=matricula, nome=nome, foto_url=cartao.get("foto", "") or "",
                foto=foto, foto_tipo=_tipo_imagem(foto) if foto else "",
            )
    return list(vistos.values())


# lado maior da foto redesenhada no navegador (px) e limites de tamanho
FOTO_LADO = 320
FOTO_TAMANHO_MAXIMO = 3 * 1024 * 1024


def impressao(dados: bytes) -> str:
    import hashlib

    return hashlib.sha256(dados).hexdigest()


def descartar_fotos_padrao(estudantes: list[Estudante]) -> list[Estudante]:
    """Remove a imagem genérica (silhueta) que o SIGEduc mostra para quem não tem foto.
    Uma foto real é de um único estudante; a mesma imagem para duas matrículas
    diferentes é a silhueta."""
    from dataclasses import replace

    donos: dict[str, set[str]] = {}
    for e in estudantes:
        if e.foto:
            donos.setdefault(impressao(e.foto), set()).add(e.matricula)
    genericas = {h for h, matriculas in donos.items() if len(matriculas) >= 2}
    return [replace(e, foto=None, foto_tipo="") if e.foto and impressao(e.foto) in genericas else e for e in estudantes]


def estudantes_da_tabela(tabelas: list[list[list[str]]]) -> list[Estudante]:
    mapa, linhas = achar_tabela(tabelas, COLUNAS_ESTUDANTES, {"matricula", "nome"})
    estudantes = []
    for linha in linhas:
        matricula = _celula(linha, mapa, "matricula")
        nome = _celula(linha, mapa, "nome")
        if re.search(r"\d", matricula) and nome:
            estudantes.append(Estudante(matricula=matricula, nome=nome))
    return estudantes


def _tipo_imagem(dados: bytes) -> str:
    if dados[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if dados[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if dados[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        return "image/webp"
    return ""


class LeitorPortal:
    def __init__(self, cliente: SigeducCliente):
        self.cliente = cliente
        self.page = cliente.page
        self.fotos: dict[str, str] = {}  # matrícula -> com_foto, silhueta, sem_imagem, quebrada, nao_baixada
        self.silhuetas: set[str] = set()  # impressões da imagem padrão já reconhecidas
        self._url_portal: str | None = None

    def tabelas(self) -> list[list[list[str]]]:
        return self.page.evaluate(_JS_TABELAS)

    def _esperar(self, pagina, seletor: str, descricao: str) -> None:
        """Espera o conteúdo esperado aparecer (o SIGEduc pode demorar a responder)."""
        try:
            pagina.locator(seletor).first.wait_for(state="attached")
        except PlaywrightTimeoutError:
            raise LookupError(f"{descricao} não apareceu na tela ({pagina.url})") from None

    def _no_portal(self) -> bool:
        return self.page.locator(S.PORTAL_TABELA_TURMAS).count() > 0

    def abrir_portal(self) -> None:
        if self._no_portal():
            return
        botao = self.page.locator(S.PORTAL_PROFESSOR)
        if botao.count() > 0:
            botao.first.click()
        elif self._url_portal:
            self.page.goto(self._url_portal)
        else:
            raise LookupError(f"'Portal do Professor' não encontrado na tela ({self.page.url})")
        self._esperar(self.page, S.PORTAL_TABELA_TURMAS, "A tabela 'Minhas Turmas' do Portal do Professor")
        self._url_portal = self.page.url

    def listar_turmas(self) -> list[TurmaPortal]:
        self.abrir_portal()
        return turmas_da_tabela(self.tabelas())

    def abrir_turma(self, turma: TurmaPortal):
        """Clica no código da turma na tabela "Minhas Turmas". Devolve a página aberta
        (a mesma ou uma nova aba, se o SIGEduc abrir em outra janela)."""
        self.abrir_portal()
        linhas = self.page.locator("tr").filter(has_text=turma.codigo)
        if turma.componente:
            linhas = linhas.filter(has_text=turma.componente)
        # a linha mais interna (tabelas de layout também "contêm" o texto)
        linha = linhas.filter(has_not=self.page.locator("tr")).first
        link = linha.locator("a").filter(has_text=re.compile(rf"^\s*{re.escape(turma.codigo)}\s*$"))
        if link.count() == 0:
            link = linha.locator("a")
        if link.count() == 0:
            raise LookupError(f"Link da turma {turma.codigo} não encontrado")
        contexto = self.page.context
        alvo = link.first.get_attribute("target")
        if alvo and alvo != "_self":
            with contexto.expect_page() as aba:
                link.first.click()
            nova = aba.value
        else:
            novas = []

            def ao_abrir(pagina):
                novas.append(pagina)

            contexto.on("page", ao_abrir)
            try:
                link.first.click()
                # espera a turma abrir: o menu "Estudantes" aparece nesta aba ou numa nova
                for _ in range(self.cliente.config.timeout_ms // 250):
                    if novas or self.page.locator(S.TURMA_MENU).count() > 0:
                        break
                    self.page.wait_for_timeout(250)
            finally:
                contexto.remove_listener("page", ao_abrir)
            nova = novas[-1] if novas else self.page
        self._esperar(nova, S.TURMA_MENU, f"O menu 'Turmas' da Escola Digital da turma {turma.codigo}")
        return nova

    def _clicar_estudantes(self, pagina, turma: TurmaPortal) -> None:
        """Clica em Turmas > Estudantes, abrindo o menu Turmas se estiver fechado."""

        def visivel(seletor):
            return next((item for item in pagina.locator(seletor).all() if item.is_visible()), None)

        if visivel(S.TURMA_ESTUDANTES) is None:
            menu = visivel(S.TURMA_MENU)
            if menu is not None:
                menu.click()
                for _ in range(20):  # espera o menu abrir
                    if visivel(S.TURMA_ESTUDANTES) is not None:
                        break
                    pagina.wait_for_timeout(250)
        item = visivel(S.TURMA_ESTUDANTES)
        if item is None:
            raise LookupError(f"Item 'Estudantes' do menu Turmas não encontrado na turma {turma.codigo} ({pagina.url})")
        item.click()

    def _baixar_foto(self, pagina, estudante: Estudante) -> Estudante:
        """Baixa a foto com a sessão do navegador (os cookies do login do professor).
        Falha ao baixar não impede a leitura do estudante."""
        from dataclasses import replace

        if estudante.foto or not estudante.foto_url:
            return estudante
        try:
            resposta = pagina.request.get(estudante.foto_url, timeout=20000)
            dados = resposta.body() if resposta.ok else b""
            if not resposta.ok:
                log.info("Foto de %s: o SIGEduc respondeu %s", estudante.matricula, resposta.status)
        except Exception as e:
            log.info("Foto de %s não baixada: %s", estudante.matricula, e)
            return estudante
        tipo = _tipo_imagem(dados)  # confere o conteúdo, não o que o servidor diz
        if not tipo or len(dados) > FOTO_TAMANHO_MAXIMO:
            if dados:
                log.info("Foto de %s ignorada (%s, %d bytes)", estudante.matricula, tipo or "não é imagem", len(dados))
            return estudante
        return replace(estudante, foto=dados, foto_tipo=tipo)

    def listar_estudantes(self, turma: TurmaPortal) -> list[Estudante]:
        pagina = self.abrir_turma(turma)
        try:
            self._clicar_estudantes(pagina, turma)
            self._esperar(pagina, S.TURMA_LISTA_ESTUDANTES, f"A lista de estudantes da turma {turma.codigo}")
            pagina.wait_for_timeout(500)  # termina de desenhar os cartões
            cartoes = pagina.evaluate(_JS_CARTOES, FOTO_LADO)
            estudantes = estudantes_dos_cartoes(cartoes)
            if not estudantes:  # layout em tabela
                estudantes = estudantes_da_tabela(pagina.evaluate(_JS_TABELAS))
            baixadas = [self._baixar_foto(pagina, e) for e in estudantes]
            finais = descartar_fotos_padrao(baixadas)
            self.silhuetas |= {impressao(a.foto) for a, b in zip(baixadas, finais) if a.foto and not b.foto}
            quebradas = {c.get("matricula") for c in cartoes if c.get("situacao") == "imagem quebrada"}
            for antes, depois in zip(baixadas, finais):
                if depois.foto:
                    situacao = "com_foto"
                elif antes.foto:
                    situacao = "silhueta"
                elif antes.foto_url:
                    situacao = "nao_baixada"
                else:
                    situacao = "quebrada" if antes.matricula in quebradas else "sem_imagem"
                # quem está em mais de uma turma fica com o melhor resultado
                if self.fotos.get(antes.matricula) != "com_foto":
                    self.fotos[antes.matricula] = situacao
            log.info(
                "%s: %d de %d estudante(s) com foto", turma.codigo, sum(1 for e in finais if e.foto), len(finais)
            )
            return finais
        finally:
            if pagina is not self.page:
                pagina.close()


def sincronizar(cliente: SigeducCliente) -> tuple[list, Relatorio]:
    """Lê todas as turmas e seus estudantes. Devolve ([(turma, estudantes|None)], relatório)."""
    leitor = LeitorPortal(cliente)
    turmas = leitor.listar_turmas()
    log.info("%d turma(s) no Portal do Professor", len(turmas))
    relatorio = Relatorio()
    resultados = []
    for turma in turmas:
        try:
            estudantes = leitor.listar_estudantes(turma)
            log.info("%s / %s: %d estudante(s)", turma.codigo, turma.componente, len(estudantes))
            if turma.qtd_estudantes is not None and turma.qtd_estudantes != len(estudantes):
                relatorio.avisos.append(
                    f"{turma.codigo}/{turma.componente}: o portal informa {turma.qtd_estudantes} "
                    f"estudante(s), mas {len(estudantes)} foram lidos"
                )
            resultados.append((turma, estudantes))
            relatorio.sucesso += 1
        except Exception as e:
            cliente.capturar_tela(f"turma-{turma.codigo}")
            relatorio.falhas.append(f"{turma.codigo}/{turma.componente}: {(str(e).splitlines() or [repr(e)])[0]}")
            resultados.append((turma, None))
        cliente.pausa()
    # silhueta repetida entre turmas diferentes também é descartada
    todos = descartar_fotos_padrao([e for _, lista in resultados if lista for e in lista])
    sem_foto = {e.matricula for e in todos if not e.foto or impressao(e.foto) in leitor.silhuetas}
    from dataclasses import replace as _replace

    resultados = [
        (t, None if lista is None else [_replace(e, foto=None, foto_tipo="") if e.matricula in sem_foto and e.foto else e for e in lista])
        for t, lista in resultados
    ]
    for matricula in sem_foto:
        if leitor.fotos.get(matricula) == "com_foto":
            leitor.fotos[matricula] = "silhueta"
    relatorio.avisos.insert(0, resumo_fotos(leitor.fotos))
    return resultados, relatorio


def resumo_fotos(situacoes: dict[str, str]) -> str:
    """Uma linha para o professor entender quantas fotos vieram e por que faltam."""
    total = lambda situacao: sum(1 for s in situacoes.values() if s == situacao)  # noqa: E731
    partes = [f"Fotos: {total('com_foto')} de {len(situacoes)} estudante(s)"]
    for situacao, texto in (
        ("silhueta", "com a imagem padrão do SIGEduc (sem foto cadastrada)"),
        ("sem_imagem", "sem imagem no cartão"),
        ("quebrada", "com imagem que não abriu no SIGEduc"),
        ("nao_baixada", "com foto que não foi possível baixar"),
    ):
        if total(situacao):
            partes.append(f"{total(situacao)} {texto}")
    return "; ".join(partes)
