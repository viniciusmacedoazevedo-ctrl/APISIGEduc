import re

import pytest
from fastapi.testclient import TestClient

from apisigeduc import composicoes as comp
from apisigeduc import envios
from apisigeduc import turmas as turmas_repo
from apisigeduc.modelos import Estudante, TurmaPortal
from apisigeduc.planilhas import converter, ler_conteudo, nota_de_linha
from apisigeduc.web.app import criar_app

ALUNOS = [Estudante("202630270867", "IVAN GLEYSON ZACARIAS DE OLIVEIRA"), Estudante("202630441436", "WESLEY VARELA PEREIRA DA SILVA")]


@pytest.fixture
def cenario(repo, sessoes):
    prof = repo.criar("prof@escola.br", "Prof", "senha-prof")
    turmas_repo.salvar(
        sessoes,
        prof.id,
        [
            (TurmaPortal("EFV6A", "ESCOLA HENRIQUE", "EF", "6º ANO", "História", 2), ALUNOS),
            (TurmaPortal("EFM6B", "ESCOLA DJALMA", "EF", "6º ANO", "História", 1), [Estudante("202630115378", "MARIA VITORIA")]),
        ],
    )
    turmas = {t.codigo: t for t, _ in turmas_repo.listar(sessoes, prof.id)}
    return prof, turmas


def _composicao(sessoes, prof, calculo="soma"):
    return comp.criar(
        sessoes,
        prof.id,
        "3º bimestre - 6º anos",
        2026,
        3,
        calculo,
        [comp.ItemNovo("Trabalho", 2, 2), comp.ItemNovo("Teste", 3), comp.ItemNovo("Prova", 5)],
    )


def test_validacoes():
    with pytest.raises(ValueError, match="não podem passar de 10"):
        comp.criar(None, 1, "x", 2026, 3, "soma", [comp.ItemNovo("A", 6), comp.ItemNovo("B", 5)])
    with pytest.raises(ValueError, match="nota para todos"):
        comp.ItemNovo("Trabalho", 2, 3).validar()
    with pytest.raises(ValueError, match="Bimestre"):
        comp.criar(None, 1, "x", 2026, 5, "soma", [comp.ItemNovo("A", 10)])
    assert comp.arredondar(7.25) == 7.3 and comp.arredondar(7.24) == 7.2


def test_soma_media_e_nota_para_todos(sessoes, cenario):
    prof, turmas = cenario
    cid = _composicao(sessoes, prof)
    comp.aplicar(sessoes, cid, [turmas["EFV6A"].id, turmas["EFM6B"].id])
    _, itens = comp.obter(sessoes, cid)
    trabalho, teste, prova = itens

    linhas = comp.planilha(sessoes, cid, turmas["EFV6A"].id)
    assert all(l.notas == {trabalho.id: 2} and l.media is None for l in linhas)  # trabalho para todos

    erros = comp.salvar_notas(
        sessoes,
        cid,
        turmas["EFV6A"].id,
        {
            (teste.id, "202630270867"): "2,5",
            (prova.id, "202630270867"): "4,25",
            (teste.id, "202630441436"): "3",
            (prova.id, "202630441436"): "7",  # passa do valor do item
            (trabalho.id, "202630441436"): "1",
        },
    )
    assert erros == ["WESLEY VARELA PEREIRA DA SILVA — Prova: deve ficar entre 0 e 5"]
    ivan, wesley = sorted(comp.planilha(sessoes, cid, turmas["EFV6A"].id), key=lambda l: l.nome)
    assert ivan.media == 8.8  # 2 + 2,5 + 4,25 = 8,75 -> 8,8
    assert wesley.notas[trabalho.id] == 1 and wesley.media is None  # prova inválida não gravou

    # aplicar de novo não sobrescreve notas ajustadas
    comp.aplicar(sessoes, cid, [turmas["EFV6A"].id])
    assert sorted(comp.planilha(sessoes, cid, turmas["EFV6A"].id), key=lambda l: l.nome)[1].notas[trabalho.id] == 1


def test_media_aritmetica(sessoes, cenario):
    prof, turmas = cenario
    cid = comp.criar(sessoes, prof.id, "Médias", 2026, 1, "media", [comp.ItemNovo("P1", 10), comp.ItemNovo("Trabalho", 5)])
    comp.aplicar(sessoes, cid, [turmas["EFM6B"].id])
    p1, trab = comp.obter(sessoes, cid)[1]
    comp.salvar_notas(sessoes, cid, turmas["EFM6B"].id, {(p1.id, "202630115378"): "7", (trab.id, "202630115378"): "4"})
    [linha] = comp.planilha(sessoes, cid, turmas["EFM6B"].id)
    assert linha.media == 7.5  # (7 + 4/5*10) / 2


