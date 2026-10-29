"""Tela do administrador: pendências para o SIGEduc, carga manual e histórico."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .. import sincronia
from ..banco import Usuario


def registrar(app: FastAPI, ctx: SimpleNamespace) -> None:
    sessoes = ctx.sessoes
    pagina, avisar, voltar, verificar_csrf = ctx.pagina, ctx.avisar, ctx.voltar, ctx.verificar_csrf
    admin_logado = ctx.admin_logado

    @app.get("/admin/sincronizacao", response_class=HTMLResponse)
    def tela(request: Request, usuario: Usuario = Depends(admin_logado)):
        itens, contexto = sincronia.pendentes(sessoes)
        return pagina(
            request,
            "sincronizacao.html",
            usuario,
            resumo=sincronia.resumo_por_turma(itens, contexto),
            total_medias=sum(1 for i in itens if i.tipo == "media"),
            total_frequencias=sum(1 for i in itens if i.tipo == "frequencia"),
            entregas=sincronia.listar(sessoes),
            janela=ctx.config.janela_sincronia,
            modo_agendado=ctx.config.modo_sincronia,
            MODOS=sincronia.MODOS,
        )

    @app.post("/admin/sincronizacao/carga")
    def carga(request: Request, modo: str = Form(...), csrf_token: str = Form(""), usuario: Usuario = Depends(admin_logado)):
        verificar_csrf(request, csrf_token)
        try:
            entrega = sincronia.executar(sessoes, modo, usuario.id)
        except ValueError as e:
            avisar(request, str(e), "erro")
            return voltar("/admin/sincronizacao")
        if entrega is None:
            avisar(request, "Nada pendente para enviar.")
        elif modo == "arquivo":
            avisar(request, f"Carga nº {entrega.id} gerada: {entrega.qtd_medias} média(s) e {entrega.qtd_frequencias} registro(s) de frequência. Baixe o arquivo no histórico.")
        else:
            avisar(request, f"Carga nº {entrega.id}: {entrega.qtd_medias} média(s) na fila do robô ({entrega.observacao}).")
        return voltar("/admin/sincronizacao")

    @app.get("/admin/sincronizacao/{entrega_id}/arquivo.zip")
    def baixar(entrega_id: int, usuario: Usuario = Depends(admin_logado)):
        entrega = sincronia.obter(sessoes, entrega_id)
        if entrega is None or entrega.arquivo is None:
            raise HTTPException(404, "Arquivo não encontrado")
        return Response(
            entrega.arquivo,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="carga-sigeduc-{entrega.id:04d}-{entrega.criado_em:%Y%m%d-%H%M}.zip"'},
        )
