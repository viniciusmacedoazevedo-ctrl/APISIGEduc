import io
import re
import zipfile
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apisigeduc import composicoes as comp
from apisigeduc import diario, envios, sincronia
from apisigeduc import turmas as turmas_repo
from apisigeduc.modelos import Estudante, TurmaPortal
from apisigeduc.web.app import criar_app

A, G = comp.AvaliacaoNova, comp.GrupoNovo
ALUNOS = [Estudante("202630270867", "IVAN GLEYSON ZACARIAS DE OLIVEIRA"), Estudante("202630441436", "WESLEY VARELA PEREIRA DA SILVA")]
ONTEM = date.today() - timedelta(days=1)


@pytest.fixture
def cenario(repo, sessoes):
    prof = repo.criar("prof@escola.br", "Daiane", "senha-prof")
    repo.definir_credencial(prof.id, "27477610885", "segredo")
    turmas_repo.salvar(sessoes, prof.id, [(TurmaPortal("EFV6A", "ESCOLA HENRIQUE", "EF", "6º ANO", "História", 2), ALUNOS)])
    [(turma, _)] = turmas_repo.listar(sessoes, prof.id)
    cid = comp.criar(sessoes, prof.id, "3º bimestre", 2026, 3, "soma", [G("Prova", 10, avaliacoes=[A("Prova", 10)])])
    comp.aplicar(sessoes, cid, [turma.id])
    prova = comp.obter(sessoes, cid).avaliacoes[0]
    comp.salvar_notas(sessoes, cid, turma.id, {(prova.id, ALUNOS[0].matricula): "7,5", (prova.id, ALUNOS[1].matricula): "5"})
    return prof, turma, cid, prova


def test_so_vai_o_liberado_e_nada_se_repete(sessoes, cenario):
    prof, turma, cid, prova = cenario
    diario.salvar_chamada(sessoes, turma.id, ONTEM, 2, "Grécia", "", {ALUNOS[1].matricula})
    itens, _ = sincronia.pendentes(sessoes)
    assert {i.tipo for i in itens} == {"frequencia"}  # médias ainda não liberadas

    comp.liberar(sessoes, cid, turma.id)
    itens, _ = sincronia.pendentes(sessoes)
    assert sorted((i.tipo, i.matricula, i.valor) for i in itens) == [
        ("frequencia", "202630270867", "P"), ("frequencia", "202630441436", "F"),
        ("media", "202630270867", "7.5"), ("media", "202630441436", "5.0"),
    ]

    entrega = sincronia.executar(sessoes, "arquivo", prof.id)
    assert (entrega.qtd_medias, entrega.qtd_frequencias) == (2, 2)
    arquivo = zipfile.ZipFile(io.BytesIO(entrega.arquivo))
    medias = arquivo.read("medias.csv").decode("utf-8-sig").splitlines()
    assert medias[0] == "escola;turma;componente;ano_letivo;bimestre;matricula;estudante;media;professor_login_sigeduc"
    assert "ESCOLA HENRIQUE;EFV6A;História;2026;3;202630270867;IVAN GLEYSON ZACARIAS DE OLIVEIRA;7,5;27477610885" in medias
    freq = arquivo.read("frequencia.csv").decode("utf-8-sig")
    assert f"{ONTEM.isoformat()};2;202630441436;WESLEY VARELA PEREIRA DA SILVA;F" in freq

    assert sincronia.pendentes(sessoes)[0] == []  # nada se repete
    assert sincronia.executar(sessoes, "arquivo", prof.id) is None

    # nota corrigida volta a ser pendente, só ela
    comp.salvar_notas(sessoes, cid, turma.id, {(prova.id, ALUNOS[1].matricula): "6"})
    [item] = sincronia.pendentes(sessoes)[0]
    assert (item.matricula, item.valor) == ("202630441436", "6.0")


def test_robo_enfileira_medias_e_so_conta_quando_concluir(sessoes, cenario):
    prof, turma, cid, _ = cenario
    comp.liberar(sessoes, cid, turma.id)
    entrega = sincronia.executar(sessoes, "sigeduc", prof.id)
    [envio] = envios.listar(sessoes)
    assert envio.tipo == "notas" and envio.total == 2 and entrega.qtd_medias == 2
    assert len(sincronia.pendentes(sessoes)[0]) == 2  # ainda na fila: continua pendente
    with sessoes.begin() as s:
        s.get(type(envio), envio.id).status = "concluido"
    assert sincronia.pendentes(sessoes)[0] == []


def test_janela_agendada_uma_vez_por_dia(sessoes, cenario):
    _, turma, cid, _ = cenario
    comp.liberar(sessoes, cid, turma.id)
    hoje = datetime.now().replace(hour=18, minute=59, second=0, microsecond=0)
    assert sincronia.executar_agendada(sessoes, "19:00", "arquivo", hoje) is None  # antes da hora
    e = sincronia.executar_agendada(sessoes, "19:00", "arquivo", hoje.replace(hour=19, minute=5))
    assert e is not None and e.criado_por_id is None
    comp.salvar_notas(sessoes, cid, turma.id, {(comp.obter(sessoes, cid).avaliacoes[0].id, ALUNOS[0].matricula): "9"})
    assert sincronia.executar_agendada(sessoes, "19:00", "arquivo", hoje.replace(hour=21)) is None  # já rodou hoje
    assert sincronia.executar_agendada(sessoes, "19:00", "arquivo", hoje.replace(hour=19) + timedelta(days=1)) is not None
    assert sincronia.hora_da_janela("25:00") is None and sincronia.executar_agendada(sessoes, "", "arquivo") is None


def _entrar(app, email, senha):
    c = TestClient(app)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text).group(1)
    c.post("/login", data={"email": email, "senha": senha, "csrf_token": csrf})
    return c, csrf


def test_tela_do_administrador(config, sessoes, repo, cenario):
    prof, turma, cid, _ = cenario
    repo.criar("admin@escola.br", "Admin", "senha-admin", admin=True)
    comp.liberar(sessoes, cid, turma.id)
    app = criar_app(config, sessoes, repo, iniciar_worker=False)
    professor, _ = _entrar(app, "prof@escola.br", "senha-prof")
    assert professor.get("/admin/sincronizacao").status_code == 403

    admin, _ = _entrar(app, "admin@escola.br", "senha-admin")
    r = admin.get("/admin/sincronizacao")
    assert r.status_code == 200 and "Médias pendentes" in r.text and "Daiane" in r.text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
    r = admin.post("/admin/sincronizacao/carga", data={"modo": "arquivo", "csrf_token": csrf})
    assert "Carga nº 1 gerada: 2 média(s)" in r.text
    z = admin.get("/admin/sincronizacao/1/arquivo.zip")
    assert z.headers["content-type"] == "application/zip" and "medias.csv" in zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    r = admin.post("/admin/sincronizacao/carga", data={"modo": "arquivo", "csrf_token": csrf})
    assert "Nada pendente" in r.text
    assert professor.get("/admin/sincronizacao/1/arquivo.zip").status_code == 403
