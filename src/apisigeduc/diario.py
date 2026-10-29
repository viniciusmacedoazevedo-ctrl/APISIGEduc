"""Diário de classe: aulas, chamada (frequência) e anotações sobre o estudante.

Frequência mínima para aprovação: 75% do total de horas letivas (LDB, art. 24, VI).
Uma aula pode valer mais de um horário (aulas geminadas): a falta conta a quantidade.
As anotações são privadas de quem escreveu e nunca vão ao SIGEduc.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from .banco import Anotacao, Aula, Estudante, Frequencia, Turma

FREQUENCIA_MINIMA = 75.0  # %, LDB art. 24, VI
CATEGORIAS = {
    "leitura": "Leitura e escrita",
    "aprendizagem": "Aprendizagem",
    "comportamento": "Comportamento e convivência",
    "saude": "Saúde",
    "familia": "Família e situação social",
    "outros": "Outros",
}
QUANTIDADE_MAXIMA = 6
TAMANHO_MAXIMO_TEXTO = 4000
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]


def inicios_dos_bimestres(texto: str) -> list[tuple[int, int]]:
    """"02-01,05-04,08-01,10-13" -> [(2, 1), (5, 4), (8, 1), (10, 13)]: início de cada bimestre."""
    inicios = []
    for parte in texto.split(","):
        mes, dia = (int(x) for x in parte.strip().split("-"))
        date(2000, mes, dia)  # confere se a data existe
        inicios.append((mes, dia))
    if len(inicios) != 4 or inicios != sorted(inicios):
        raise ValueError("Informe o início dos 4 bimestres em ordem, como 02-01,05-04,08-01,10-13")
    return inicios


def bimestre_da_data(dia: date, inicios: list[tuple[int, int]]) -> int:
    """Bimestre da aula; antes do início do 1º conta como 1º."""
    return max([1] + [n for n, (mes, d) in enumerate(inicios, start=1) if (dia.month, dia.day) >= (mes, d)])


def data_por_extenso(dia: date) -> str:
    return f"{dia.day:02d} de {MESES[dia.month - 1]} de {dia.year}"


# --- aulas e chamada ------------------------------------------------------------
def aulas(sessoes: sessionmaker, turma_id: int) -> list[tuple[Aula, int]]:
    """Aulas da turma, da mais recente para a mais antiga, com o número de faltas."""
    faltas = (
        select(Frequencia.aula_id, func.count().label("n"))
        .where(Frequencia.presente.is_(False))
        .group_by(Frequencia.aula_id)
        .subquery()
    )
    with sessoes() as sessao:
        return [
            (a, n)
            for a, n in sessao.execute(
                select(Aula, func.coalesce(faltas.c.n, 0))
                .outerjoin(faltas, faltas.c.aula_id == Aula.id)
                .where(Aula.turma_id == turma_id)
                .order_by(Aula.data.desc())
            )
        ]


def obter_aula(sessoes: sessionmaker, turma_id: int, aula_id: int) -> Aula | None:
    with sessoes() as sessao:
        aula = sessao.get(Aula, aula_id)
        return aula if aula is not None and aula.turma_id == turma_id else None


def aula_do_dia(sessoes: sessionmaker, turma_id: int, dia: date) -> Aula | None:
    with sessoes() as sessao:
        return sessao.scalar(select(Aula).where(Aula.turma_id == turma_id, Aula.data == dia))


def presencas(sessoes: sessionmaker, aula_id: int) -> dict[str, bool]:
    with sessoes() as sessao:
        return {m: p for m, p in sessao.execute(select(Frequencia.matricula, Frequencia.presente).where(Frequencia.aula_id == aula_id))}


def salvar_chamada(
    sessoes: sessionmaker,
    turma_id: int,
    dia: date,
    quantidade: int,
    conteudo: str,
    observacoes: str,
    faltas: set[str],
) -> int:
    """Grava a aula do dia (cria ou atualiza) e a chamada: quem está em `faltas`
    faltou, os demais estudantes da turma estão presentes. Devolve o id da aula."""
    if not 1 <= quantidade <= QUANTIDADE_MAXIMA:
        raise ValueError(f"Quantidade de aulas deve ser de 1 a {QUANTIDADE_MAXIMA}")
    if dia > date.today():
        raise ValueError("Não é possível registrar aula em data futura")
    with sessoes.begin() as sessao:
        matriculas = set(sessao.scalars(select(Estudante.matricula).where(Estudante.turma_id == turma_id)))
        if not matriculas:
            raise ValueError("A turma não tem estudantes. Busque as turmas no SIGEduc antes da chamada.")
        aula = sessao.scalar(select(Aula).where(Aula.turma_id == turma_id, Aula.data == dia))
        if aula is None:
            aula = Aula(turma_id=turma_id, data=dia)
            sessao.add(aula)
        aula.quantidade = quantidade
        aula.conteudo = conteudo.strip()[:TAMANHO_MAXIMO_TEXTO]
        aula.observacoes = observacoes.strip()[:TAMANHO_MAXIMO_TEXTO]
        sessao.flush()
        sessao.execute(delete(Frequencia).where(Frequencia.aula_id == aula.id))
        sessao.add_all(Frequencia(aula_id=aula.id, matricula=m, presente=m not in faltas) for m in matriculas)
        return aula.id


def excluir_aula(sessoes: sessionmaker, aula_id: int) -> None:
    with sessoes.begin() as sessao:
        sessao.execute(delete(Frequencia).where(Frequencia.aula_id == aula_id))
        sessao.execute(delete(Aula).where(Aula.id == aula_id))


@dataclass
class ResumoFrequencia:
    aulas_dadas: int  # horários (soma das quantidades)
    faltas: int  # horários faltados

    @property
    def percentual(self) -> float | None:
        if not self.aulas_dadas:
            return None
        return round((self.aulas_dadas - self.faltas) / self.aulas_dadas * 100, 1)

    @property
    def abaixo_do_minimo(self) -> bool:
        return self.percentual is not None and self.percentual < FREQUENCIA_MINIMA


def frequencia_da_turma(sessoes: sessionmaker, turma_id: int) -> dict[str, ResumoFrequencia]:
    """Frequência de cada estudante da turma em todas as aulas registradas."""
    with sessoes() as sessao:
        total = sessao.scalar(select(func.coalesce(func.sum(Aula.quantidade), 0)).where(Aula.turma_id == turma_id)) or 0
        faltas = dict(
            sessao.execute(
                select(Frequencia.matricula, func.sum(Aula.quantidade))
                .join(Aula, Aula.id == Frequencia.aula_id)
                .where(Aula.turma_id == turma_id, Frequencia.presente.is_(False))
                .group_by(Frequencia.matricula)
            ).all()
        )
        matriculas = sessao.scalars(select(Estudante.matricula).where(Estudante.turma_id == turma_id)).all()
    return {m: ResumoFrequencia(total, int(faltas.get(m, 0))) for m in matriculas}


# --- anotações (privadas de quem escreveu) -----------------------------------------
def anotar(
    sessoes: sessionmaker, autor_id: int, matricula: str, categoria: str, texto: str, importante: bool = False
) -> int:
    if categoria not in CATEGORIAS:
        raise ValueError("Categoria inválida")
    texto = texto.strip()
    if not texto:
        raise ValueError("Escreva a anotação")
    if len(texto) > TAMANHO_MAXIMO_TEXTO:
        raise ValueError(f"A anotação passa de {TAMANHO_MAXIMO_TEXTO} caracteres")
    with sessoes.begin() as sessao:
        nota = Anotacao(autor_id=autor_id, matricula=matricula, categoria=categoria, texto=texto, importante=importante)
        sessao.add(nota)
        sessao.flush()
        return nota.id


def anotacoes(sessoes: sessionmaker, autor_id: int, matricula: str) -> list[Anotacao]:
    """Só as anotações de quem pede."""
    with sessoes() as sessao:
        return list(
            sessao.scalars(
                select(Anotacao)
                .where(Anotacao.autor_id == autor_id, Anotacao.matricula == matricula)
                .order_by(Anotacao.criado_em.desc(), Anotacao.id.desc())
            )
        )


def contagem_anotacoes(sessoes: sessionmaker, autor_id: int, matriculas: list[str]) -> dict[str, list[str]]:
    """{matrícula: categorias das anotações IMPORTANTES} para sinalizar o estudante ao
    lançar nota e na chamada (as demais anotações ficam só na ficha)."""
    if not matriculas:
        return {}
    with sessoes() as sessao:
        linhas = sessao.execute(
            select(Anotacao.matricula, Anotacao.categoria).where(
                Anotacao.autor_id == autor_id, Anotacao.matricula.in_(matriculas), Anotacao.importante.is_(True)
            )
        ).all()
    resultado: dict[str, list[str]] = {}
    for m, c in linhas:
        if c not in resultado.setdefault(m, []):
            resultado[m].append(c)
    return resultado


def excluir_anotacao(sessoes: sessionmaker, autor_id: int, anotacao_id: int) -> None:
    with sessoes.begin() as sessao:
        nota = sessao.get(Anotacao, anotacao_id)
        if nota is None or nota.autor_id != autor_id:
            raise LookupError("Anotação não encontrada")
        sessao.delete(nota)


# --- estudante ---------------------------------------------------------------------
def turmas_do_estudante(sessoes: sessionmaker, usuario_id: int | None, matricula: str) -> list[tuple[Turma, Estudante]]:
    """Turmas (do professor, ou todas se usuario_id for None) em que a matrícula está."""
    consulta = select(Turma, Estudante).join(Estudante, Estudante.turma_id == Turma.id).where(Estudante.matricula == matricula)
    if usuario_id is not None:
        consulta = consulta.where(Turma.usuario_id == usuario_id)
    with sessoes() as sessao:
        return [(t, e) for t, e in sessao.execute(consulta.order_by(Turma.codigo, Turma.componente))]


def alternar_importante(sessoes: sessionmaker, autor_id: int, anotacao_id: int) -> bool:
    with sessoes.begin() as sessao:
        nota = sessao.get(Anotacao, anotacao_id)
        if nota is None or nota.autor_id != autor_id:
            raise LookupError("Anotação não encontrada")
        nota.importante = not nota.importante
        return nota.importante
