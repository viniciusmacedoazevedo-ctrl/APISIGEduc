"""Entrega dos dados à SEEC/SIGEduc: o que está pendente, carga manual pelo
administrador, janela agendada e o registro de cada carga.

Vão ao SIGEduc só a média bimestral (das turmas que o professor liberou) e a frequência
de cada aula. Um item já entregue com o mesmo valor não é reenviado; se o valor mudou
depois da entrega (nota corrigida, chamada refeita), volta a ser pendente.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import composicoes as comp
from .banco import (
    Aula,
    ComposicaoTurma,
    CredencialSigeduc,
    Entrega,
    EntregaItem,
    Envio,
    Estudante,
    Frequencia,
    Turma,
    Usuario,
)

log = logging.getLogger(__name__)
MODOS = {"arquivo": "Arquivo padronizado para a SEEC", "sigeduc": "Lançamento no SIGEduc pelo robô"}


@dataclass(frozen=True)
class Item:
    tipo: str  # media | frequencia
    turma_id: int
    matricula: str
    referencia: str
    valor: str

    @property
    def chave(self) -> tuple:
        return (self.tipo, self.turma_id, self.matricula, self.referencia)


@dataclass
class Contexto:
    """Dados de exibição e do arquivo para cada item."""

    turmas: dict[int, Turma]
    nomes: dict[tuple[int, str], str]
    professores: dict[int, Usuario]
    logins: dict[int, str]
    quantidades: dict[tuple[int, str], int]  # (turma, data) -> aulas


def itens_atuais(sessoes: sessionmaker) -> tuple[list[Item], Contexto]:
    """Tudo o que deveria estar no SIGEduc hoje: médias liberadas e chamadas feitas."""
    itens: dict[tuple, Item] = {}
    with sessoes() as sessao:
        turmas = {t.id: t for t in sessao.scalars(select(Turma))}
        nomes = {(e.turma_id, e.matricula): e.nome for e in sessao.scalars(select(Estudante))}
        professores = {u.id: u for u in sessao.scalars(select(Usuario))}
        logins = {c.usuario_id: c.login for c in sessao.scalars(select(CredencialSigeduc))}
        liberadas = sessao.execute(
            select(ComposicaoTurma.composicao_id, ComposicaoTurma.turma_id)
            .where(ComposicaoTurma.enviado_em.is_not(None))
            .order_by(ComposicaoTurma.composicao_id)
        ).all()
        aulas = sessao.execute(
            select(Aula.turma_id, Aula.data, Aula.quantidade, Frequencia.matricula, Frequencia.presente).join(
                Frequencia, Frequencia.aula_id == Aula.id
            )
        ).all()
    for composicao_id, turma_id in liberadas:  # a composição mais recente do bimestre prevalece
        estrutura = comp.obter(sessoes, composicao_id)
        ref = f"{estrutura.composicao.ano_letivo}-{estrutura.composicao.bimestre}"
        for linha in comp.planilha(sessoes, composicao_id, turma_id):
            if linha.media is not None:
                item = Item("media", turma_id, linha.matricula, ref, f"{linha.media:.1f}")
                itens[item.chave] = item
    quantidades = {}
    for turma_id, dia, quantidade, matricula, presente in aulas:
        quantidades[(turma_id, dia.isoformat())] = quantidade
        item = Item("frequencia", turma_id, matricula, dia.isoformat(), "P" if presente else "F")
        itens[item.chave] = item
    return list(itens.values()), Contexto(turmas, nomes, professores, logins, quantidades)


def _entregues(sessoes: sessionmaker) -> dict[tuple, str]:
    """{chave: último valor entregue}. No modo robô, só conta se o envio concluiu."""
    with sessoes() as sessao:
        linhas = sessao.execute(
            select(EntregaItem, Entrega.modo, Envio.status)
            .join(Entrega, Entrega.id == EntregaItem.entrega_id)
            .outerjoin(Envio, Envio.id == EntregaItem.envio_id)
            .order_by(EntregaItem.id)
        ).all()
    entregues = {}
    for item, modo, status in linhas:
        if modo == "arquivo" or status == "concluido":
            entregues[(item.tipo, item.turma_id, item.matricula, item.referencia)] = item.valor
    return entregues


def pendentes(sessoes: sessionmaker) -> tuple[list[Item], Contexto]:
    atuais, contexto = itens_atuais(sessoes)
    entregues = _entregues(sessoes)
    return [i for i in atuais if entregues.get(i.chave) != i.valor], contexto


@dataclass
class ResumoPendencia:
    turma: Turma
    professor: str
    medias: int
    frequencias: int


def resumo_por_turma(itens: list[Item], contexto: Contexto) -> list[ResumoPendencia]:
    por_turma: dict[int, ResumoPendencia] = {}
    for i in itens:
        turma = contexto.turmas[i.turma_id]
        r = por_turma.setdefault(
            i.turma_id, ResumoPendencia(turma, contexto.professores[turma.usuario_id].nome or contexto.professores[turma.usuario_id].email, 0, 0)
        )
        if i.tipo == "media":
            r.medias += 1
        else:
            r.frequencias += 1
    return sorted(por_turma.values(), key=lambda r: (r.professor, r.turma.codigo, r.turma.componente))


# --- arquivo padronizado ------------------------------------------------------------
LEIAME = """APISIGEduc - carga para a SEEC/SIGEduc
Gerado em {quando}. Entrega nº {numero}.

