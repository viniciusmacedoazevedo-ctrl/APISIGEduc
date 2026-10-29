"""Tela web: cada pessoa entra com seu e-mail, cadastra a senha do SIGEduc e
envia planilhas, que entram numa fila processada em segundo plano."""

from __future__ import annotations

import csv
import io
import logging
import re
import secrets
import unicodedata
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from .. import envios
from .. import turmas as turmas_repo
from ..banco import Turma, Usuario
from ..modelos import descrever_turma
from ..config import Config, ConfigError
from ..usuarios import RepositorioUsuarios, UsuarioNaoEncontrado
from . import notas as notas_web

log = logging.getLogger(__name__)

STATUS = {
    "pendente": "Na fila",
    "processando": "Processando",
    "concluido": "Concluído",
    "concluido_com_falhas": "Concluído com falhas",
    "falhou": "Falhou",
}
TIPOS_PLANILHA = {"notas": "Notas", "frequencia": "Frequência"}
TIPOS = {**TIPOS_PLANILHA, "turmas": "Importar turmas"}
BIMESTRES = {1: "1º bimestre", 2: "2º bimestre", 3: "3º bimestre", 4: "4º bimestre"}
CALCULO_NOMES = {"soma": "soma", "media": "média"}

PASTA_WEB = Path(__file__).parent
templates = Jinja2Templates(directory=PASTA_WEB / "templates")
templates.env.filters["data"] = lambda d: d.strftime("%d/%m/%Y %H:%M") if d else "—"
templates.env.globals.update(STATUS=STATUS, TIPOS=TIPOS, TIPOS_PLANILHA=TIPOS_PLANILHA, BIMESTRES=BIMESTRES, CALCULO_NOMES=CALCULO_NOMES)
templates.env.filters["num"] = lambda v: "" if v is None else f"{v:g}".replace(".", ",")
templates.env.filters["descrever_turma"] = descrever_turma


