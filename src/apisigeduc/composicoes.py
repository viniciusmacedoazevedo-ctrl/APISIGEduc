"""Composição das notas do bimestre e cálculo da média enviada ao SIGEduc.

No SIGEduc só entra a média do bimestre. Aqui ela é construída a partir de
vários itens (trabalho, teste, prova...). A composição é criada uma vez e
aplicada a várias turmas; um item pode ter uma nota padrão, aplicada a todos
os estudantes, que depois pode ser ajustada aluno a aluno.

Cálculo:
- soma:  média = soma das notas dos itens (ex.: trabalho 2 + teste 3 + prova 5 = 10)
- media: média aritmética dos itens, cada um convertido para a escala 0-10
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from .banco import Composicao, ComposicaoTurma, Estudante, ItemComposicao, NotaItem, Turma

CALCULOS = {"soma": "Soma dos itens", "media": "Média dos itens (escala 0 a 10)"}
NOTA_MAXIMA = 10.0


def ler_numero(texto: str | float | None) -> float | None:
    """'8,5' -> 8.5; vazio -> None."""
    if texto is None:
        return None
    if isinstance(texto, (int, float)):
        return float(texto)
    texto = texto.strip().replace(",", ".")
    if not texto:
        return None
    try:
        return float(texto)
    except ValueError:
        raise ValueError(f"valor inválido: {texto!r}") from None


def arredondar(valor: float, casas: int = 1) -> float:
    """Arredondamento escolar (0,25 -> 0,3), sem o viés do round() do Python."""
    return float(Decimal(str(valor)).quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP))


@dataclass
class ItemNovo:
    nome: str
    valor_maximo: float
    nota_padrao: float | None = None

    def validar(self) -> None:
        if not self.nome.strip():
            raise ValueError("item sem nome")
        if not 0 < self.valor_maximo <= NOTA_MAXIMA:
            raise ValueError(f"{self.nome}: o valor do item deve ser maior que 0 e no máximo {NOTA_MAXIMA:g}")
        if self.nota_padrao is not None and not 0 <= self.nota_padrao <= self.valor_maximo:
            raise ValueError(f"{self.nome}: a nota para todos deve ficar entre 0 e {self.valor_maximo:g}")


def calcular_media(calculo: str, itens: list[ItemComposicao], notas: dict[int, float]) -> float | None:
    """Média do estudante; None se algum item ainda não tem nota."""
    if not itens or any(item.id not in notas for item in itens):
        return None
    if calculo == "media":
        media = sum(notas[i.id] / i.valor_maximo * NOTA_MAXIMA for i in itens) / len(itens)
    else:
        media = sum(notas[i.id] for i in itens)
    return arredondar(min(media, NOTA_MAXIMA))


def total_maximo(calculo: str, itens: list[ItemComposicao]) -> float:
    return NOTA_MAXIMA if calculo == "media" else sum(i.valor_maximo for i in itens)


# --- composições --------------------------------------------------------------
def criar(
    sessoes: sessionmaker, usuario_id: int, nome: str, ano_letivo: int, bimestre: int, calculo: str, itens: list[ItemNovo]
) -> int:
    if not nome.strip():
        raise ValueError("Dê um nome à composição (ex.: 3º bimestre - 6º anos)")
    if bimestre not in (1, 2, 3, 4):
        raise ValueError("Bimestre deve ser de 1 a 4")
    if calculo not in CALCULOS:
        raise ValueError("Forma de cálculo inválida")
    if not itens:
        raise ValueError("Inclua pelo menos um item (ex.: Prova, vale 10)")
    for item in itens:
        item.validar()
    if calculo == "soma" and sum(i.valor_maximo for i in itens) > NOTA_MAXIMA + 1e-9:
        raise ValueError(f"Na soma, os itens juntos não podem passar de {NOTA_MAXIMA:g} pontos")
    with sessoes.begin() as sessao:
        composicao = Composicao(
            usuario_id=usuario_id, nome=nome.strip(), ano_letivo=ano_letivo, bimestre=bimestre, calculo=calculo
        )
        sessao.add(composicao)
        sessao.flush()
        sessao.add_all(
            ItemComposicao(
                composicao_id=composicao.id, nome=i.nome.strip(), valor_maximo=i.valor_maximo, nota_padrao=i.nota_padrao, ordem=n
            )
            for n, i in enumerate(itens)
        )
        return composicao.id


def duplicar(sessoes: sessionmaker, composicao_id: int, nome: str, bimestre: int) -> int:
    """Copia os itens para outra composição (ex.: mesmo modelo no 4º bimestre)."""
    composicao, itens = obter(sessoes, composicao_id)
    return criar(
        sessoes,
        composicao.usuario_id,
        nome,
        composicao.ano_letivo,
        bimestre,
        composicao.calculo,
        [ItemNovo(i.nome, i.valor_maximo, i.nota_padrao) for i in itens],
    )


def listar(sessoes: sessionmaker, usuario_id: int) -> list[tuple[Composicao, list[ItemComposicao], int]]:
    """Composições do usuário com seus itens e o número de turmas aplicadas."""
    with sessoes() as sessao:
        composicoes = sessao.scalars(
            select(Composicao)
            .where(Composicao.usuario_id == usuario_id)
            .order_by(Composicao.ano_letivo.desc(), Composicao.bimestre.desc(), Composicao.id.desc())
        ).all()
        resultado = []
        for c in composicoes:
            itens = sessao.scalars(select(ItemComposicao).where(ItemComposicao.composicao_id == c.id).order_by(ItemComposicao.ordem)).all()
            n_turmas = len(sessao.scalars(select(ComposicaoTurma.id).where(ComposicaoTurma.composicao_id == c.id)).all())
            resultado.append((c, list(itens), n_turmas))
        return resultado


def obter(sessoes: sessionmaker, composicao_id: int) -> tuple[Composicao, list[ItemComposicao]]:
    with sessoes() as sessao:
        composicao = sessao.get(Composicao, composicao_id)
        if composicao is None:
            raise LookupError("Composição não encontrada")
        itens = sessao.scalars(
            select(ItemComposicao).where(ItemComposicao.composicao_id == composicao_id).order_by(ItemComposicao.ordem)
        ).all()
        return composicao, list(itens)


def excluir(sessoes: sessionmaker, composicao_id: int) -> None:
    with sessoes.begin() as sessao:
        itens = select(ItemComposicao.id).where(ItemComposicao.composicao_id == composicao_id)
        sessao.execute(delete(NotaItem).where(NotaItem.item_id.in_(itens)))
        sessao.execute(delete(ComposicaoTurma).where(ComposicaoTurma.composicao_id == composicao_id))
        sessao.execute(delete(ItemComposicao).where(ItemComposicao.composicao_id == composicao_id))
        sessao.execute(delete(Composicao).where(Composicao.id == composicao_id))


# --- aplicação nas turmas -----------------------------------------------------
def turmas_aplicadas(sessoes: sessionmaker, composicao_id: int) -> list[tuple[ComposicaoTurma, Turma]]:
    with sessoes() as sessao:
        return [
            (ligacao, turma)
            for ligacao, turma in sessao.execute(
                select(ComposicaoTurma, Turma)
                .join(Turma, Turma.id == ComposicaoTurma.turma_id)
                .where(ComposicaoTurma.composicao_id == composicao_id)
                .order_by(Turma.codigo)
            )
        ]


def aplicar(sessoes: sessionmaker, composicao_id: int, turma_ids: list[int]) -> int:
    """Aplica a composição nas turmas e lança as notas padrão para todos os
    estudantes (sem sobrescrever notas já lançadas). Devolve quantas turmas novas."""
    _, itens = obter(sessoes, composicao_id)
    novas = 0
    with sessoes.begin() as sessao:
        for turma_id in turma_ids:
            existe = sessao.scalar(
                select(ComposicaoTurma).where(
                    ComposicaoTurma.composicao_id == composicao_id, ComposicaoTurma.turma_id == turma_id
                )
            )
            if existe is None:
                sessao.add(ComposicaoTurma(composicao_id=composicao_id, turma_id=turma_id))
                novas += 1
            matriculas = sessao.scalars(select(Estudante.matricula).where(Estudante.turma_id == turma_id)).all()
            for item in itens:
                if item.nota_padrao is not None:
                    _preencher(sessao, item.id, turma_id, matriculas, item.nota_padrao, sobrescrever=False)
    return novas


def remover_turma(sessoes: sessionmaker, composicao_id: int, turma_id: int) -> None:
    _, itens = obter(sessoes, composicao_id)
    with sessoes.begin() as sessao:
        sessao.execute(
            delete(NotaItem).where(NotaItem.turma_id == turma_id, NotaItem.item_id.in_([i.id for i in itens]))
        )
        sessao.execute(
            delete(ComposicaoTurma).where(
                ComposicaoTurma.composicao_id == composicao_id, ComposicaoTurma.turma_id == turma_id
            )
        )


def _preencher(sessao, item_id: int, turma_id: int, matriculas, valor: float, sobrescrever: bool) -> None:
    existentes = {
        n.matricula: n
        for n in sessao.scalars(select(NotaItem).where(NotaItem.item_id == item_id, NotaItem.turma_id == turma_id))
    }
    for matricula in matriculas:
        nota = existentes.get(matricula)
        if nota is None:
            sessao.add(NotaItem(item_id=item_id, turma_id=turma_id, matricula=matricula, valor=valor))
        elif sobrescrever:
            nota.valor = valor


# --- lançamento por turma ---------------------------------------------------------
@dataclass
class LinhaAluno:
    matricula: str
    nome: str
    notas: dict[int, float] = field(default_factory=dict)  # item_id -> valor
    media: float | None = None


def planilha(sessoes: sessionmaker, composicao_id: int, turma_id: int) -> list[LinhaAluno]:
    """Estudantes da turma com as notas de cada item e a média calculada."""
    composicao, itens = obter(sessoes, composicao_id)
    with sessoes() as sessao:
        estudantes = sessao.scalars(
            select(Estudante).where(Estudante.turma_id == turma_id).order_by(Estudante.nome)
        ).all()
        notas = sessao.scalars(
            select(NotaItem).where(NotaItem.turma_id == turma_id, NotaItem.item_id.in_([i.id for i in itens]))
        ).all()
    por_aluno: dict[str, dict[int, float]] = {}
    for n in notas:
        por_aluno.setdefault(n.matricula, {})[n.item_id] = n.valor
    linhas = []
    for e in estudantes:
        notas_aluno = por_aluno.get(e.matricula, {})
        linhas.append(LinhaAluno(e.matricula, e.nome, notas_aluno, calcular_media(composicao.calculo, itens, notas_aluno)))
    return linhas


def salvar_notas(sessoes: sessionmaker, composicao_id: int, turma_id: int, valores: dict[tuple[int, str], str]) -> list[str]:
    """Grava as notas digitadas {(item_id, matricula): '8,5'}. Vazio apaga a nota.

    Devolve a lista de erros (as notas válidas são gravadas mesmo assim).
    """
    _, itens = obter(sessoes, composicao_id)
    por_id = {i.id: i for i in itens}
    with sessoes() as sessao:
        nomes = {
            matricula: nome
            for matricula, nome in sessao.execute(
                select(Estudante.matricula, Estudante.nome).where(Estudante.turma_id == turma_id)
            )
        }
    erros = []
    with sessoes.begin() as sessao:
        existentes = {
            (n.item_id, n.matricula): n
            for n in sessao.scalars(
                select(NotaItem).where(NotaItem.turma_id == turma_id, NotaItem.item_id.in_(list(por_id)))
            )
        }
        for (item_id, matricula), texto in valores.items():
            item = por_id.get(item_id)
            if item is None or matricula not in nomes:
                continue
            try:
                valor = ler_numero(texto)
                if valor is not None and not 0 <= valor <= item.valor_maximo:
                    raise ValueError(f"deve ficar entre 0 e {item.valor_maximo:g}")
            except ValueError as e:
                erros.append(f"{nomes[matricula]} — {item.nome}: {e}")
                continue
            nota = existentes.get((item_id, matricula))
            if valor is None:
                if nota is not None:
                    sessao.delete(nota)
            elif nota is None:
                sessao.add(NotaItem(item_id=item_id, turma_id=turma_id, matricula=matricula, valor=valor))
            else:
                nota.valor = valor
    return erros


def csv_medias(composicao: Composicao, turma: Turma, linhas: list[LinhaAluno]) -> bytes:
    """Médias no formato de planilha de notas aceito pela fila de lançamento."""
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(["matricula", "nome", "turma", "disciplina", "bimestre", "nota"])
    for linha in linhas:
        if linha.media is not None:
            escritor.writerow(
                [linha.matricula, linha.nome, turma.codigo, turma.componente, composicao.bimestre, f"{linha.media:.1f}".replace(".", ",")]
            )
    return ("﻿" + saida.getvalue()).encode("utf-8")


def marcar_enviado(sessoes: sessionmaker, composicao_id: int, turma_id: int) -> None:
    with sessoes.begin() as sessao:
        ligacao = sessao.scalar(
            select(ComposicaoTurma).where(
                ComposicaoTurma.composicao_id == composicao_id, ComposicaoTurma.turma_id == turma_id
            )
        )
        if ligacao is not None:
            ligacao.enviado_em = datetime.now()