medias.csv      uma linha por estudante e bimestre: média bimestral (1 casa decimal)
frequencia.csv  uma linha por estudante e aula: P (presente) ou F (falta);
                quantidade_aulas = aulas geminadas daquela data

Separador ";", codificação UTF-8. Só constam dados novos ou alterados desde a
última entrega. Layout provisório: será ajustado ao formato definido pela SEEC.
"""


def _csv(cabecalho: list[str], linhas: list[list]) -> str:
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(cabecalho)
    escritor.writerows(linhas)
    return "﻿" + saida.getvalue()


def montar_arquivo(itens: list[Item], contexto: Contexto, numero: int, quando: datetime) -> bytes:
    medias, frequencias = [], []
    for i in sorted(itens, key=lambda i: (i.tipo, contexto.turmas[i.turma_id].codigo, i.referencia, i.matricula)):
        t = contexto.turmas[i.turma_id]
        base = [t.escola, t.codigo, t.componente]
        nome = contexto.nomes.get((i.turma_id, i.matricula), "")
        login = contexto.logins.get(t.usuario_id, "")
        if i.tipo == "media":
            ano, bimestre = i.referencia.split("-")
            medias.append(base + [ano, bimestre, i.matricula, nome, i.valor.replace(".", ","), login])
        else:
            frequencias.append(base + [i.referencia, contexto.quantidades.get((i.turma_id, i.referencia), 1), i.matricula, nome, i.valor, login])
    zip_ = io.BytesIO()
    with zipfile.ZipFile(zip_, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "medias.csv",
            _csv(["escola", "turma", "componente", "ano_letivo", "bimestre", "matricula", "estudante", "media", "professor_login_sigeduc"], medias),
        )
        z.writestr(
            "frequencia.csv",
            _csv(["escola", "turma", "componente", "data", "quantidade_aulas", "matricula", "estudante", "situacao", "professor_login_sigeduc"], frequencias),
        )
        z.writestr("LEIAME.txt", LEIAME.format(quando=f"{quando:%d/%m/%Y %H:%M}", numero=numero))
    return zip_.getvalue()


# --- carga ----------------------------------------------------------------------------
def executar(sessoes: sessionmaker, modo: str, criado_por_id: int | None, agora: datetime | None = None) -> Entrega | None:
    """Faz uma carga com tudo o que está pendente. None se não há nada pendente."""
    if modo not in MODOS:
        raise ValueError("Modo de carga inválido")
    agora = agora or datetime.now()
    itens, contexto = pendentes(sessoes)
    if modo == "sigeduc":
        itens = [i for i in itens if i.tipo == "media"]  # o robô ainda não lança frequência
    if not itens:
        return None
    with sessoes.begin() as sessao:
        entrega = Entrega(
            criado_por_id=criado_por_id,
            modo=modo,
            qtd_medias=sum(1 for i in itens if i.tipo == "media"),
            qtd_frequencias=sum(1 for i in itens if i.tipo == "frequencia"),
            criado_em=agora,
        )
        sessao.add(entrega)
        sessao.flush()
        envio_de: dict[tuple, int] = {}
        if modo == "arquivo":
            entrega.arquivo = montar_arquivo(itens, contexto, entrega.id, agora)
        else:
            envio_de = _enfileirar(sessao, itens, contexto)
            entrega.observacao = f"{len(set(envio_de.values()))} envio(s) na fila do robô"
        sessao.add_all(
            EntregaItem(
                entrega_id=entrega.id,
                tipo=i.tipo,
                turma_id=i.turma_id,
                matricula=i.matricula,
                referencia=i.referencia,
                valor=i.valor,
                envio_id=envio_de.get((i.turma_id, i.referencia)),
            )
            for i in itens
            if modo == "arquivo" or (i.turma_id, i.referencia) in envio_de
        )
        log.info("Entrega %d (%s): %d média(s), %d frequência(s)", entrega.id, modo, entrega.qtd_medias, entrega.qtd_frequencias)
        return entrega


def _enfileirar(sessao, itens: list[Item], contexto: Contexto) -> dict[tuple, int]:
    """Uma planilha de notas por turma e bimestre na fila do robô, com o login do professor."""
    grupos: dict[tuple, list[Item]] = {}
    for i in itens:
        grupos.setdefault((i.turma_id, i.referencia), []).append(i)
    envios = {}
    for (turma_id, ref), lista in grupos.items():
        t = contexto.turmas[turma_id]
        if t.usuario_id not in contexto.logins:
            log.warning("Turma %s sem senha do SIGEduc cadastrada; ficou pendente", t.codigo)
            continue
        _, bimestre = ref.split("-")
        dados = _csv(
            ["matricula", "nome", "turma", "disciplina", "bimestre", "nota"],
            [[i.matricula, contexto.nomes.get((turma_id, i.matricula), ""), t.codigo, t.componente, bimestre, i.valor.replace(".", ",")] for i in lista],
        ).encode("utf-8")
        envio = Envio(
            usuario_id=t.usuario_id,
            tipo="notas",
            arquivo_nome=f"Carga {bimestre}º bim - {t.codigo} - {t.componente}.csv",
            arquivo=dados,
            total=len(lista),
        )
        sessao.add(envio)
        sessao.flush()
        envios[(turma_id, ref)] = envio.id
    return envios


def listar(sessoes: sessionmaker, limite: int = 30) -> list[Entrega]:
    with sessoes() as sessao:
        return list(sessao.scalars(select(Entrega).order_by(Entrega.id.desc()).limit(limite)))


def obter(sessoes: sessionmaker, entrega_id: int) -> Entrega | None:
    with sessoes() as sessao:
        return sessao.get(Entrega, entrega_id)


# --- janela agendada --------------------------------------------------------------
def hora_da_janela(janela: str) -> tuple[int, int] | None:
    try:
        hora, minuto = (int(p) for p in janela.split(":"))
    except ValueError:
        return None
    return (hora, minuto) if 0 <= hora < 24 and 0 <= minuto < 60 else None


def executar_agendada(sessoes: sessionmaker, janela: str, modo: str, agora: datetime | None = None) -> Entrega | None:
    """Na janela do dia (a partir do horário, uma vez por dia), faz a carga agendada."""
    agora = agora or datetime.now()
    hm = hora_da_janela(janela)
    if hm is None or (agora.hour, agora.minute) < hm:
        return None
    inicio_do_dia = datetime.combine(agora.date(), datetime.min.time())
    with sessoes() as sessao:
        ja = sessao.scalar(
            select(Entrega.id).where(Entrega.criado_por_id.is_(None), Entrega.criado_em >= inicio_do_dia).limit(1)
        )
    if ja:
        return None
    return executar(sessoes, modo, None, agora)


def rodar_agendador(sessoes: sessionmaker, janela: str, modo: str, parar, intervalo: float = 60.0) -> None:
    log.info("Carga agendada diária às %s (%s)", janela, modo)
    while not parar.is_set():
        try:
            executar_agendada(sessoes, janela, modo)
        except Exception:
            log.exception("Erro na carga agendada")
        parar.wait(intervalo)