def _csv(nome: str, cabecalho: list[str], linhas: list[list]) -> Response:
    """CSV separado por ; com BOM, para abrir direto no Excel."""
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(cabecalho)
    escritor.writerows(linhas)
    seguro = re.sub(r"[^A-Za-z0-9._-]+", "-", unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode())
    return Response(
        ("\ufeff" + saida.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{seguro}"'},
    )


class NaoAutenticado(Exception):
    pass


def criar_app(
    config: Config,
    sessoes: sessionmaker,
    repo: RepositorioUsuarios,
    iniciar_worker: bool = True,
    cookie_seguro: bool = False,
) -> FastAPI:
    if len(config.segredo_sessao) < 32:
        raise ConfigError(
            "APISIGEDUC_SEGREDO_SESSAO ausente ou curto demais no .env. Use o valor gerado por: "
            "apisigeduc gerar-chave"
        )

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        parar = threading.Event()
        if iniciar_worker:
            threading.Thread(
                target=envios.rodar_worker, args=(sessoes, repo, config, parar), daemon=True
            ).start()
        yield
        parar.set()

    app = FastAPI(title="APISIGEduc", lifespan=ciclo_de_vida, docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.segredo_sessao,
        session_cookie="apisigeduc_sessao",
        max_age=8 * 60 * 60,
        same_site="lax",
        https_only=cookie_seguro,
    )

    # --- auxiliares ---------------------------------------------------------
    @app.exception_handler(NaoAutenticado)
    async def _redirecionar_login(request: Request, exc: NaoAutenticado):
        return RedirectResponse("/login", status_code=303)

    def usuario_logado(request: Request) -> Usuario:
        usuario_id = request.session.get("usuario_id")
        if usuario_id is None:
            raise NaoAutenticado()
        try:
            usuario = repo.obter(usuario_id)
        except UsuarioNaoEncontrado:
            usuario = None
        if usuario is None or not usuario.ativo:
            request.session.clear()
            raise NaoAutenticado()
        return usuario

    def admin_logado(usuario: Usuario = Depends(usuario_logado)) -> Usuario:
        if not usuario.admin:
            raise HTTPException(403, "Acesso restrito a administradores")
        return usuario

    def csrf(request: Request) -> str:
        if "csrf" not in request.session:
            request.session["csrf"] = secrets.token_urlsafe(32)
        return request.session["csrf"]

    def verificar_csrf(request: Request, token: str) -> None:
        if not token or not secrets.compare_digest(token, request.session.get("csrf", "")):
            raise HTTPException(400, "Formulário expirado. Recarregue a página e tente de novo.")

    def avisar(request: Request, texto: str, tipo: str = "ok") -> None:
        request.session.setdefault("avisos", []).append([tipo, texto])

    def pagina(request: Request, modelo: str, usuario: Usuario | None = None, **contexto):
        contexto.update(
            usuario=usuario,
            csrf=csrf(request),
            avisos=request.session.pop("avisos", []),
        )
        return templates.TemplateResponse(request, modelo, contexto)

    def voltar(caminho: str) -> RedirectResponse:
        return RedirectResponse(caminho, status_code=303)

    # --- app instalável (PWA) ---------------------------------------------------
    app.mount("/static", StaticFiles(directory=PASTA_WEB / "static"), name="static")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        # servido na raiz para controlar o app inteiro; sem cache para atualizar logo
        return FileResponse(
            PASTA_WEB / "static" / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"}
        )

    # --- login --------------------------------------------------------------
    @app.get("/saude", response_class=PlainTextResponse)
    def saude():
        return "ok"

    @app.get("/login", response_class=HTMLResponse)
    def tela_login(request: Request):
        return pagina(request, "login.html")

    @app.post("/login")
    def entrar(request: Request, email: str = Form(...), senha: str = Form(...), csrf_token: str = Form("")):
        verificar_csrf(request, csrf_token)
        usuario = repo.autenticar(email, senha)
        if usuario is None:
            avisar(request, "E-mail ou senha incorretos. Confira e tente de novo; se esqueceu a senha, peça ao administrador para redefini-la.", "erro")
            return voltar("/login")
        request.session.clear()
        request.session["usuario_id"] = usuario.id
        log.info("Login na tela web: %s", usuario.email)
        return voltar("/")

    @app.post("/sair")
    def sair(request: Request, csrf_token: str = Form("")):
        verificar_csrf(request, csrf_token)
        request.session.clear()
        return voltar("/login")

    # --- painel e envios ----------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def painel(request: Request, usuario: Usuario = Depends(usuario_logado)):
        lista = envios.listar(sessoes, None if usuario.admin else usuario.id)
        nomes = {u.id: u.nome or u.email for u in repo.listar()} if usuario.admin else {}
        return pagina(
            request,
            "painel.html",
            usuario,
            envios=lista,
            nomes=nomes,
            credencial=repo.dados_credencial(usuario.id),
            atualizar=any(e.status in ("pendente", "processando") for e in lista),
        )

    @app.post("/envios", response_class=HTMLResponse)
    async def enviar(
        request: Request,
        tipo: str = Form(...),
        arquivo: UploadFile = File(...),
        acao: str = Form("validar"),
        ignorar_erros: bool = Form(False),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(usuario_logado),
    ):
        verificar_csrf(request, csrf_token)
        dados = await arquivo.read(envios.TAMANHO_MAXIMO + 1)
        nome = arquivo.filename or "planilha"
        try:
            leitura = envios.validar(tipo, nome, dados)
        except Exception as e:
            avisar(request, f"Não foi possível ler {nome}: {e}", "erro")
            return voltar("/")

        if acao == "enviar" and (not leitura.erros or ignorar_erros):
            if repo.dados_credencial(usuario.id) is None:
                avisar(request, "Cadastre sua senha do SIGEduc antes de enviar planilhas.", "erro")
                return voltar("/credencial")
            if not leitura.registros:
                avisar(request, "A planilha não tem nenhuma linha válida.", "erro")
                return voltar("/")
            envio_id = envios.criar(sessoes, usuario.id, tipo, nome, dados, len(leitura.registros))
            avisar(request, f"Planilha {nome} na fila de lançamento (envio nº {envio_id}).")
            return voltar("/")

        return pagina(
            request, "validacao.html", usuario, nome=nome, tipo=tipo, leitura=leitura, tentou_enviar=acao == "enviar"
        )

    @app.get("/envios/{envio_id}", response_class=HTMLResponse)
    def detalhe(request: Request, envio_id: int, usuario: Usuario = Depends(usuario_logado)):
        envio = envios.obter(sessoes, envio_id)
        if envio is None or (envio.usuario_id != usuario.id and not usuario.admin):
            raise HTTPException(404, "Envio não encontrado")
        return pagina(
            request,
            "envio.html",
            usuario,
            envio=envio,
            dono=repo.obter(envio.usuario_id),
            atualizar=envio.status in ("pendente", "processando"),
        )

    # --- turmas e estudantes ------------------------------------------------
    def turma_permitida(turma_id: int, usuario: Usuario) -> Turma:
        turma = turmas_repo.obter(sessoes, turma_id)
        if turma is None or (turma.usuario_id != usuario.id and not usuario.admin):
            raise HTTPException(404, "Turma não encontrada")
        return turma

    @app.get("/turmas", response_class=HTMLResponse)
    def tela_turmas(request: Request, usuario: Usuario = Depends(usuario_logado)):
        lista = turmas_repo.listar(sessoes, None if usuario.admin else usuario.id)
        pendentes = [
            e for e in envios.listar(sessoes, usuario.id, limite=5)
            if e.tipo == "turmas" and e.status in ("pendente", "processando")
        ]
        nomes = {u.id: u.nome or u.email for u in repo.listar()} if usuario.admin else {}
        return pagina(
            request,
            "turmas.html",
            usuario,
            turmas=lista,
            nomes=nomes,
            pendente=pendentes[0] if pendentes else None,
            credencial=repo.dados_credencial(usuario.id),
            atualizar=bool(pendentes),
        )

    @app.post("/turmas/sincronizar")
    def sincronizar_turmas(request: Request, csrf_token: str = Form(""), usuario: Usuario = Depends(usuario_logado)):
        verificar_csrf(request, csrf_token)
        if repo.dados_credencial(usuario.id) is None:
            avisar(request, "Cadastre sua senha do SIGEduc antes de buscar as turmas.", "erro")
            return voltar("/credencial")
        envio_id, criado = envios.pedir_sincronizacao(sessoes, usuario.id)
        if criado:
            avisar(request, f"Busca de turmas e estudantes no SIGEduc na fila (nº {envio_id}).")
        else:
            avisar(request, f"Já existe uma busca em andamento (nº {envio_id}).", "erro")
        return voltar("/turmas")

    @app.get("/turmas/{turma_id}", response_class=HTMLResponse)
    def detalhe_turma(request: Request, turma_id: int, usuario: Usuario = Depends(usuario_logado)):
        from .. import composicoes as comp

        turma = turma_permitida(turma_id, usuario)
        aplicadas = [
            e.composicao for e, _ in comp.listar(sessoes, turma.usuario_id)
            if turma.id in {t.id for _, t in comp.turmas_aplicadas(sessoes, e.composicao.id)}
        ]
        return pagina(
            request, "turma.html", usuario, turma=turma, estudantes=turmas_repo.estudantes(sessoes, turma.id), composicoes=aplicadas
        )

    @app.get("/turmas/{turma_id}/{arquivo}.csv")
    def baixar_turma(turma_id: int, arquivo: str, usuario: Usuario = Depends(usuario_logado)):
        turma = turma_permitida(turma_id, usuario)
        alunos = turmas_repo.estudantes(sessoes, turma.id)
        base = f"{turma.codigo}-{turma.componente}"
        if arquivo == "estudantes":
            return _csv(f"{base}-estudantes.csv", ["matricula", "nome"], [[a.matricula, a.nome] for a in alunos])
        if arquivo == "modelo-notas":
            return _csv(
                f"{base}-notas.csv",
                ["matricula", "nome", "turma", "disciplina", "bimestre", "nota"],
                [[a.matricula, a.nome, turma.codigo, turma.componente, "", ""] for a in alunos],
            )
        if arquivo == "modelo-frequencia":
            return _csv(
                f"{base}-frequencia.csv",
                ["matricula", "nome", "turma", "disciplina", "data", "presenca"],
                [[a.matricula, a.nome, turma.codigo, turma.componente, "", "P"] for a in alunos],
            )
        raise HTTPException(404, "Arquivo não encontrado")

    # --- credencial do SIGEduc ----------------------------------------------
    @app.get("/credencial", response_class=HTMLResponse)
    def tela_credencial(request: Request, usuario: Usuario = Depends(usuario_logado)):
        return pagina(request, "credencial.html", usuario, credencial=repo.dados_credencial(usuario.id))

    @app.post("/credencial")
    def salvar_credencial(
        request: Request,
        login: str = Form(...),
        senha: str = Form(...),
        escola: str = Form(""),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(usuario_logado),
    ):
        verificar_csrf(request, csrf_token)
        try:
            repo.definir_credencial(usuario.id, login, senha, escola)
        except ValueError as e:
            avisar(request, str(e), "erro")
            return voltar("/credencial")
        avisar(request, "Senha do SIGEduc salva (criptografada).")
        return voltar("/")

    # --- conta --------------------------------------------------------------
    @app.get("/conta", response_class=HTMLResponse)
    def tela_conta(request: Request, usuario: Usuario = Depends(usuario_logado)):
        return pagina(request, "conta.html", usuario)

    @app.post("/conta/senha")
    def trocar_senha(
        request: Request,
        atual: str = Form(...),
        nova: str = Form(...),
        confirmacao: str = Form(...),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(usuario_logado),
    ):
        verificar_csrf(request, csrf_token)
        if repo.autenticar(usuario.email, atual) is None:
            avisar(request, "Senha atual incorreta.", "erro")
        elif nova != confirmacao:
            avisar(request, "A nova senha e a confirmação não conferem.", "erro")
        else:
            try:
                repo.alterar_senha(usuario.id, nova)
                avisar(request, "Senha de acesso alterada.")
                return voltar("/")
            except ValueError as e:
                avisar(request, str(e), "erro")
        return voltar("/conta")

    # --- administração ------------------------------------------------------
    @app.get("/admin/usuarios", response_class=HTMLResponse)
    def tela_usuarios(request: Request, usuario: Usuario = Depends(admin_logado)):
        lista = repo.listar()
        credenciais = {u.id: repo.dados_credencial(u.id) for u in lista}
        return pagina(request, "admin_usuarios.html", usuario, usuarios=lista, credenciais=credenciais)

    @app.post("/admin/usuarios")
    def criar_usuario(
        request: Request,
        email: str = Form(...),
        nome: str = Form(""),
        senha: str = Form(...),
        admin: bool = Form(False),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(admin_logado),
    ):
        verificar_csrf(request, csrf_token)
        try:
            novo = repo.criar(email, nome, senha, admin)
            avisar(request, f"Usuário {novo.email} criado. Informe a senha inicial a ele.")
        except ValueError as e:
            avisar(request, str(e), "erro")
        return voltar("/admin/usuarios")

    @app.post("/admin/usuarios/{usuario_id}/ativo")
    def alternar_ativo(
        request: Request,
        usuario_id: int,
        ativo: bool = Form(False),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(admin_logado),
    ):
        verificar_csrf(request, csrf_token)
        if usuario_id == usuario.id and not ativo:
            avisar(request, "Você não pode desativar a si mesmo.", "erro")
        else:
            repo.definir_ativo(usuario_id, ativo)
            avisar(request, "Usuário " + ("reativado." if ativo else "desativado."))
        return voltar("/admin/usuarios")

    @app.post("/admin/usuarios/{usuario_id}/senha")
    def redefinir_senha(
        request: Request,
        usuario_id: int,
        senha: str = Form(...),
        csrf_token: str = Form(""),
        usuario: Usuario = Depends(admin_logado),
    ):
        verificar_csrf(request, csrf_token)
        try:
            repo.alterar_senha(usuario_id, senha)
            avisar(request, "Senha de acesso redefinida.")
        except ValueError as e:
            avisar(request, str(e), "erro")
        return voltar("/admin/usuarios")

    notas_web.registrar(
        app,
        SimpleNamespace(
            sessoes=sessoes,
            repo=repo,
            pagina=pagina,
            avisar=avisar,
            voltar=voltar,
            verificar_csrf=verificar_csrf,
            usuario_logado=usuario_logado,
            turma_permitida=turma_permitida,
        ),
    )
    return app
