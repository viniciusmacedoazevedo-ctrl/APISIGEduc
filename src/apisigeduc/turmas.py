"""Turmas e estudantes importados do SIGEduc, guardados por usuário."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from . import modelos
from .banco import Estudante, Turma

# (turma lida do portal, estudantes lidos ou None se a leitura da turma falhou)
Resultado = tuple[modelos.TurmaPortal, "list[modelos.Estudante] | None"]


def salvar(sessoes: sessionmaker, usuario_id: int, resultados: list[Resultado]) -> tuple[int, int]:
    """Grava a sincronização. Devolve (turmas, estudantes) gravados.

    Os estudantes de cada turma lida com sucesso são substituídos pela lista
    nova. Se a leitura de uma turma falhou, a lista antiga é mantida. Turmas
    que não apareceram nesta sincronização não são apagadas.
    """
    agora = datetime.now()
    total_estudantes = 0
    with sessoes.begin() as sessao:
        for lida, estudantes in resultados:
            turma = sessao.scalar(
                select(Turma).where(
                    Turma.usuario_id == usuario_id,
                    Turma.codigo == lida.codigo,
                    Turma.componente == lida.componente,
                    Turma.escola == lida.escola,
                )
            )
            if turma is None:
                turma = Turma(usuario_id=usuario_id, codigo=lida.codigo, componente=lida.componente, escola=lida.escola)
                sessao.add(turma)
            turma.etapa = lida.etapa
            turma.ano_serie = lida.ano_serie
            turma.qtd_estudantes = lida.qtd_estudantes
            if estudantes is None:
                continue
            sessao.flush()
            sessao.execute(delete(Estudante).where(Estudante.turma_id == turma.id))
            unicos = {e.matricula: e for e in estudantes}  # sem matrícula repetida
            sessao.add_all(Estudante(turma_id=turma.id, matricula=e.matricula, nome=e.nome) for e in unicos.values())
            turma.sincronizado_em = agora
            total_estudantes += len(unicos)
    return len(resultados), total_estudantes


def listar(sessoes: sessionmaker, usuario_id: int | None = None) -> list[tuple[Turma, int]]:
    """Turmas com a quantidade de estudantes importados."""
    contagem = (
        select(Estudante.turma_id, func.count().label("n")).group_by(Estudante.turma_id).subquery()
    )
    consulta = (
        select(Turma, func.coalesce(contagem.c.n, 0))
        .outerjoin(contagem, contagem.c.turma_id == Turma.id)
        .order_by(Turma.codigo, Turma.componente)
    )
    if usuario_id is not None:
        consulta = consulta.where(Turma.usuario_id == usuario_id)
    with sessoes() as sessao:
        return [(t, n) for t, n in sessao.execute(consulta)]


def obter(sessoes: sessionmaker, turma_id: int) -> Turma | None:
    with sessoes() as sessao:
        return sessao.get(Turma, turma_id)


def estudantes(sessoes: sessionmaker, turma_id: int) -> list[Estudante]:
    with sessoes() as sessao:
        return list(sessao.scalars(select(Estudante).where(Estudante.turma_id == turma_id).order_by(Estudante.nome)))
