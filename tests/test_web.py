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


@pytest.fixture
def app_gravando(config, sessoes, repo):
    """Gravação no SIGEduc ligada: só para um SIGEduc de testes, nunca no real."""
    from dataclasses import replace

    repo.criar("admin@escola.br", "Admin", "senha-admin", admin=True)
    repo.criar("prof@escola.br", "Prof", "senha-prof")
    return criar_app(replace(config, sigeduc_somente_leitura=False), sessoes, repo, iniciar_worker=False)


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


def test_por_padrao_nao_grava_no_sigeduc(app, sessoes, repo):
    cliente = _entrar(app, "prof@escola.br", "senha-prof")
    repo.definir_credencial(repo.por_email("prof@escola.br").id, "p", "s")
    csrf = _csrf(cliente, "/")
    arquivo = {"arquivo": ("notas.csv", (EXEMPLOS / "notas.csv").read_bytes(), "text/csv")}
    r = cliente.post("/envios", data={"tipo": "notas", "acao": "enviar", "csrf_token": csrf}, files=arquivo)
    assert "só consulta o SIGEduc" in r.text and envios.listar(sessoes) == []


def test_fluxo_do_professor(app_gravando, sessoes, repo):
    app = app_gravando
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


def test_planilha_com_erro_nao_entra_na_fila(app_gravando, sessoes, repo):
    app = app_gravando
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


def test_recusa_segredo_de_sessao_fraco(config, sessoes, repo):
    from dataclasses import replace

    from apisigeduc.config import ConfigError

    with pytest.raises(ConfigError):
        criar_app(replace(config, segredo_sessao="@hiperlink"), sessoes, repo, iniciar_worker=False)


def test_turmas_na_web(app, sessoes, repo):
    from apisigeduc import turmas as turmas_repo
    from apisigeduc.modelos import Estudante, TurmaPortal

    prof = repo.por_email("prof@escola.br")
    turmas_repo.salvar(
        sessoes,
        prof.id,
        [(TurmaPortal("EFV6A", "ESCOLA X", "EF", "6º ANO", "História", 2), [Estudante("202600000001", "ANA CLARA SOUZA")])],
    )
    [(turma, _)] = turmas_repo.listar(sessoes, prof.id)

    cliente = _entrar(app, "prof@escola.br", "senha-prof")
    r = cliente.get("/turmas")
    assert "EFV6A" in r.text and "vespertino" in r.text and "portal: 2" in r.text
    assert "Cadastre sua" in r.text  # sem senha do SIGEduc, sem botão de busca

    r = cliente.get(f"/turmas/{turma.id}")
    assert "ANA CLARA SOUZA" in r.text and "202600000001" in r.text

    r = cliente.get(f"/turmas/{turma.id}/modelo-notas.csv")
    assert r.headers["content-type"].startswith("text/csv")
    linhas = r.content.decode("utf-8-sig").splitlines()
    assert linhas == ["matricula;nome;turma;disciplina;bimestre;nota", "202600000001;ANA CLARA SOUZA;EFV6A;História;;"]

    # pedir a busca
    repo.definir_credencial(prof.id, "p", "s")
    csrf = _csrf(cliente, "/turmas")
    r = cliente.post("/turmas/sincronizar", data={"csrf_token": csrf})
    assert "na fila" in r.text and "Busca em andamento" in r.text

    # outro professor não vê a turma
    repo.criar("outro@escola.br", "Outro", "senha-outro")
    outro = _entrar(app, "outro@escola.br", "senha-outro")
    assert outro.get(f"/turmas/{turma.id}").status_code == 404
    assert outro.get(f"/turmas/{turma.id}/estudantes.csv").status_code == 404


def test_app_instalavel(app):
    cliente = TestClient(app)
    manifesto = cliente.get("/static/manifest.webmanifest").json()
    assert manifesto["display"] == "standalone" and manifesto["start_url"] == "/"
    assert {i["sizes"] for i in manifesto["icons"]} >= {"192x192", "512x512"}
    for icone in manifesto["icons"]:
        assert cliente.get(icone["src"]).status_code == 200
    sw = cliente.get("/sw.js")
    assert sw.headers["content-type"].startswith("application/javascript")
    assert cliente.get("/static/offline.html").status_code == 200
    pagina = cliente.get("/login").text
    assert 'rel="manifest"' in pagina and "apple-touch-icon" in pagina and "serviceWorker" in pagina
