"""Telas do diário: chamada, aulas dadas e ficha do estudante com anotações."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import composicoes as comp
from .. import diario
from .. import turmas as turmas_repo
from ..banco import Usuario


def registrar(app: FastAPI, ctx: SimpleNamespace) -> None:
    sessoes = ctx.sessoes
    pagina, avisar, voltar, verificar_csrf = ctx.pagina, ctx.avisar, ctx.voltar, ctx.verificar_csrf
    usuario_logado, turma_permitida = ctx.usuario_logado, ctx.turma_permitida

    def ler_data(texto: str | None) -> date:
        if not texto:
            return date.today()
        try:
            return date.fromisoformat(texto)
        except ValueError:
            raise HTTPException(400, "Data inválida") from None

    # --- chamada e aulas ---------------------------------------------------------
    @app.get("/turmas/{turma_id}/aulas", response_class=HTMLResponse)
    def tela_aulas(request: Request, turma_id: int, usuario: Usuario = Depends(usuario_logado)):
        turma = turma_permitida(turma_id, usuario)
        frequencia = diario.frequencia_da_turma(sessoes, turma.id)
        estudantes = turmas_repo.estudantes(sessoes, turma.id)
        return pagina(
            request,
            "aulas.html",
            usuario,
            turma=turma,
            aulas=diario.aulas(sessoes, turma.id),
            estudantes=estudantes,
            frequencia=frequencia,
            abaixo=[e for e in estudantes if frequencia[e.matricula].abaixo_do_minimo],
            minima=diario.FREQUENCIA_MINIMA,
            hoje=date.today(),
        )

    @app.get("/turmas/{turma_id}/chamada", response_class=HTMLResponse)
    def tela_chamada(request: Request, turma_id: int, data: str | None = None, usuario: Usuario = Depends(usuario_logado)):
        turma = turma_permitida(turma_id, usuario)
        dia = ler_data(data)
        aula = diario.aula_do_dia(sessoes, turma.id, dia)
        estudantes = turmas_repo.estudantes(sessoes, turma.id)
        return pagina(
            request,
            "chamada.html",
            usuario,
            turma=turma,
            dia=dia,
            hoje=date.today(),
            aula=aula,
            presencas=diario.presencas(sessoes, aula.id) if aula else {},
            estudantes=estudantes,
            marcados=diario.contagem_anotacoes(sessoes, usuario.id, [e.matricula for e in estudantes]),
            CATEGORIAS=diario.CATEGORIAS,
        )

    @app.post("/turmas/{turma_id}/chamada")
    def salvar_chamada(
        request: Request,
        turma_id: int,
        data: str = Form(...),
        quantidade: int = Form(1),
        conteudo: str = Form(""),
        observacoes: str = Form(""),
        falta: list[str] = Form([]),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(usuario_logado),
    ):
        verificar_csrf(request, csrf_token)
        turma = turma_permitida(turma_id, usuario)
        dia = ler_data(data)
        try:
            diario.salvar_chamada(sessoes, turma.id, dia, quantidade, conteudo, observacoes, set(falta))
        except ValueError as e:
            avisar(request, str(e), "erro")
            return voltar(f"/turmas/{turma.id}/chamada?data={dia.isoformat()}")
        avisar(request, f"Chamada de {dia:%d/%m/%Y} salva: {len(falta)} falta(s).")
        return voltar(f"/turmas/{turma.id}/aulas")

    @app.post("/turmas/{turma_id}/aulas/{aula_id}/excluir")
    def excluir_aula(
        request: Request, turma_id: int, aula_id: int, csrf_token: str = Form(""), usuario: Usuario = Depends(usuario_logado)
    ):
        verificar_csrf(request, csrf_token)
        turma = turma_permitida(turma_id, usuario)
        aula = diario.obter_aula(sessoes, turma.id, aula_id)
        if aula is None:
            raise HTTPException(404, "Aula não encontrada")
        diario.excluir_aula(sessoes, aula.id)
        avisar(request, f"Aula de {aula.data:%d/%m/%Y} excluída, com a chamada.")
        return voltar(f"/turmas/{turma.id}/aulas")

    # --- ficha do estudante -----------------------------------------------------
    def estudante_permitido(matricula: str, usuario: Usuario):
        vinculos = diario.turmas_do_estudante(sessoes, None if usuario.admin else usuario.id, matricula)
        if not vinculos:
            raise HTTPException(404, "Estudante não encontrado nas suas turmas")
        return vinculos

    @app.get("/alunos/{matricula}", response_class=HTMLResponse)
    def ficha(request: Request, matricula: str, usuario: Usuario = Depends(usuario_logado)):
        vinculos = estudante_permitido(matricula, usuario)
        turmas = []
        for turma, _ in vinculos:
            medias = {}
            for estrutura, _n in comp.listar(sessoes, turma.usuario_id):
                if turma.id in {t.id for _, t in comp.turmas_aplicadas(sessoes, estrutura.composicao.id)}:
                    linha = next((l for l in comp.planilha(sessoes, estrutura.composicao.id, turma.id) if l.matricula == matricula), None)
                    if linha is not None and estrutura.composicao.bimestre not in medias:
                        medias[estrutura.composicao.bimestre] = linha.media
            turmas.append((turma, diario.frequencia_da_turma(sessoes, turma.id).get(matricula), medias))
        return pagina(
            request,
            "aluno.html",
            usuario,
            matricula=matricula,
            nome=vinculos[0][1].nome,
            turmas=turmas,
            anotacoes=diario.anotacoes(sessoes, usuario.id, matricula),
            CATEGORIAS=diario.CATEGORIAS,
            minima=ctx.config.media_minima,
            frequencia_minima=diario.FREQUENCIA_MINIMA,
            voltar_para=request.query_params.get("voltar", ""),
        )

    @app.post("/alunos/{matricula}/anotacoes")
    def anotar(
        request: Request,
        matricula: str,
        categoria: str = Form(...),
        texto: str = Form(""),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(usuario_logado),
    ):
        verificar_csrf(request, csrf_token)
        estudante_permitido(matricula, usuario)
        try:
            diario.anotar(sessoes, usuario.id, matricula, categoria, texto)
            avisar(request, "Anotação salva. Só você pode ver.")
        except ValueError as e:
            avisar(request, str(e), "erro")
        return voltar(f"/alunos/{matricula}")

    @app.post("/alunos/{matricula}/anotacoes/{anotacao_id}/excluir")
    def excluir_anotacao(
        request: Request, matricula: str, anotacao_id: int, csrf_token: str = Form(""), usuario: Usuario = Depends(usuario_logado)
    ):
        verificar_csrf(request, csrf_token)
        try:
            diario.excluir_anotacao(sessoes, usuario.id, anotacao_id)
            avisar(request, "Anotação excluída.")
        except LookupError as e:
            avisar(request, str(e), "erro")
        return voltar(f"/alunos/{matricula}")
