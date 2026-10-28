import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apisigeduc import envios
from apisigeduc.web.app import criar_app

EXEMPLOS = Path(__file__).parent.parent / "exemplos"


@pytest.fixture
def app(config, sessoes, repo):
    repo.criar("admin@escola.br", "Admin", "senha-admin", admin=True)
    repo.criar("prof@escola.br", "Prof", "senha-prof")
    return criar_app(config, sessoes, repo, iniciar_worker=False)


def _csrf(cliente, caminho="/login"):
    return re.search(r'name="csrf_token" value="([^"]+)"', cliente.get(caminho).text).group(1)


def _entrar(app, email, senha):
    cliente = TestClient(app)
    r = cliente.post("/login", data={"email": email, "senha": senha, "csrf_token": _csrf(cliente)})
    assert r.status_code == 200 and "Sair" in r.text, r.text
    return cliente


def test_exige_login(app):
    r = TestClient(app).get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_senha_errada_e_csrf(app):
    cliente = TestClient(app)
    r = cliente.post("/login", data={"email": "prof@escola.br", "senha": "x", "csrf_token": _csrf(cliente)})
    assert "E-mail ou senha incorretos" in r.text
    r = cliente.post("/login", data={"email": "prof@escola.br", "senha": "senha-prof", "csrf_token": "falso"})
    assert r.status_code == 400


def test_fluxo_do_professor(app, sessoes, repo):
    cliente = _entrar(app, "prof@escola.br", "senha-prof")
    assert "ainda não cadastrou sua senha do SIGEduc" in cliente.get("/").text
    assert cliente.get("/admin/usuarios").status_code == 403

    csrf = _csrf(cliente, "/")
    arquivo = {"arquivo": ("notas.csv", (EXEMPLOS / "notas.csv").read_bytes(), "text/csv")}

    # só conferir
    r = cliente.post("/envios", data={"tipo": "notas", "acao": "validar", "csrf_token": csrf}, files=arquivo)
    assert "3</strong> linha(s) válida(s)" in r.text and "20260001" in r.text

    # lançar sem credencial -> manda cadastrar
    r = cliente.post("/envios", data={"tipo": "notas", "acao": "enviar", "csrf_token": csrf}, files=arquivo)
    assert "Cadastre sua senha do SIGEduc" in r.text

    r = cliente.post("/credencial", data={"login": "prof.sigeduc", "senha": "segredo", "csrf_token": csrf})
    assert "Senha do SIGEduc salva" in r.text
    assert "segredo" not in cliente.get("/credencial").text

    r = cliente.post("/envios", data={"tipo": "notas", "acao": "enviar", "csrf_token": csrf}, files=arquivo)
    assert "na fila de lançamento" in r.text and "Na fila" in r.text
    envio = envios.listar(sessoes)[0]
    assert envio.total == 3 and envio.status == "pendente"
    assert cliente.get(f"/envios/{envio.id}").status_code == 200


def test_planilha_com_erro_nao_entra_na_fila(app, sessoes, repo):
    cliente = _entrar(app, "prof@escola.br", "senha-prof")
    repo.definir_credencial(repo.por_email("prof@escola.br").id, "p", "s")
    csrf = _csrf(cliente, "/")
    ruim = "matricula;turma;disciplina;bimestre;nota\n1;A;Mat;9;7\n2;A;Mat;1;8\n".encode()
    r = cliente.post(
        "/envios", data={"tipo": "notas", "acao": "enviar", "csrf_token": csrf}, files={"arquivo": ("r.csv", ruim)}
    )
    assert "Nada foi lançado" in r.text and "bimestre inválido" in r.text
    assert envios.listar(sessoes) == []

    r = cliente.post(
        "/envios",
        data={"tipo": "notas", "acao": "enviar", "ignorar_erros": "true", "csrf_token": csrf},
        files={"arquivo": ("r.csv", ruim)},
    )
    assert envios.listar(sessoes)[0].total == 1


def test_professor_nao_ve_envio_de_outro(app, sessoes, repo):
    admin = repo.por_email("admin@escola.br")
    envio_id = envios.criar(sessoes, admin.id, "notas", "x.csv", b"", 0)
    cliente = _entrar(app, "prof@escola.br", "senha-prof")
    assert cliente.get(f"/envios/{envio_id}").status_code == 404


def test_admin_gerencia_usuarios(app, repo):
    cliente = _entrar(app, "admin@escola.br", "senha-admin")
    csrf = _csrf(cliente, "/admin/usuarios")
    r = cliente.post(
        "/admin/usuarios",
        data={"email": "novo@escola.br", "nome": "Novo", "senha": "senha-novo", "csrf_token": csrf},
    )
    assert "novo@escola.br criado" in r.text
    novo = repo.por_email("novo@escola.br")
    cliente.post(f"/admin/usuarios/{novo.id}/ativo", data={"ativo": "false", "csrf_token": csrf})
    assert repo.autenticar("novo@escola.br", "senha-novo") is None
    _entrar(app, "prof@escola.br", "senha-prof")  # outros continuam entrando
