import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from apisigeduc import diario
from apisigeduc import turmas as turmas_repo
from apisigeduc.modelos import Estudante, TurmaPortal
from apisigeduc.web.app import criar_app

ALUNOS = [
    Estudante("202630270867", "IVAN GLEYSON ZACARIAS DE OLIVEIRA"),
    Estudante("202630441436", "WESLEY VARELA PEREIRA DA SILVA"),
]
ONTEM = date.today() - timedelta(days=1)


@pytest.fixture
def cenario(repo, sessoes):
    prof = repo.criar("prof@escola.br", "Prof", "senha-prof")
    outro = repo.criar("outro@escola.br", "Outro", "senha-outro")
    admin = repo.criar("admin@escola.br", "Admin", "senha-admin", admin=True)
    turmas_repo.salvar(sessoes, prof.id, [(TurmaPortal("EFV6A", componente="História"), ALUNOS)])
    # o mesmo Ivan também é aluno do outro professor, em Matemática
    turmas_repo.salvar(sessoes, outro.id, [(TurmaPortal("EFV6A", componente="Matemática"), ALUNOS[:1])])
    [(turma, _)] = turmas_repo.listar(sessoes, prof.id)
    return prof, outro, admin, turma


def test_chamada_e_frequencia_com_aulas_geminadas(sessoes, cenario):
    _, _, _, turma = cenario
    ivan, wesley = (a.matricula for a in ALUNOS)
    diario.salvar_chamada(sessoes, turma.id, ONTEM - timedelta(days=1), 2, "Revolução Industrial", "", {ivan})
    diario.salvar_chamada(sessoes, turma.id, ONTEM, 1, "Exercícios", "turma agitada", set())
    freq = diario.frequencia_da_turma(sessoes, turma.id)
    assert (freq[ivan].aulas_dadas, freq[ivan].faltas, freq[ivan].percentual) == (3, 2, 33.3)
    assert freq[ivan].abaixo_do_minimo and not freq[wesley].abaixo_do_minimo
    assert freq[wesley].percentual == 100.0

    # refazer a chamada do mesmo dia substitui (não duplica)
    diario.salvar_chamada(sessoes, turma.id, ONTEM, 1, "Exercícios", "", {wesley})
    assert len(diario.aulas(sessoes, turma.id)) == 2
    assert diario.frequencia_da_turma(sessoes, turma.id)[wesley].faltas == 1

    with pytest.raises(ValueError, match="futura"):
        diario.salvar_chamada(sessoes, turma.id, date.today() + timedelta(days=1), 1, "", "", set())
    with pytest.raises(ValueError, match="Quantidade"):
        diario.salvar_chamada(sessoes, turma.id, ONTEM, 9, "", "", set())


def test_anotacoes_sao_privadas_de_quem_escreveu(sessoes, cenario):
    prof, outro, admin, _ = cenario
    ivan = ALUNOS[0].matricula
    diario.anotar(sessoes, prof.id, ivan, "leitura", "Ainda não lê textos curtos.")
    assert [a.texto for a in diario.anotacoes(sessoes, prof.id, ivan)] == ["Ainda não lê textos curtos."]
    assert diario.anotacoes(sessoes, outro.id, ivan) == []
    assert diario.anotacoes(sessoes, admin.id, ivan) == []
    assert diario.contagem_anotacoes(sessoes, prof.id, [ivan]) == {}  # comum: só na ficha
    nota_id = diario.anotar(sessoes, prof.id, ivan, "saude", "Usa óculos; sentar na frente.", importante=True)
    assert diario.contagem_anotacoes(sessoes, prof.id, [ivan]) == {ivan: ["saude"]}  # importante: sinaliza
    assert diario.contagem_anotacoes(sessoes, outro.id, [ivan]) == {}
    assert diario.alternar_importante(sessoes, prof.id, nota_id) is False
    assert diario.contagem_anotacoes(sessoes, prof.id, [ivan]) == {}
    with pytest.raises(LookupError):
        diario.excluir_anotacao(sessoes, outro.id, diario.anotacoes(sessoes, prof.id, ivan)[0].id)
    with pytest.raises(LookupError):
        diario.alternar_importante(sessoes, outro.id, nota_id)
    with pytest.raises(ValueError):
        diario.anotar(sessoes, prof.id, ivan, "inventada", "x")


def _entrar(app, email, senha):
    cliente = TestClient(app)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', cliente.get("/login").text).group(1)
    cliente.post("/login", data={"email": email, "senha": senha, "csrf_token": csrf})
    return cliente


def _csrf(cliente, caminho):
    return re.search(r'name="csrf_token" value="([^"]+)"', cliente.get(caminho).text).group(1)


