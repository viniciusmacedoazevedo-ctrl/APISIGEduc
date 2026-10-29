"""Telas de composição de notas: criar a composição do bimestre, aplicar nas
turmas, lançar as notas por item e enviar as médias ao SIGEduc."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .. import composicoes as comp
from .. import envios
from .. import turmas as turmas_repo
from ..banco import Usuario


def registrar(app: FastAPI, ctx: SimpleNamespace) -> None:
    sessoes, repo = ctx.sessoes, ctx.repo
    pagina, avisar, voltar, verificar_csrf = ctx.pagina, ctx.avisar, ctx.voltar, ctx.verificar_csrf
    usuario_logado = ctx.usuario_logado

    def composicao_permitida(composicao_id: int, usuario: Usuario) -> comp.Estrutura:
        try:
            estrutura = comp.obter(sessoes, composicao_id)
        except LookupError:
            raise HTTPException(404, "Composição não encontrada") from None
        if estrutura.composicao.usuario_id != usuario.id and not usuario.admin:
            raise HTTPException(404, "Composição não encontrada")
        return estrutura

    def turma_aplicada(composicao_id: int, turma_id: int, usuario: Usuario):
        estrutura = composicao_permitida(composicao_id, usuario)
        turma = ctx.turma_permitida(turma_id, usuario)
        if turma.id not in {t.id for _, t in comp.turmas_aplicadas(sessoes, composicao_id)}:
            raise HTTPException(404, "A composição não foi aplicada nesta turma")
        return estrutura, turma

    def ler_estrutura(texto: str) -> list[comp.GrupoNovo]:
        """JSON da tela: [{nome, valor, calculo, avaliacoes: [{nome, valor, padrao}]}]."""
        try:
            dados = json.loads(texto or "[]")
        except json.JSONDecodeError:
            raise ValueError("Estrutura da composição inválida") from None
        grupos = []
        for g in dados:
            avaliacoes = [
                comp.AvaliacaoNova(str(a.get("nome", "")), comp.ler_numero(a.get("valor")) or 0, comp.ler_numero(a.get("padrao")))
                for a in g.get("avaliacoes", [])
            ]
            grupos.append(comp.GrupoNovo(str(g.get("nome", "")), comp.ler_numero(g.get("valor")) or 0, str(g.get("calculo", "soma")), avaliacoes))
        return grupos

    @app.get("/notas", response_class=HTMLResponse)
    def tela_composicoes(request: Request, usuario: Usuario = Depends(usuario_logado)):
        return pagina(
            request,
            "notas.html",
            usuario,
            composicoes=comp.listar(sessoes, usuario.id),
            ano=date.today().year,
            CALCULOS=comp.CALCULOS,
        )

    @app.post("/notas")
    def criar_composicao(
        request: Request,
        nome: str = Form(""),
        ano_letivo: int = Form(...),
        bimestre: int = Form(...),
        calculo: str = Form("soma"),
        estrutura: str = Form("[]"),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(usuario_logado),
    ):
        verificar_csrf(request, csrf_token)
        try:
            composicao_id = comp.criar(sessoes, usuario.id, nome, ano_letivo, bimestre, calculo, ler_estrutura(estrutura))
        except ValueError as e:
            avisar(request, str(e), "erro")
            return voltar("/notas")
        avisar(request, "Composição criada. Agora escolha as turmas em que ela vale.")
        return voltar(f"/notas/{composicao_id}")

    @app.get("/notas/{composicao_id}", response_class=HTMLResponse)
    def tela_composicao(request: Request, composicao_id: int, usuario: Usuario = Depends(usuario_logado)):
        estrutura = composicao_permitida(composicao_id, usuario)
        composicao = estrutura.composicao
        aplicadas = comp.turmas_aplicadas(sessoes, composicao_id)
        progresso = {}
        for _, turma in aplicadas:
            linhas = comp.planilha(sessoes, composicao_id, turma.id)
            progresso[turma.id] = (sum(1 for l in linhas if l.media is not None), len(linhas))
        ja = {t.id for _, t in aplicadas}
        disponiveis = [t for t, _ in turmas_repo.listar(sessoes, composicao.usuario_id) if t.id not in ja]
        return pagina(
            request,
            "composicao.html",
            usuario,
            composicao=composicao,
            estrutura=estrutura,
            aplicadas=aplicadas,
            progresso=progresso,
            disponiveis=disponiveis,
            CALCULOS=comp.CALCULOS,
        )

    @app.post("/notas/{composicao_id}/avaliacoes")
    def adicionar_avaliacao(
        request: Request,
        composicao_id: int,
        grupo_id: int = Form(...),
        nome: str = Form(""),
        valor: str = Form(""),
        padrao: str = Form(""),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(usuario_logado),
    ):
        verificar_csrf(request, csrf_token)
        composicao_permitida(composicao_id, usuario)
        try:
            maximo = comp.ler_numero(valor)
            if maximo is None:
                raise ValueError("Informe quanto a avaliação vale")
            comp.adicionar_avaliacao(sessoes, composicao_id, grupo_id, comp.AvaliacaoNova(nome, maximo, comp.ler_numero(padrao)))
            avisar(request, f"Avaliação {nome.strip()} incluída.")
        except (ValueError, LookupError) as e:
            avisar(request, str(e), "erro")
        return voltar(f"/notas/{composicao_id}")

    @app.post("/notas/{composicao_id}/avaliacoes/{item_id}/remover")
    def remover_avaliacao(
        request: Request, composicao_id: int, item_id: int, csrf_token: str = Form(""), usuario: Usuario = Depends(usuario_logado)
    ):
        verificar_csrf(request, csrf_token)
        composicao_permitida(composicao_id, usuario)
        try:
            comp.remover_avaliacao(sessoes, composicao_id, item_id)
            avisar(request, "Avaliação removida, com as notas lançadas nela.")
        except (ValueError, LookupError) as e:
            avisar(request, str(e), "erro")
        return voltar(f"/notas/{composicao_id}")

    @app.post("/notas/{composicao_id}/turmas")
    def aplicar_turmas(
        request: Request,
        composicao_id: int,
        turma_id: list[int] = Form([]),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(usuario_logado),
    ):
        verificar_csrf(request, csrf_token)
        composicao_permitida(composicao_id, usuario)
        ids = [ctx.turma_permitida(t, usuario).id for t in turma_id]
        if not ids:
            avisar(request, "Marque pelo menos uma turma.", "erro")
        else:
            novas = comp.aplicar(sessoes, composicao_id, ids)
            avisar(request, f"Composição aplicada em {novas} turma(s). As notas para todos já foram lançadas.")
        return voltar(f"/notas/{composicao_id}")

    @app.post("/notas/{composicao_id}/duplicar")
    def duplicar(
        request: Request,
        composicao_id: int,
        nome: str = Form(""),
        bimestre: int = Form(...),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(usuario_logado),
    ):
        verificar_csrf(request, csrf_token)
        composicao_permitida(composicao_id, usuario)
        try:
            novo_id = comp.duplicar(sessoes, composicao_id, nome, bimestre)
        except ValueError as e:
            avisar(request, str(e), "erro")
            return voltar(f"/notas/{composicao_id}")
        avisar(request, "Composição copiada. Escolha as turmas.")
        return voltar(f"/notas/{novo_id}")

    @app.post("/notas/{composicao_id}/excluir")
    def excluir(request: Request, composicao_id: int, csrf_token: str = Form(""), usuario: Usuario = Depends(usuario_logado)):
        verificar_csrf(request, csrf_token)
        composicao_permitida(composicao_id, usuario)
        comp.excluir(sessoes, composicao_id)
        avisar(request, "Composição excluída, com as notas lançadas nela.")
        return voltar("/notas")

    # --- lançamento por turma ----------------------------------------------
    @app.get("/notas/{composicao_id}/turmas/{turma_id}", response_class=HTMLResponse)
    def tela_lancamento(request: Request, composicao_id: int, turma_id: int, usuario: Usuario = Depends(usuario_logado)):
        estrutura, turma = turma_aplicada(composicao_id, turma_id, usuario)
        linhas = comp.planilha(sessoes, composicao_id, turma_id)
        regras = {
            "calculo": estrutura.composicao.calculo,
            "grupos": [
                {"id": g.grupo.id, "valor": g.grupo.valor, "calculo": g.grupo.calculo, "avaliacoes": [a.id for a in g.avaliacoes]}
                for g in estrutura.grupos
            ],
        }
        return pagina(
            request,
            "lancamento.html",
            usuario,
            composicao=estrutura.composicao,
            estrutura=estrutura,
            regras=regras,
            turma=turma,
            linhas=linhas,
            completos=sum(1 for l in linhas if l.media is not None),
        )

    @app.post("/notas/{composicao_id}/turmas/{turma_id}")
    async def salvar_lancamento(request: Request, composicao_id: int, turma_id: int, usuario: Usuario = Depends(usuario_logado)):
        formulario = await request.form()
        verificar_csrf(request, str(formulario.get("csrf_token", "")))
        turma_aplicada(composicao_id, turma_id, usuario)
        valores = {}
        for chave, valor in formulario.items():
            if chave.startswith("n_"):  # n_<item_id>_<matricula>
                _, item_id, matricula = chave.split("_", 2)
                valores[(int(item_id), matricula)] = str(valor)
        erros = comp.salvar_notas(sessoes, composicao_id, turma_id, valores)
        for erro in erros:
            avisar(request, erro, "erro")
        if not erros:
            avisar(request, "Notas salvas.")
        return voltar(f"/notas/{composicao_id}/turmas/{turma_id}")

    @app.post("/notas/{composicao_id}/turmas/{turma_id}/remover")
    def remover_turma(
        request: Request, composicao_id: int, turma_id: int, csrf_token: str = Form(""), usuario: Usuario = Depends(usuario_logado)
    ):
        verificar_csrf(request, csrf_token)
        turma_aplicada(composicao_id, turma_id, usuario)
        comp.remover_turma(sessoes, composicao_id, turma_id)
        avisar(request, "Turma retirada da composição (as notas dela foram apagadas).")
        return voltar(f"/notas/{composicao_id}")

    @app.get("/notas/{composicao_id}/turmas/{turma_id}/medias.csv")
    def baixar_medias(composicao_id: int, turma_id: int, usuario: Usuario = Depends(usuario_logado)):
        estrutura, turma = turma_aplicada(composicao_id, turma_id, usuario)
        composicao = estrutura.composicao
        dados = comp.csv_medias(composicao, turma, comp.planilha(sessoes, composicao_id, turma_id))
        return Response(
            dados,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="medias-{turma.codigo}-{composicao.bimestre}bim.csv"'},
        )

    @app.post("/notas/{composicao_id}/turmas/{turma_id}/enviar")
    def enviar_medias(
        request: Request,
        composicao_id: int,
        turma_id: int,
        so_completos: bool = Form(False),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(usuario_logado),
    ):
        verificar_csrf(request, csrf_token)
        estrutura, turma = turma_aplicada(composicao_id, turma_id, usuario)
        composicao = estrutura.composicao
        destino = f"/notas/{composicao_id}/turmas/{turma_id}"
        if repo.dados_credencial(usuario.id) is None:
            avisar(request, "Cadastre sua senha do SIGEduc antes de enviar as médias.", "erro")
            return voltar("/credencial")
        linhas = comp.planilha(sessoes, composicao_id, turma_id)
        completos = [l for l in linhas if l.media is not None]
        faltando = len(linhas) - len(completos)
        if not completos:
            avisar(request, "Nenhum estudante tem todas as notas lançadas.", "erro")
            return voltar(destino)
        if faltando and not so_completos:
            avisar(
                request,
                f"{faltando} estudante(s) ainda sem todas as notas. Complete ou marque "
                f"\"Enviar só quem está completo\".",
                "erro",
            )
            return voltar(destino)
        nome = f"Médias {composicao.bimestre}º bim - {turma.codigo} - {turma.componente}.csv"
        envio_id = envios.criar(sessoes, usuario.id, "notas", nome, comp.csv_medias(composicao, turma, linhas), len(completos))
        comp.marcar_enviado(sessoes, composicao_id, turma_id)
        avisar(request, f"{len(completos)} média(s) da turma {turma.codigo} na fila de lançamento no SIGEduc (envio nº {envio_id}).")
        return voltar(destino)
