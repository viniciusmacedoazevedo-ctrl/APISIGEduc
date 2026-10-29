"""Telas de composição de notas: criar a composição do bimestre, aplicar nas
turmas, lançar as notas por item e enviar as médias ao SIGEduc."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from .. import composicoes as comp
from .. import desempenho
from .. import diario
from .. import envios
from .. import planilha_notas
from .. import turmas as turmas_repo
from ..banco import Usuario


def registrar(app: FastAPI, ctx: SimpleNamespace) -> None:
    sessoes, repo = ctx.sessoes, ctx.repo
    pagina, avisar, voltar, verificar_csrf = ctx.pagina, ctx.avisar, ctx.voltar, ctx.verificar_csrf
    usuario_logado = ctx.usuario_logado
    minima = ctx.config.media_minima

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
        ligacao = next(l for l, t in comp.turmas_aplicadas(sessoes, composicao_id) if t.id == turma_id)
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
            marcados=diario.contagem_anotacoes(sessoes, usuario.id, [l.matricula for l in linhas]),
            com_foto=turmas_repo.com_foto(sessoes, [l.matricula for l in linhas]),
            liberado_em=ligacao.enviado_em,
            janela=ctx.config.janela_sincronia,
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

    # --- desempenho -----------------------------------------------------------
    @app.get("/notas/{composicao_id}/turmas/{turma_id}/desempenho", response_class=HTMLResponse)
    def desempenho_turma(request: Request, composicao_id: int, turma_id: int, usuario: Usuario = Depends(usuario_logado)):
        estrutura, turma = turma_aplicada(composicao_id, turma_id, usuario)
        linhas = comp.planilha(sessoes, composicao_id, turma_id)
        menor, maior = desempenho.rankings(linhas, minima)
        return pagina(
            request,
            "desempenho_turma.html",
            usuario,
            composicao=estrutura.composicao,
            turma=turma,
            linhas=linhas,
            resumo=desempenho.resumir([l.media for l in linhas], minima),
            avaliacoes=desempenho.por_avaliacao(estrutura, linhas),
            menor=menor,
            maior=maior,
            pendentes=[l for l in linhas if l.media is None],
            minima_rede=minima,
        )

    @app.get("/notas/{composicao_id}/desempenho", response_class=HTMLResponse)
    def desempenho_composicao(request: Request, composicao_id: int, usuario: Usuario = Depends(usuario_logado)):
        estrutura = composicao_permitida(composicao_id, usuario)
        por_turma, todas, codigo = [], [], {}
        for _, turma in comp.turmas_aplicadas(sessoes, composicao_id):
            linhas = comp.planilha(sessoes, composicao_id, turma.id)
            por_turma.append((turma, desempenho.resumir([l.media for l in linhas], minima)))
            for l in linhas:
                codigo[id(l)] = turma.codigo
            todas.extend(linhas)
        menor, maior = desempenho.rankings(todas, minima, limite=15, turma_de=lambda l: codigo[id(l)])
        return pagina(
            request,
            "desempenho_composicao.html",
            usuario,
            composicao=estrutura.composicao,
            turmas=por_turma,
            geral=desempenho.resumir([l.media for l in todas], minima),
            menor=menor,
            maior=maior,
            minima_rede=minima,
        )

    @app.get("/turmas/{turma_id}/desempenho", response_class=HTMLResponse)
    def desempenho_anual(request: Request, turma_id: int, usuario: Usuario = Depends(usuario_logado)):
        turma = ctx.turma_permitida(turma_id, usuario)
        bimestres, alunos, avisos = desempenho.anual(sessoes, turma.usuario_id, turma.id, minima)
        frequencia = diario.frequencia_da_turma(sessoes, turma.id)
        contagem = {s: sum(1 for a in alunos if a.situacao == s) for s in ("recuperacao", "atencao", "ok", "sem_notas")}
        return pagina(
            request,
            "desempenho_anual.html",
            usuario,
            turma=turma,
            bimestres=bimestres,
            alunos=alunos,
            avisos=avisos,
            contagem=contagem,
            minima_rede=minima,
            frequencia=frequencia,
            frequencia_minima=diario.FREQUENCIA_MINIMA,
        )

    @app.get("/notas/{composicao_id}/turmas/{turma_id}/planilha.xlsx")
    def baixar_planilha(composicao_id: int, turma_id: int, usuario: Usuario = Depends(usuario_logado)):
        estrutura, turma = turma_aplicada(composicao_id, turma_id, usuario)
        dados = planilha_notas.exportar(estrutura, turma, comp.planilha(sessoes, composicao_id, turma_id))
        return Response(
            dados,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="notas-{turma.codigo}-{estrutura.composicao.bimestre}bim.xlsx"'},
        )

    @app.post("/notas/{composicao_id}/turmas/{turma_id}/importar")
    async def importar_planilha(
        request: Request,
        composicao_id: int,
        turma_id: int,
        arquivo: UploadFile = File(...),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(usuario_logado),
    ):
        verificar_csrf(request, csrf_token)
        estrutura, turma = turma_aplicada(composicao_id, turma_id, usuario)
        destino = f"/notas/{composicao_id}/turmas/{turma_id}"
        try:
            valores = planilha_notas.importar(estrutura, turma, await arquivo.read(envios.TAMANHO_MAXIMO + 1))
        except planilha_notas.PlanilhaInvalida as e:
            avisar(request, str(e), "erro")
            return voltar(destino)
        erros = comp.salvar_notas(sessoes, composicao_id, turma_id, valores)
        for erro in erros:
            avisar(request, erro, "erro")
        avisar(request, f"Planilha importada: {len(valores) - len(erros)} nota(s) gravada(s).")
        return voltar(destino)

    @app.post("/notas/{composicao_id}/turmas/{turma_id}/liberar")
    def liberar_medias(
        request: Request,
        composicao_id: int,
        turma_id: int,
        so_completos: bool = Form(False),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(usuario_logado),
    ):
        """Libera as médias da turma para a próxima carga ao SIGEduc (janela agendada ou
        carga do administrador). Médias alteradas depois de entregues voltam a ser enviadas."""
        verificar_csrf(request, csrf_token)
        estrutura, turma = turma_aplicada(composicao_id, turma_id, usuario)
        destino = f"/notas/{composicao_id}/turmas/{turma_id}"
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
                f"\"Liberar só quem está completo\" (os demais entram quando forem completados).",
                "erro",
            )
            return voltar(destino)
        comp.liberar(sessoes, composicao_id, turma_id)
        janela = ctx.config.janela_sincronia
        quando = f"na carga das {janela}" if janela else "na próxima carga feita pelo administrador"
        avisar(request, f"{len(completos)} média(s) de {turma.codigo} liberadas. Vão ao SIGEduc {quando}.")
        return voltar(destino)

    @app.post("/notas/{composicao_id}/turmas/{turma_id}/recolher")
    def recolher_medias(
        request: Request, composicao_id: int, turma_id: int, csrf_token: str = Form(""), usuario: Usuario = Depends(usuario_logado)
    ):
        verificar_csrf(request, csrf_token)
        turma_aplicada(composicao_id, turma_id, usuario)
        comp.liberar(sessoes, composicao_id, turma_id, liberado=False)
        avisar(request, "Liberação cancelada: as médias desta turma não vão na próxima carga.")
        return voltar(f"/notas/{composicao_id}/turmas/{turma_id}")
