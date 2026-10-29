"""Indicadores de desempenho a partir das médias lançadas.

Nomenclatura e parâmetros oficiais:
- LDB (Lei nº 9.394/96), art. 24, V: a verificação do rendimento escolar é contínua e
  cumulativa; alínea "e": estudos de recuperação, de preferência paralelos ao período
  letivo, para os casos de baixo rendimento escolar. Arts. 12, V e 13, IV: recuperação
  dos alunos de menor rendimento. Art. 24, VI: frequência mínima de 75% para aprovação.
- Rede estadual do RN (SEEC/RN): média mínima 6,0; média anual = média aritmética dos
  quatro bimestres (Portaria nº 1033/2008/SEEC/RN; conferir a norma vigente).

Os indicadores aqui são de ACOMPANHAMENTO: a situação final do estudante depende também
da frequência e das regras de recuperação e progressão parcial da rede.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from sqlalchemy.orm import sessionmaker

from . import composicoes as comp
from .composicoes import arredondar

BIMESTRES_NO_ANO = 4
FREQUENCIA_MINIMA = 75  # % (LDB, art. 24, VI) — a frequência ainda não é importada


def faixas(minima: float) -> list[tuple[float, float, str]]:
    """Faixas da distribuição; a divisão central é a média mínima."""
    meio = (minima + 10) / 2
    baixo = minima / 1.5
    fmt = lambda v: comp._fmt(arredondar(v, 1))  # noqa: E731
    return [
        (0, baixo, f"0 a {fmt(baixo - 0.1)}"),
        (baixo, minima, f"{fmt(baixo)} a {fmt(minima - 0.1)}"),
        (minima, meio, f"{fmt(minima)} a {fmt(meio - 0.1)}"),
        (meio, 10.0001, f"{fmt(meio)} a 10"),
    ]


@dataclass
class Faixa:
    rotulo: str
    quantidade: int
    abaixo_da_minima: bool


@dataclass
class Resumo:
    minima: float
    total: int = 0
    com_media: int = 0
    media: float | None = None
    mediana: float | None = None
    maior: float | None = None
    menor: float | None = None
    acima: int = 0  # média >= mínima
    abaixo: int = 0  # média < mínima: indicados para estudos de recuperação
    faixas: list[Faixa] = field(default_factory=list)

    @property
    def pendentes(self) -> int:
        return self.total - self.com_media

    @property
    def pct_acima(self) -> float | None:
        return None if not self.com_media else self.acima / self.com_media * 100

    @property
    def maior_faixa(self) -> int:
        return max((f.quantidade for f in self.faixas), default=0)


def resumir(medias: list[float | None], minima: float) -> Resumo:
    lancadas = [m for m in medias if m is not None]
    r = Resumo(minima=minima, total=len(medias), com_media=len(lancadas))
    if lancadas:
        r.media = arredondar(statistics.fmean(lancadas))
        r.mediana = arredondar(statistics.median(lancadas))
        r.maior, r.menor = max(lancadas), min(lancadas)
        r.acima = sum(1 for m in lancadas if m >= minima)
        r.abaixo = len(lancadas) - r.acima
    r.faixas = [
        Faixa(rotulo, sum(1 for m in lancadas if inicio <= m < fim), fim <= minima)
        for inicio, fim, rotulo in faixas(minima)
    ]
    return r


@dataclass
class DesempenhoAvaliacao:
    grupo: str
    nome: str
    media: float | None  # escala 0 a 10
    lancadas: int
    total: int


def por_avaliacao(estrutura: comp.Estrutura, linhas: list[comp.LinhaAluno]) -> list[DesempenhoAvaliacao]:
    """Média de cada avaliação convertida para 0 a 10 (mostra onde a turma teve mais dificuldade)."""
    resultado = []
    for g in estrutura.grupos:
        for a in g.avaliacoes:
            notas = [l.notas[a.id] / a.valor_maximo * 10 for l in linhas if a.id in l.notas]
            resultado.append(
                DesempenhoAvaliacao(
                    g.grupo.nome,
                    a.nome,
                    arredondar(statistics.fmean(notas)) if notas else None,
                    len(notas),
                    len(linhas),
                )
            )
    return resultado


@dataclass
class Posicao:
    matricula: str
    nome: str
    media: float
    turma: str = ""
    falta: float = 0.0  # quanto falta para a média mínima


def rankings(linhas, minima: float, limite: int = 10, turma_de=None) -> tuple[list[Posicao], list[Posicao]]:
    """(menor rendimento: abaixo da mínima, do mais distante ao mais próximo;
        maior rendimento: as maiores médias). `turma_de(linha)` dá o código da turma."""
    com_media = [l for l in linhas if l.media is not None]
    pos = lambda l: Posicao(  # noqa: E731
        l.matricula, l.nome, l.media, turma_de(l) if turma_de else "", max(0.0, arredondar(minima - l.media))
    )
    menor = sorted((l for l in com_media if l.media < minima), key=lambda l: (l.media, l.nome))
    maior = sorted(com_media, key=lambda l: (-l.media, l.nome))
    return [pos(l) for l in menor[:limite]], [pos(l) for l in maior[:limite]]


# --- acompanhamento anual de uma turma ------------------------------------------
@dataclass
class AlunoAnual:
    matricula: str
    nome: str
    bimestres: dict[int, float | None]
    minima: float

    @property
    def lancados(self) -> list[float]:
        return [m for m in self.bimestres.values() if m is not None]

    @property
    def media_parcial(self) -> float | None:
        """Média aritmética dos bimestres já lançados (média anual quando os 4 estão lançados)."""
        return arredondar(statistics.fmean(self.lancados)) if self.lancados else None

    @property
    def bimestres_abaixo(self) -> int:
        return sum(1 for m in self.lancados if m < self.minima)

    @property
    def restantes(self) -> int:
        return BIMESTRES_NO_ANO - len(self.lancados)

    @property
    def necessario(self) -> float | None:
        """Média necessária nos bimestres restantes para a média anual chegar à mínima."""
        if not self.lancados or not self.restantes:
            return None
        return arredondar((self.minima * BIMESTRES_NO_ANO - sum(self.lancados)) / self.restantes)

    @property
    def situacao(self) -> str:
        """Código usado na tela: ok | atencao | recuperacao | sem_notas."""
        if not self.lancados:
            return "sem_notas"
        if not self.restantes:
            return "ok" if self.media_parcial >= self.minima else "recuperacao"
        if self.necessario > 10:
            return "recuperacao"
        if self.media_parcial < self.minima or self.bimestres_abaixo:
            return "atencao"
        return "ok"


def anual(sessoes: sessionmaker, usuario_id: int, turma_id: int, minima: float):
    """Médias de cada bimestre da turma (uma composição por bimestre; se houver mais de
    uma no mesmo bimestre, vale a mais recente). Devolve (bimestres, alunos, avisos)."""
    por_bimestre: dict[int, comp.Estrutura] = {}
    avisos = []
    for estrutura, _ in comp.listar(sessoes, usuario_id):
        if turma_id not in {t.id for _, t in comp.turmas_aplicadas(sessoes, estrutura.composicao.id)}:
            continue
        b = estrutura.composicao.bimestre
        if b in por_bimestre:
            avisos.append(
                f"O {b}º bimestre tem mais de uma composição nesta turma; foi usada a mais recente "
                f"({por_bimestre[b].composicao.nome})."
            )
            continue  # listar() vem da mais recente para a mais antiga
        por_bimestre[b] = estrutura
    alunos: dict[str, AlunoAnual] = {}
    for b, estrutura in sorted(por_bimestre.items()):
        for linha in comp.planilha(sessoes, estrutura.composicao.id, turma_id):
            aluno = alunos.setdefault(linha.matricula, AlunoAnual(linha.matricula, linha.nome, {}, minima))
            aluno.bimestres[b] = linha.media
    ordem = {"recuperacao": 0, "atencao": 1, "ok": 2, "sem_notas": 3}
    lista = sorted(alunos.values(), key=lambda a: (ordem[a.situacao], a.media_parcial if a.media_parcial is not None else 99, a.nome))
    return sorted(por_bimestre), lista, avisos
