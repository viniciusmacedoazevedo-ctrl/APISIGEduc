"""O que está lançado no SIGEduc (consultado só para leitura) e a comparação com o
APISIGEduc: notas do 1º ao 4º bimestre, faltas e % de frequência por estudante.

A tela de notas do SIGEduc não mostra a matrícula. O estudante é reconhecido pelo
nome (sem acentos, maiúsculas e espaços); se o nome não bater com exatamente um
estudante da turma, a linha fica sem matrícula e aparece como "não reconhecido".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from .banco import ConsultaSigeduc, Estudante, NotaSigeduc, Turma
from .composicoes import arredondar

DIFERENCA_MINIMA = 0.05  # notas com uma casa: abaixo disso é a mesma nota


def normalizar(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).strip().upper()


def salvar(sessoes: sessionmaker, usuario_id: int, resultados, agora: datetime | None = None) -> tuple[int, int, int]:
    """Grava a consulta. Devolve (turmas lidas, linhas, linhas sem estudante reconhecido).
    Turma cuja leitura falhou mantém a consulta anterior."""
    agora = agora or datetime.now()
    turmas = linhas = sem_matricula = 0
    with sessoes.begin() as sessao:
        for lida, notas in resultados:
            if notas is None:
                continue
            turma = sessao.scalar(
                select(Turma).where(
                    Turma.usuario_id == usuario_id,
                    Turma.codigo == lida.codigo,
                    Turma.componente == lida.componente,
                    Turma.escola == lida.escola,
                )
            )
            if turma is None:  # turma ainda não importada: busque as turmas antes
                continue
            por_nome: dict[str, list[str]] = {}
            for e in sessao.scalars(select(Estudante).where(Estudante.turma_id == turma.id)):
                por_nome.setdefault(normalizar(e.nome), []).append(e.matricula)
            sessao.execute(delete(NotaSigeduc).where(NotaSigeduc.turma_id == turma.id))
            for linha in notas.linhas:
                candidatos = por_nome.get(normalizar(linha.nome), [])
                matricula = candidatos[0] if len(candidatos) == 1 else None
                sem_matricula += matricula is None and linha.ativo
                n1, n2, n3, n4 = linha.notas
                sessao.add(NotaSigeduc(
                    turma_id=turma.id, matricula=matricula, nome=linha.nome, ativo=linha.ativo,
                    nota1=n1, nota2=n2, nota3=n3, nota4=n4, faltas=linha.faltas, frequencia=linha.frequencia,
                ))
            consulta = sessao.get(ConsultaSigeduc, turma.id) or ConsultaSigeduc(turma_id=turma.id)
            consulta.aulas_dadas = notas.aulas_dadas
            consulta.aulas_previstas = notas.aulas_previstas
            consulta.consultado_em = agora
            sessao.add(consulta)
            turmas += 1
            linhas += len(notas.linhas)
    return turmas, linhas, sem_matricula


def ultima_consulta(sessoes: sessionmaker, turma_id: int) -> ConsultaSigeduc | None:
    with sessoes() as sessao:
        return sessao.get(ConsultaSigeduc, turma_id)


def por_matricula(sessoes: sessionmaker, turma_id: int) -> dict[str, NotaSigeduc]:
    with sessoes() as sessao:
        return {
            n.matricula: n
            for n in sessao.scalars(select(NotaSigeduc).where(NotaSigeduc.turma_id == turma_id, NotaSigeduc.matricula.is_not(None)))
        }


@dataclass
class Linha:
    nome: str
    matricula: str | None
    ativo: bool
    sigeduc: list[float | None]  # 1º ao 4º bimestre, como está no SIGEduc
    aqui: list[float | None] = field(default_factory=lambda: [None] * 4)  # médias do APISIGEduc
    faltas: int | None = None
    frequencia: float | None = None

    @property
    def diferentes(self) -> list[bool]:
        """Bimestres em que as duas médias existem e não batem."""
        return [a is not None and s is not None and abs(a - s) >= DIFERENCA_MINIMA for s, a in zip(self.sigeduc, self.aqui)]

    @property
    def faltando(self) -> list[bool]:
        """Bimestres com média calculada aqui e ainda sem nota no SIGEduc."""
        return [a is not None and s is None for s, a in zip(self.sigeduc, self.aqui)]

    @property
    def media_anual(self) -> float | None:
        """Média anual do SIGEduc (RN): (n1 + n2 + n3 + n4) / 4, só com os quatro bimestres."""
        if any(n is None for n in self.sigeduc):
            return None
        return arredondar(sum(self.sigeduc) / 4)


@dataclass
class Comparacao:
    consulta: ConsultaSigeduc | None
    linhas: list[Linha]
    ano: int | None  # ano letivo das composições usadas para comparar

    @property
    def divergencias(self) -> int:
        return sum(sum(l.diferentes) for l in self.linhas if l.ativo)

    @property
    def faltando(self) -> int:
        return sum(sum(l.faltando) for l in self.linhas if l.ativo)

    @property
    def nao_reconhecidos(self) -> list[Linha]:
        return [l for l in self.linhas if l.ativo and l.matricula is None]


def medias_aqui(sessoes: sessionmaker, turma: Turma) -> tuple[int | None, dict[str, list[float | None]]]:
    """Médias do APISIGEduc por estudante e bimestre (ano letivo mais recente com
    composição aplicada à turma). Com duas composições no mesmo bimestre, vale a primeira."""
    from . import composicoes as comp

    aplicadas = [
        e.composicao for e, _ in comp.listar(sessoes, turma.usuario_id)
        if turma.id in {t.id for _, t in comp.turmas_aplicadas(sessoes, e.composicao.id)}
    ]
    if not aplicadas:
        return None, {}
    ano = max(c.ano_letivo for c in aplicadas)
    medias: dict[str, list[float | None]] = {}
    usados: set[int] = set()
    for c in sorted(aplicadas, key=lambda c: c.id):
        if c.ano_letivo != ano or c.bimestre in usados or not 1 <= c.bimestre <= 4:
            continue
        usados.add(c.bimestre)
        for linha in comp.planilha(sessoes, c.id, turma.id):
            medias.setdefault(linha.matricula, [None] * 4)[c.bimestre - 1] = linha.media
    return ano, medias


def comparar(sessoes: sessionmaker, turma: Turma) -> Comparacao:
    with sessoes() as sessao:
        consulta = sessao.get(ConsultaSigeduc, turma.id)
        registros = list(sessao.scalars(select(NotaSigeduc).where(NotaSigeduc.turma_id == turma.id).order_by(NotaSigeduc.id)))
    ano, medias = medias_aqui(sessoes, turma)
    linhas = [
        Linha(
            nome=r.nome, matricula=r.matricula, ativo=r.ativo,
            sigeduc=[r.nota1, r.nota2, r.nota3, r.nota4],
            aqui=medias.get(r.matricula, [None] * 4) if r.matricula else [None] * 4,
            faltas=r.faltas, frequencia=r.frequencia,
        )
        for r in registros
    ]
    return Comparacao(consulta=consulta, linhas=linhas, ano=ano)