def test_telas_do_diario(config, sessoes, repo, cenario):
    prof, outro, admin, turma = cenario
    app = criar_app(config, sessoes, repo, iniciar_worker=False)
    ivan = ALUNOS[0].matricula

    cliente = _entrar(app, "prof@escola.br", "senha-prof")
    r = cliente.get(f"/turmas/{turma.id}/chamada?data={ONTEM.isoformat()}")
    assert r.status_code == 200 and "Toque em quem faltou" in r.text and "IVAN GLEYSON" in r.text
    csrf = _csrf(cliente, f"/turmas/{turma.id}/chamada")
    r = cliente.post(
        f"/turmas/{turma.id}/chamada",
        data={"data": ONTEM.isoformat(), "quantidade": "2", "conteudo": "Grécia Antiga", "falta": [ivan], "csrf_token": csrf},
    )
    assert "salva: 1 falta(s)" in r.text and "Grécia Antiga" in r.text
    assert "Frequência abaixo de 75%" in r.text  # Ivan com 0%

    # anotação comum pela ficha: não sinaliza; importante: sinaliza na turma e na chamada
    r = cliente.post(f"/alunos/{ivan}/anotacoes", data={"categoria": "leitura", "texto": "Não lê textos curtos.", "csrf_token": csrf})
    assert "Só você pode ver" in r.text and "Não lê textos curtos." in r.text
    assert "sinal-importante" not in cliente.get(f"/turmas/{turma.id}").text
    r = cliente.post(
        f"/alunos/{ivan}/anotacoes",
        data={"categoria": "saude", "texto": "Usa óculos.", "importante": "true", "csrf_token": csrf},
    )
    assert "aparecerá sinalizado" in r.text
    assert "sinal-importante" in cliente.get(f"/turmas/{turma.id}").text
    assert "sinal-importante" in cliente.get(f"/turmas/{turma.id}/chamada").text

    # outro professor do mesmo aluno abre a ficha, mas não vê a anotação
    r = _entrar(app, "outro@escola.br", "senha-outro").get(f"/alunos/{ivan}")
    assert r.status_code == 200 and "Não lê textos curtos." not in r.text
    # o administrador também não lê
    r = _entrar(app, "admin@escola.br", "senha-admin").get(f"/alunos/{ivan}")
    assert r.status_code == 200 and "Não lê textos curtos." not in r.text

    # quem não tem o aluno nem a turma: 404
    repo.criar("sem@escola.br", "Sem", "senha-semx")
    estranho = _entrar(app, "sem@escola.br", "senha-semx")
    assert estranho.get(f"/alunos/{ivan}").status_code == 404
    assert estranho.get(f"/turmas/{turma.id}/chamada").status_code == 404
    assert estranho.get(f"/turmas/{turma.id}/aulas").status_code == 404


def test_migracao_anotacoes_sem_importante(tmp_path):
    from sqlalchemy import create_engine, text

    from apisigeduc.banco import criar_sessoes

    url = f"sqlite:///{tmp_path / 'antigo.db'}"
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE anotacoes (id INTEGER PRIMARY KEY, autor_id INTEGER, matricula VARCHAR(50), categoria VARCHAR(40), texto TEXT, criado_em DATETIME, atualizado_em DATETIME)"))
        c.execute(text("INSERT INTO anotacoes (id, autor_id, matricula, categoria, texto) VALUES (1, 7, '123', 'leitura', 'antiga')"))
    engine.dispose()
    sessoes = criar_sessoes(url)
    [nota] = diario.anotacoes(sessoes, 7, "123")
    assert nota.texto == "antiga" and nota.importante is False


def test_bimestre_das_aulas():
    inicios = diario.inicios_dos_bimestres("02-01,05-04,08-01,10-13")
    assert [diario.bimestre_da_data(date(2026, m, d), inicios) for m, d in
            [(1, 20), (2, 23), (5, 3), (5, 4), (8, 3), (10, 9), (10, 13), (12, 18)]] == [1, 1, 1, 2, 3, 3, 4, 4]
    assert diario.data_por_extenso(date(2026, 3, 5)) == "05 de março de 2026"
    for ruim in ("02-01,05-04,08-01", "05-04,02-01,08-01,10-13", "02-30,05-04,08-01,10-13"):
        with pytest.raises(ValueError):
            diario.inicios_dos_bimestres(ruim)


def test_tela_da_turma_com_abas(config, sessoes, repo, cenario):
    from apisigeduc import composicoes as comp

    prof, _, _, turma = cenario
    ivan = ALUNOS[0].matricula
    diario.salvar_chamada(sessoes, turma.id, date(2026, 3, 2), 2, "Pré-história: fontes históricas", "", {ivan})
    diario.salvar_chamada(sessoes, turma.id, date(2026, 8, 3), 1, "", "", set())
    cid = comp.criar(sessoes, prof.id, "3º bimestre", 2026, 3, "soma",
                     [comp.GrupoNovo("Prova", 10, avaliacoes=[comp.AvaliacaoNova("Prova", 10)])])
    comp.aplicar(sessoes, cid, [turma.id])
    prova = comp.obter(sessoes, cid).avaliacoes[0]
    comp.salvar_notas(sessoes, cid, turma.id, {(prova.id, ivan): "7"})

    cliente = _entrar(criar_app(config, sessoes, repo, iniciar_worker=False), "prof@escola.br", "senha-prof")
    html = cliente.get(f"/turmas/{turma.id}").text
    for aba in ('aria-controls="alunos"', 'aria-controls="aulas"', 'aria-controls="notas"'):
        assert aba in html
    assert 'id="letra-I"' in html and 'id="letra-W"' in html and 'href="#letra-I"' in html
    assert "02 de março de 2026" in html and "Pré-história: fontes históricas" in html
    assert "Sem conteúdo registrado" in html  # aula de agosto sem conteúdo
    assert 'data-aulas-bimestre="1"' in html and 'data-aulas-bimestre="3"' in html
    assert "1 de 2 com média" in html and f"/notas/{cid}/turmas/{turma.id}?modo=aluno" in html
    # o outro professor não abre a turma
    outro = _entrar(criar_app(config, sessoes, repo, iniciar_worker=False), "outro@escola.br", "senha-outro")
    assert outro.get(f"/turmas/{turma.id}").status_code == 404
