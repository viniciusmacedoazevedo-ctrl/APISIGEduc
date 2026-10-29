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
_JS_CARTOES = """
() => {
  const padrao = /Matr[ií]cula\\s*:?\\s*(\\d{4,})/i;
  const contar = (t) => (t.match(/Matr[ií]cula\\s*:?\\s*\\d{4,}/gi) || []).length;
  const internos = Array.from(document.body.querySelectorAll('*')).filter((el) =>
    padrao.test(el.innerText || '') &&
    !Array.from(el.children).some((f) => padrao.test(f.innerText || '')));
  return internos.map((el) => {
    let cartao = el;
    while (cartao.parentElement && contar(cartao.parentElement.innerText || '') === 1) cartao = cartao.parentElement;
    const linhas = (cartao.innerText || '').split('\\n').map((l) => l.replace(/^[\\s•·●○◦-]+/, '').trim()).filter(Boolean);
    const nome = linhas.find((l) => !/^(matr[ií]cula|usu[aá]rio|e-?mail)\\b/i.test(l)) || '';
    // foto: a maior imagem do cartão (a outra é o ícone pequeno ao lado do nome)
    const imagens = Array.from(cartao.querySelectorAll('img')).map((img) => {
      const r = img.getBoundingClientRect();
      return { src: img.currentSrc || img.src || '', area: r.width * r.height };
    }).filter((i) => /^https?:/.test(i.src)).sort((a, b) => b.area - a.area);
    const foto = imagens.length && imagens[0].area >= 900 ? imagens[0].src : '';
    return { matricula: (el.innerText.match(padrao) || [])[1] || '', nome, foto };
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


def estudantes_dos_cartoes(cartoes: list[dict]) -> list[Estudante]:
    vistos: dict[str, Estudante] = {}
    for cartao in cartoes:
        nome = re.sub(r"\s+", " ", cartao.get("nome", "")).strip()
        matricula = cartao.get("matricula", "")
        if matricula and nome and matricula not in vistos:
            vistos[matricula] = Estudante(matricula=matricula, nome=nome, foto_url=cartao.get("foto", "") or "")
    return list(vistos.values())


FOTO_TAMANHO_MAXIMO = 400 * 1024


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

        if not estudante.foto_url:
            return estudante
        try:
            resposta = pagina.request.get(estudante.foto_url, timeout=15000)
            dados = resposta.body() if resposta.ok else b""
        except Exception:
            log.debug("Falha ao baixar a foto de %s", estudante.matricula, exc_info=True)
            return estudante
        tipo = _tipo_imagem(dados)  # confere o conteúdo, não o que o servidor diz
        if not tipo or len(dados) > FOTO_TAMANHO_MAXIMO:
            return estudante
        return replace(estudante, foto=dados, foto_tipo=tipo)

    def listar_estudantes(self, turma: TurmaPortal) -> list[Estudante]:
        pagina = self.abrir_turma(turma)
        try:
            self._clicar_estudantes(pagina, turma)
            self._esperar(pagina, S.TURMA_LISTA_ESTUDANTES, f"A lista de estudantes da turma {turma.codigo}")
            pagina.wait_for_timeout(500)  # termina de desenhar os cartões
            estudantes = estudantes_dos_cartoes(pagina.evaluate(_JS_CARTOES))
            if not estudantes:  # layout em tabela
                estudantes = estudantes_da_tabela(pagina.evaluate(_JS_TABELAS))
            return descartar_fotos_padrao([self._baixar_foto(pagina, e) for e in estudantes])
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
                relatorio.falhas.append(
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
    sem_foto = {e.matricula for e in todos if not e.foto}
    from dataclasses import replace as _replace

    resultados = [
        (t, None if lista is None else [_replace(e, foto=None, foto_tipo="") if e.matricula in sem_foto and e.foto else e for e in lista])
        for t, lista in resultados
    ]
    return resultados, relatorio
