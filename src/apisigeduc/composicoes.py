"""Composição das notas do bimestre e cálculo da média enviada ao SIGEduc.

No SIGEduc só entra a média do bimestre. Aqui ela é construída em dois níveis,
totalmente personalizáveis por bimestre:

    Média do bimestre  = soma ou média dos GRUPOS (Trabalho, Teste, Prova...)
    Nota de cada grupo = soma ou média das suas AVALIAÇÕES (Trabalho 1, 2, 3...)

Exemplos:
- Prova (vale 10)                          -> média = prova
- Teste (10) e Prova (10), média dos grupos -> (teste + prova) / 2
- Teste (5) e Prova (5), soma               -> teste + prova
- Trabalho (4) com Trabalho 1, 2 e 3 somados, Teste (2), Prova (4) -> soma

Regras de cada combinação:
- soma:  nota = soma das notas; os valores das partes somam até o valor do todo
- media: nota = média das notas, cada uma convertida para a escala do todo

A composição é criada uma vez e aplicada a várias turmas. Uma avaliação pode ter
uma nota padrão, lançada para todos os estudantes e ajustável aluno a aluno.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from .banco import Composicao, ComposicaoTurma, Estudante, GrupoComposicao, ItemComposicao, NotaItem, Turma

CALCULOS = {"soma": "Soma", "media": "Média"}
NOTA_MAXIMA = 10.0
_FOLGA = 1e-9


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


def _fmt(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def combinar(calculo: str, partes: list[tuple[float, float]], valor_total: float) -> float:
    """Combina (nota, valor máximo) das partes numa nota de 0 a valor_total."""
    if calculo == "media":
        return sum(nota / maximo for nota, maximo in partes) / len(partes) * valor_total
    return sum(nota for nota, _ in partes)


# --- estrutura (o que o professor monta) ------------------------------------------
@dataclass
class AvaliacaoNova:
    nome: str
    valor_maximo: float
    nota_padrao: float | None = None

    def validar(self, grupo: str) -> None:
        if not self.nome.strip():
            raise ValueError(f"{grupo}: avaliação sem nome")
        if not 0 < self.valor_maximo <= NOTA_MAXIMA:
            raise ValueError(f"{self.nome}: deve valer mais que 0 e no máximo {_fmt(NOTA_MAXIMA)}")
        if self.nota_padrao is not None and not 0 <= self.nota_padrao <= self.valor_maximo:
            raise ValueError(f"{self.nome}: a nota para todos deve ficar entre 0 e {_fmt(self.valor_maximo)}")


@dataclass
class GrupoNovo:
    nome: str
    valor: float
    calculo: str = "soma"
    avaliacoes: list[AvaliacaoNova] = field(default_factory=list)

    def validar(self) -> None:
        if not self.nome.strip():
            raise ValueError("Grupo sem nome (ex.: Trabalho, Teste, Prova)")
        if not 0 < self.valor <= NOTA_MAXIMA:
            raise ValueError(f"{self.nome}: deve valer mais que 0 e no máximo {_fmt(NOTA_MAXIMA)}")
        if self.calculo not in CALCULOS:
            raise ValueError(f"{self.nome}: forma de combinar inválida")
        if not self.avaliacoes:
            raise ValueError(f"{self.nome}: inclua pelo menos uma avaliação")
        for a in self.avaliacoes:
            a.validar(self.nome)
        if self.calculo == "soma":
            soma = sum(a.valor_maximo for a in self.avaliacoes)
            if soma > self.valor + _FOLGA:
                raise ValueError(
                    f"{self.nome}: as avaliações somam {_fmt(soma)}, mais que os {_fmt(self.valor)} do grupo"
                )


def validar_estrutura(calculo: str, grupos: list[GrupoNovo]) -> None:
    if calculo not in CALCULOS:
        raise ValueError("Forma de calcular a média inválida")
    if not grupos:
        raise ValueError("Inclua pelo menos um grupo (ex.: Prova, vale 10)")
    for g in grupos:
        g.validar()
    if calculo == "soma":
        soma = sum(g.valor for g in grupos)
        if soma > NOTA_MAXIMA + _FOLGA:
            raise ValueError(f"Na soma, os grupos juntos valem {_fmt(soma)}: não podem passar de {_fmt(NOTA_MAXIMA)}")


@dataclass
class Grupo:
    grupo: GrupoComposicao
    avaliacoes: list[ItemComposicao]

    def nota(self, notas: dict[int, float]) -> float | None:
        if any(a.id not in notas for a in self.avaliacoes):
            return None
        return combinar(self.grupo.calculo, [(notas[a.id], a.valor_maximo) for a in self.avaliacoes], self.grupo.valor)


@dataclass
class Estrutura:
    composicao: Composicao
    grupos: list[Grupo]

    @property
    def avaliacoes(self) -> list[ItemComposicao]:
        return [a for g in self.grupos for a in g.avaliacoes]

    def media(self, notas: dict[int, float]) -> float | None:
        """Média do estudante; None se alguma avaliação ainda não tem nota."""
        parciais = [g.nota(notas) for g in self.grupos]
        if not parciais or any(p is None for p in parciais):
            return None
        media = combinar(
            self.composicao.calculo, [(p, g.grupo.valor) for p, g in zip(parciais, self.grupos)], NOTA_MAXIMA
        )
        return arredondar(min(media, NOTA_MAXIMA))

    def formula(self) -> list[str]:
        """Explicação em texto, ex.: ['Média = Trabalho (4) + Teste (2) + Prova (4)', ...]."""
        def juntar(calculo, partes):
            return " + ".join(partes) if calculo == "soma" else f"média de {', '.join(partes)}"

        linhas = [
            "Média = " + juntar(self.composicao.calculo, [f"{g.grupo.nome} ({_fmt(g.grupo.valor)})" for g in self.grupos])
        ]
        for g in self.grupos:
            if len(g.avaliacoes) > 1 or g.avaliacoes[0].nome != g.grupo.nome:
                partes = [f"{a.nome} ({_fmt(a.valor_maximo)})" for a in g.avaliacoes]
                linhas.append(f"{g.grupo.nome} = " + juntar(g.grupo.calculo, partes))
        return linhas

    def avisos(self) -> list[str]:
        """Pontos que provavelmente faltam planejar (não impedem o uso)."""
        avisos = []
        if self.composicao.calculo == "soma":
            soma = sum(g.grupo.valor for g in self.grupos)
            if abs(soma - NOTA_MAXIMA) > _FOLGA:
                avisos.append(f"Os grupos somam {_fmt(soma)} pontos: a média máxima será {_fmt(soma)}, não 10.")
        for g in self.grupos:
            if g.grupo.calculo == "soma":
                soma = sum(a.valor_maximo for a in g.avaliacoes)
                if abs(soma - g.grupo.valor) > _FOLGA:
                    avisos.append(
                        f"{g.grupo.nome}: as avaliações somam {_fmt(soma)} de {_fmt(g.grupo.valor)} — "
                        f"faltam {_fmt(g.grupo.valor - soma)} ponto(s) (inclua outra avaliação quando planejar)."
                    )
        return avisos


# --- composições --------------------------------------------------------------
def criar(
    sessoes: sessionmaker, usuario_id: int, nome: str, ano_letivo: int, bimestre: int, calculo: str, grupos: list[GrupoNovo]
) -> int:
    if not nome.strip():
        raise ValueError("Dê um nome à composição (ex.: 3º bimestre - 6º anos)")
    if bimestre not in (1, 2, 3, 4):
        raise ValueError("Bimestre deve ser de 1 a 4")
    validar_estrutura(calculo, grupos)
    with sessoes.begin() as sessao:
        composicao = Composicao(
            usuario_id=usuario_id, nome=nome.strip(), ano_letivo=ano_letivo, bimestre=bimestre, calculo=calculo
        )
        sessao.add(composicao)
        sessao.flush()
        ordem = 0
        for n, g in enumerate(grupos):
            grupo = GrupoComposicao(
                composicao_id=composicao.id, nome=g.nome.strip(), valor=g.valor, calculo=g.calculo, ordem=n
            )
            sessao.add(grupo)
            sessao.flush()
            for a in g.avaliacoes:
                sessao.add(
                    ItemComposicao(
                        composicao_id=composicao.id,
                        grupo_id=grupo.id,
                        nome=a.nome.strip(),
                        valor_maximo=a.valor_maximo,
                        nota_padrao=a.nota_padrao,
                        ordem=ordem,
                    )
                )
                ordem += 1
        return composicao.id


def obter(sessoes: sessionmaker, composicao_id: int) -> Estrutura:
    with sessoes() as sessao:
        composicao = sessao.get(Composicao, composicao_id)
        if composicao is None:
            raise LookupError("Composição não encontrada")
        grupos = sessao.scalars(
            select(GrupoComposicao).where(GrupoComposicao.composicao_id == composicao_id).order_by(GrupoComposicao.ordem, GrupoComposicao.id)
        ).all()
        itens = sessao.scalars(
            select(ItemComposicao).where(ItemComposicao.composicao_id == composicao_id).order_by(ItemComposicao.ordem, ItemComposicao.id)
        ).all()
    return Estrutura(composicao, [Grupo(g, [i for i in itens if i.grupo_id == g.id]) for g in grupos])


def para_novos(estrutura: Estrutura) -> list[GrupoNovo]:
    return [
        GrupoNovo(
            g.grupo.nome,
            g.grupo.valor,
            g.grupo.calculo,
            [AvaliacaoNova(a.nome, a.valor_maximo, a.nota_padrao) for a in g.avaliacoes],
        )
        for g in estrutura.grupos
    ]


def duplicar(sessoes: sessionmaker, composicao_id: int, nome: str, bimestre: int) -> int:
    """Copia a estrutura para outra composição (ex.: mesmo modelo no 4º bimestre)."""
    estrutura = obter(sessoes, composicao_id)
    c = estrutura.composicao
    return criar(sessoes, c.usuario_id, nome, c.ano_letivo, bimestre, c.calculo, para_novos(estrutura))


def listar(sessoes: sessionmaker, usuario_id: int) -> list[tuple[Estrutura, int]]:
    """Composições do usuário com o número de turmas aplicadas."""
    with sessoes() as sessao:
        ids = sessao.scalars(
            select(Composicao.id)
            .where(Composicao.usuario_id == usuario_id)
            .order_by(Composicao.ano_letivo.desc(), Composicao.bimestre.desc(), Composicao.id.desc())
        ).all()
        n_turmas = dict(
            sessao.execute(
                select(ComposicaoTurma.composicao_id, func.count()).group_by(ComposicaoTurma.composicao_id)
            ).all()
        )
    return [(obter(sessoes, i), n_turmas.get(i, 0)) for i in ids]


def excluir(sessoes: sessionmaker, composicao_id: int) -> None:
    with sessoes.begin() as sessao:
        itens = select(ItemComposicao.id).where(ItemComposicao.composicao_id == composicao_id)
        sessao.execute(delete(NotaItem).where(NotaItem.item_id.in_(itens)))
        sessao.execute(delete(ComposicaoTurma).where(ComposicaoTurma.composicao_id == composicao_id))
        sessao.execute(delete(ItemComposicao).where(ItemComposicao.composicao_id == composicao_id))
        sessao.execute(delete(GrupoComposicao).where(GrupoComposicao.composicao_id == composicao_id))
        sessao.execute(delete(Composicao).where(Composicao.id == composicao_id))


def adicionar_avaliacao(sessoes: sessionmaker, composicao_id: int, grupo_id: int, nova: AvaliacaoNova) -> None:
    """Inclui uma avaliação num grupo durante o bimestre (ex.: Trabalho 3) e lança
    a nota para todos nas turmas em que a composição já está aplicada."""
    estrutura = obter(sessoes, composicao_id)
    grupo = next((g for g in estrutura.grupos if g.grupo.id == grupo_id), None)
    if grupo is None:
        raise LookupError("Grupo não encontrado")
    novo = GrupoNovo(
        grupo.grupo.nome,
        grupo.grupo.valor,
        grupo.grupo.calculo,
        [AvaliacaoNova(a.nome, a.valor_maximo) for a in grupo.avaliacoes] + [nova],
    )
    novo.validar()
    with sessoes.begin() as sessao:
        ultima = max((a.ordem for a in estrutura.avaliacoes), default=0)
        item = ItemComposicao(
            composicao_id=composicao_id,
            grupo_id=grupo_id,
            nome=nova.nome.strip(),
            valor_maximo=nova.valor_maximo,
            nota_padrao=nova.nota_padrao,
            ordem=ultima + 1,
        )
        sessao.add(item)
        sessao.flush()
        # mantém as avaliações de cada grupo juntas na ordem
        for n, a in enumerate(sorted(sessao.scalars(select(ItemComposicao).where(ItemComposicao.composicao_id == composicao_id)),
                                     key=lambda a: ([g.grupo.id for g in estrutura.grupos].index(a.grupo_id), a.ordem))):
            a.ordem = n
        if nova.nota_padrao is not None:
            for turma_id in sessao.scalars(select(ComposicaoTurma.turma_id).where(ComposicaoTurma.composicao_id == composicao_id)):
                matriculas = sessao.scalars(select(Estudante.matricula).where(Estudante.turma_id == turma_id)).all()
                _preencher(sessao, item.id, turma_id, matriculas, nova.nota_padrao, sobrescrever=False)


def remover_avaliacao(sessoes: sessionmaker, composicao_id: int, item_id: int) -> None:
    estrutura = obter(sessoes, composicao_id)
    grupo = next((g for g in estrutura.grupos if item_id in {a.id for a in g.avaliacoes}), None)
    if grupo is None:
        raise LookupError("Avaliação não encontrada")
    with sessoes.begin() as sessao:
        sessao.execute(delete(NotaItem).where(NotaItem.item_id == item_id))
        sessao.execute(delete(ItemComposicao).where(ItemComposicao.id == item_id))
        if len(grupo.avaliacoes) == 1:  # era a única: o grupo sai junto
            if len(estrutura.grupos) == 1:
                raise ValueError("A composição precisa de pelo menos uma avaliação")
            sessao.execute(delete(GrupoComposicao).where(GrupoComposicao.id == grupo.grupo.id))


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
    itens = obter(sessoes, composicao_id).avaliacoes
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
    itens = obter(sessoes, composicao_id).avaliacoes
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
    notas: dict[int, float] = field(default_factory=dict)  # avaliação (item_id) -> valor
    grupos: dict[int, float | None] = field(default_factory=dict)  # grupo_id -> nota do grupo
    media: float | None = None


def planilha(sessoes: sessionmaker, composicao_id: int, turma_id: int) -> list[LinhaAluno]:
    """Estudantes da turma com as notas de cada avaliação, de cada grupo e a média."""
    estrutura = obter(sessoes, composicao_id)
    itens = estrutura.avaliacoes
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
        linhas.append(
            LinhaAluno(
                e.matricula,
                e.nome,
                notas_aluno,
                {g.grupo.id: g.nota(notas_aluno) for g in estrutura.grupos},
                estrutura.media(notas_aluno),
            )
        )
    return linhas


def salvar_notas(sessoes: sessionmaker, composicao_id: int, turma_id: int, valores: dict[tuple[int, str], str]) -> list[str]:
    """Grava as notas digitadas {(item_id, matricula): '8,5'}. Vazio apaga a nota.

    Devolve a lista de erros (as notas válidas são gravadas mesmo assim).
    """
    itens = obter(sessoes, composicao_id).avaliacoes
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
                    raise ValueError(f"deve ficar entre 0 e {_fmt(item.valor_maximo)}")
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
