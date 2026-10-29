"""Consulta das notas e da frequência já lançadas no SIGEduc. Só leitura.

Caminho: Portal do Professor -> código da turma (Escola Digital) -> menu lateral
"Lançar Notas" -> tabela com uma linha por estudante (notas do 1º ao 4º bimestre,
faltas e % de frequência). O robô só lê a tabela: não preenche nada e não clica em
Gravar. A tela não mostra a matrícula; o estudante é reconhecido pelo nome.
Nomes riscados são de quem saiu da turma.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .. import seletores as S
from ..cliente import SigeducCliente
from ..modelos import TurmaPortal
from .base import Relatorio
from .turmas import LeitorPortal

log = logging.getLogger(__name__)

_JS_NOTAS = """
() => {
  const tabela = document.querySelector("table[id$='notaEstudante']");
  if (!tabela) return null;
  const texto = (el) => (el ? (el.value !== undefined && el.tagName !== 'TD' ? el.value : el.textContent) : '').replace(/\\s+/g, ' ').trim();
  const linhas = [];
  for (const tr of (tabela.tBodies[0] || tabela).rows) {
    const celula = tr.querySelector("td[title='Estudante']") || tr.cells[0];
    if (!celula || !tr.querySelector("input[id$=':nota1']")) continue;
    const riscado = getComputedStyle(celula).textDecorationLine.includes('line-through') ||
      !!celula.querySelector('s, strike, del');
    const campo = (sufixo) => texto(tr.querySelector(`[id$=':${sufixo}']`));
    linhas.push({
      nome: texto(celula), riscado,
      notas: ['nota1', 'nota2', 'nota3', 'nota4'].map(campo),
      faltas: campo('frequencia'), frequencia: campo('frequePercentual'),
    });
  }
  const totais = Array.from(document.querySelectorAll('span.totalAulas')).map((s) => s.textContent.trim());
  return { linhas, totais };
}
"""


@dataclass
class LinhaNotas:
    nome: str
    ativo: bool
    notas: list[float | None]  # 1º ao 4º bimestre
    faltas: int | None = None
    frequencia: float | None = None


@dataclass
class NotasTurma:
    linhas: list[LinhaNotas] = field(default_factory=list)
    aulas_dadas: int | None = None
    aulas_previstas: int | None = None


def _numero(texto: str | None) -> float | None:
    texto = (texto or "").strip().replace("%", "").replace(",", ".")
    try:
        return float(texto) if texto else None
    except ValueError:
        return None


def notas_da_tela(dados: dict | None) -> NotasTurma:
    if not dados:
        return NotasTurma()
    linhas = []
    for item in dados.get("linhas", []):
        nome = re.sub(r"\s+", " ", item.get("nome", "")).strip()
        if not nome:
            continue
        notas = [_numero(v) for v in (item.get("notas") or [])[:4]]
        faltas = _numero(item.get("faltas"))
        linhas.append(LinhaNotas(
            nome=nome,
            ativo=not item.get("riscado"),
            notas=notas + [None] * (4 - len(notas)),
            faltas=int(faltas) if faltas is not None else None,
            frequencia=_numero(item.get("frequencia")),
        ))
    totais = [_numero(t) for t in dados.get("totais", [])]
    return NotasTurma(
        linhas=linhas,
        aulas_dadas=int(totais[0]) if len(totais) > 0 and totais[0] is not None else None,
        aulas_previstas=int(totais[1]) if len(totais) > 1 and totais[1] is not None else None,
    )


class LeitorNotas(LeitorPortal):
    def _abrir_lancar_notas(self, pagina, turma: TurmaPortal) -> None:
        """Abre o item "Lançar Notas" do menu lateral (só para ler a tabela)."""
        itens = pagina.locator(S.TURMA_LANCAR_NOTAS)
        item = next((i for i in itens.all() if i.is_visible()), None)
        if item is not None:
            item.click()
        elif itens.count() > 0:  # menu recolhido (tela estreita): o link existe, só não aparece
            itens.first.evaluate("link => link.click()")
        else:
            raise LookupError(f"Item 'Lançar Notas' não encontrado na turma {turma.codigo} ({pagina.url})")

    def ler_notas(self, turma: TurmaPortal) -> NotasTurma:
        pagina = self.abrir_turma(turma)
        try:
            self._abrir_lancar_notas(pagina, turma)
            self._esperar(pagina, S.CONSULTA_TABELA_NOTAS, f"A tabela de notas da turma {turma.codigo}")
            pagina.wait_for_timeout(300)  # termina de preencher os campos
            return notas_da_tela(pagina.evaluate(_JS_NOTAS))
        finally:
            if pagina is not self.page:
                pagina.close()


def consultar(cliente: SigeducCliente) -> tuple[list[tuple[TurmaPortal, NotasTurma | None]], Relatorio]:
    """Lê as notas e a frequência de todas as turmas do professor. Não altera nada no SIGEduc."""
    leitor = LeitorNotas(cliente)
    turmas = leitor.listar_turmas()
    relatorio = Relatorio()
    resultados: list[tuple[TurmaPortal, NotasTurma | None]] = []
    for turma in turmas:
        try:
            notas = leitor.ler_notas(turma)
            log.info("%s / %s: %d estudante(s) na tela de notas", turma.codigo, turma.componente, len(notas.linhas))
            resultados.append((turma, notas))
            relatorio.sucesso += 1
        except Exception as e:
            cliente.capturar_tela(f"notas-{turma.codigo}")
            relatorio.falhas.append(f"{turma.codigo}/{turma.componente}: {(str(e).splitlines() or [repr(e)])[0]}")
            resultados.append((turma, None))
        cliente.pausa()
    return resultados, relatorio
