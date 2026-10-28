"""Fila de planilhas enviadas pela web e o processamento em segundo plano."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .banco import Envio
from .config import Config
from .planilhas import CONVERSORES, converter, ler_conteudo
from .usuarios import RepositorioUsuarios

log = logging.getLogger(__name__)

TAMANHO_MAXIMO = 5 * 1024 * 1024  # 5 MB


def _executores() -> dict[str, Callable]:
    from .tarefas.frequencia import lancar_frequencia
    from .tarefas.notas import lancar_notas

    return {"notas": lancar_notas, "frequencia": lancar_frequencia}


def validar(tipo: str, nome: str, dados: bytes):
    """Lê e valida a planilha. Devolve o ResultadoLeitura (registros + erros)."""
    if tipo not in CONVERSORES:
        raise ValueError(f"Tipo inválido: {tipo}")
    if len(dados) > TAMANHO_MAXIMO:
        raise ValueError("Arquivo maior que 5 MB")
    return converter(ler_conteudo(nome, dados), CONVERSORES[tipo])


def criar(sessoes: sessionmaker, usuario_id: int, tipo: str, nome: str, dados: bytes, total: int) -> int:
    with sessoes.begin() as sessao:
        envio = Envio(usuario_id=usuario_id, tipo=tipo, arquivo_nome=nome, arquivo=dados, total=total)
        sessao.add(envio)
        sessao.flush()
        return envio.id


def listar(sessoes: sessionmaker, usuario_id: int | None = None, limite: int = 50) -> list[Envio]:
    consulta = select(Envio).order_by(Envio.id.desc()).limit(limite)
    if usuario_id is not None:
        consulta = consulta.where(Envio.usuario_id == usuario_id)
    with sessoes() as sessao:
        return list(sessao.scalars(consulta))


def obter(sessoes: sessionmaker, envio_id: int) -> Envio | None:
    with sessoes() as sessao:
        return sessao.get(Envio, envio_id)


def _pegar_proximo(sessoes: sessionmaker) -> Envio | None:
    """Marca o envio pendente mais antigo como 'processando'.

    FOR UPDATE SKIP LOCKED garante que dois workers não peguem o mesmo envio.
    """
    with sessoes.begin() as sessao:
        envio = sessao.scalar(
            select(Envio)
            .where(Envio.status == "pendente")
            .order_by(Envio.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if envio is None:
            return None
        envio.status = "processando"
        envio.iniciado_em = datetime.now()
        return envio


def _finalizar(sessoes: sessionmaker, envio_id: int, **campos) -> None:
    with sessoes.begin() as sessao:
        envio = sessao.get(Envio, envio_id)
        for nome, valor in campos.items():
            setattr(envio, nome, valor)
        envio.concluido_em = datetime.now()


def processar_proximo(
    sessoes: sessionmaker,
    repo: RepositorioUsuarios,
    config: Config,
    abrir_cliente: Callable | None = None,
    executores: dict[str, Callable] | None = None,
) -> bool:
    """Processa um envio pendente. Devolve False se a fila estiver vazia."""
    envio = _pegar_proximo(sessoes)
    if envio is None:
        return False
    log.info("Processando envio %d (%s, %s)", envio.id, envio.tipo, envio.arquivo_nome)

    if abrir_cliente is None:
        from .cliente import SigeducCliente as abrir_cliente
    executores = executores or _executores()

    try:
        leitura = validar(envio.tipo, envio.arquivo_nome, envio.arquivo)
        credencial = repo.credencial(envio.usuario_id)
        with abrir_cliente(config, credencial) as cliente:
            cliente.login()
            relatorio = executores[envio.tipo](cliente, leitura.registros)
        falhas = leitura.erros + relatorio.falhas
        _finalizar(
            sessoes,
            envio.id,
            status="concluido_com_falhas" if falhas else "concluido",
            sucesso=relatorio.sucesso,
            falhas="\n".join(falhas),
            mensagem=relatorio.resumo(),
        )
    except Exception as e:
        log.exception("Envio %d falhou", envio.id)
        _finalizar(sessoes, envio.id, status="falhou", mensagem=str(e).splitlines()[0] if str(e) else repr(e))
    return True


def rodar_worker(
    sessoes: sessionmaker,
    repo: RepositorioUsuarios,
    config: Config,
    parar: threading.Event,
    intervalo: float = 5.0,
) -> None:
    """Processa a fila até `parar` ser sinalizado."""
    log.info("Worker iniciado")
    while not parar.is_set():
        try:
            if processar_proximo(sessoes, repo, config):
                continue
        except Exception:
            log.exception("Erro no worker")
        parar.wait(intervalo)
    log.info("Worker encerrado")