def test_notas_sobrevivem_a_atualizacao_da_turma(sessoes, cenario):
    prof, turmas = cenario
    cid = _composicao(sessoes, prof)
    comp.aplicar(sessoes, cid, [turmas["EFV6A"].id])
    turmas_repo.salvar(sessoes, prof.id, [(TurmaPortal("EFV6A", "ESCOLA HENRIQUE", "EF", "6º ANO", "História", 2), ALUNOS)])
    assert all(l.notas for l in comp.planilha(sessoes, cid, turmas["EFV6A"].id))


def test_csv_de_medias_e_aceito_pela_fila(sessoes, cenario):
    prof, turmas = cenario
    cid = _composicao(sessoes, prof)
    turma = turmas["EFV6A"]
    comp.aplicar(sessoes, cid, [turma.id])
    _, (trabalho, teste, prova) = comp.obter(sessoes, cid)
    comp.salvar_notas(sessoes, cid, turma.id, {(teste.id, "202630270867"): "3", (prova.id, "202630270867"): "4,5"})
    composicao, _ = comp.obter(sessoes, cid)
    dados = comp.csv_medias(composicao, turma, comp.planilha(sessoes, cid, turma.id))
    leitura = converter(ler_conteudo("medias.csv", dados), nota_de_linha)
    assert leitura.erros == []
    [nota] = leitura.registros
    assert (nota.matricula, nota.turma, nota.disciplina, nota.bimestre, nota.nota) == ("202630270867", "EFV6A", "História", 3, 9.5)


def _csrf(cliente, caminho):
    return re.search(r'name="csrf_token" value="([^"]+)"', cliente.get(caminho).text).group(1)


def test_fluxo_web(config, sessoes, repo, cenario):
    prof, turmas = cenario
    app = criar_app(config, sessoes, repo, iniciar_worker=False)
    cliente = TestClient(app)
    cliente.post("/login", data={"email": "prof@escola.br", "senha": "senha-prof", "csrf_token": _csrf(cliente, "/login")})
    csrf = _csrf(cliente, "/notas")

    r = cliente.post(
        "/notas",
        data={
            "nome": "3º bimestre - 6º anos", "ano_letivo": "2026", "bimestre": "3", "calculo": "soma", "csrf_token": csrf,
            "item_nome": ["Trabalho", "Teste", "Prova", ""], "item_valor": ["2", "3", "5", ""], "item_padrao": ["2", "", "", ""],
        },
    )
    assert "Composição criada" in r.text and "EFV6A" in r.text
    cid = int(re.search(r"/notas/(\d+)/turmas", r.text).group(1))

    r = cliente.post(f"/notas/{cid}/turmas", data={"turma_id": [turmas["EFV6A"].id, turmas["EFM6B"].id], "csrf_token": csrf})
    assert "aplicada em 2 turma(s)" in r.text

    tid = turmas["EFV6A"].id
    r = cliente.get(f"/notas/{cid}/turmas/{tid}")
    assert "IVAN GLEYSON" in r.text and 'value="2"' in r.text  # trabalho para todos
    _, (trabalho, teste, prova) = comp.obter(sessoes, cid)
    r = cliente.post(
        f"/notas/{cid}/turmas/{tid}",
        data={"csrf_token": csrf, f"n_{teste.id}_202630270867": "3", f"n_{prova.id}_202630270867": "4,5"},
    )
    assert "Notas salvas" in r.text and "9,5" in r.text

    # sem senha do SIGEduc
    r = cliente.post(f"/notas/{cid}/turmas/{tid}/enviar", data={"csrf_token": csrf})
    assert "Cadastre sua senha do SIGEduc" in r.text
    repo.definir_credencial(prof.id, "27477610885", "x")
    r = cliente.post(f"/notas/{cid}/turmas/{tid}/enviar", data={"csrf_token": csrf})
    assert "1 estudante(s) ainda sem todas as notas" in r.text
    r = cliente.post(f"/notas/{cid}/turmas/{tid}/enviar", data={"csrf_token": csrf, "so_completos": "true"})
    assert "1 média(s) da turma EFV6A na fila" in r.text
    [envio] = envios.listar(sessoes)
    assert envio.tipo == "notas" and envio.total == 1

    # outro professor não acessa
    repo.criar("outro@escola.br", "Outro", "senha-outro")
    outro = TestClient(app)
    outro.post("/login", data={"email": "outro@escola.br", "senha": "senha-outro", "csrf_token": _csrf(outro, "/login")})
    assert outro.get(f"/notas/{cid}").status_code == 404
    assert outro.get(f"/notas/{cid}/turmas/{tid}").status_code == 404
