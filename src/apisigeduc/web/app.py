"""Tela web: cada pessoa entra com seu e-mail, cadastra a senha do SIGEduc e
envia planilhas, que entram numa fila processada em segundo plano."""

from __future__ import annotations

import logging
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from .. import envios
from ..banco import Usuario
from ..config import Config, ConfigError
from ..usuarios import RepositorioUsuarios, UsuarioNaoEncontrado

log = logging.getLogger(__name__)

STATUS = {
    "pendente": "Na fila",
    "processando": "Processando",
    "concluido": "Concluído",
    "concluido_com_falhas": "Concluído com falhas",
    "falhou": "Falhou",
}
TIPOS = {"notas": "Notas", "frequencia": "Frequência"}

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["data"] = lambda d: d.strftime("%d/%m/%Y %H:%M") if d else "—"
templates.env.globals.update(STATUS=STATUS, TIPOS=TIPOS)


class NaoAutenticado(Exception):
    pass


def criar_app(
    config: Config,
    sessoes: sessionmaker,
    repo: RepositorioUsuarios,
    iniciar_worker: bool = True,
    cookie_seguro: bool = False,
) -> FastAPI:
    if not config.segredo_sessao:
        raise ConfigError(
            "Defina APISIGEDUC_SEGREDO_SESSAO no .env. Gere com: apisigeduc gerar-chave"
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
            avisar(request, "E-mail ou senha incorretos.", "erro")
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

    return app
