import re

import pytest
from fastapi.testclient import TestClient

from apisigeduc import composicoes as comp
from apisigeduc import desempenho as d
from apisigeduc import turmas as turmas_repo
from apisigeduc.modelos import Estudante, TurmaPortal
from apisigeduc.web.app import criar_app

A, G = comp.AvaliacaoNova, comp.GrupoNovo
ALUNOS = [
    Estudante("202630270867", "IVAN GLEYSON ZACARIAS DE OLIVEIRA"),
    Estudante("202630441436", "WESLEY VARELA PEREIRA DA SILVA"),
    Estudante("202630173200", "ALANNY ESTEFANY CAVALCANTE DA SILVA"),
    Estudante("202630144244", "ANDRIELE SAMIRAH DE OLIVEIRA DANTAS"),
]


def test_resumo_e_faixas():
    r = d.resumir([3, 5.5, 6, 7.9, 8, 10, None], 6)
    assert (r.media, r.mediana, r.menor, r.maior) == (6.7, 7.0, 3, 10)
    assert (r.acima, r.abaixo, r.pendentes, round(r.pct_acima)) == (4, 2, 1, 67)
    assert [(f.rotulo, f.quantidade, f.abaixo_da_minima) for f in r.faixas] == [
        ("0 a 3,9", 1, True), ("4 a 5,9", 1, True), ("6 a 7,9", 2, False), ("8 a 10", 2, False)
    ]
    vazio = d.resumir([None, None], 6)
    assert vazio.media is None and vazio.pct_acima is None and vazio.pendentes == 2


def test_rankings():
    linhas = [comp.LinhaAluno(str(i), n, media=m) for i, (n, m) in enumerate([("ANA", 4.5), ("BIA", 9), ("CAIO", 5.9), ("DUDA", None), ("EVA", 9)])]
    menor, maior = d.rankings(linhas, 6)
    assert [(p.nome, p.falta) for p in menor] == [("ANA", 1.5), ("CAIO", 0.1)]
    assert [p.nome for p in maior] == ["BIA", "EVA", "CAIO", "ANA"]


def test_situacao_anual():
    def aluno(*medias):
        return d.AlunoAnual("1", "X", {b: m for b, m in enumerate(medias, start=1)}, 6)

    assert aluno(7, 8).situacao == "ok" and aluno(7, 8).necessario == 4.5
    a = aluno(5, 6.5)  # um bimestre abaixo
    assert a.situacao == "atencao" and a.bimestres_abaixo == 1 and a.necessario == 6.3
    assert aluno(2, 1, 1).situacao == "recuperacao" and aluno(2, 1, 1).necessario == 20.0  # precisa de mais de 10
    assert aluno(10, 10, 9).necessario == -5.0  # já alcançou
    assert aluno(6, 6, 6, 6).situacao == "ok"
    final_abaixo = aluno(6, 6, 5, 5.5)
    assert final_abaixo.media_parcial == 5.6 and final_abaixo.situacao == "recuperacao"
    assert aluno().situacao == "sem_notas"


@pytest.fixture
def cenario(repo, sessoes):
    prof = repo.criar("prof@escola.br", "Prof", "senha-prof")
    turmas_repo.salvar(
        sessoes,
        prof.id,
        [
            (TurmaPortal("EFV6A", "ESCOLA HENRIQUE", "EF", "6º ANO", "História", 4), ALUNOS),
            (TurmaPortal("EFM6A", "ESCOLA DJALMA", "EF", "6º ANO", "História", 1), [Estudante("202630243350", "ELYSABETH NOEMI ALVES SILVA")]),
        ],
    )
    turmas = {t.codigo: t for t, _ in turmas_repo.listar(sessoes, prof.id)}
    return prof, turmas


def _bimestre(sessoes, prof, turmas, bimestre, notas):
    cid = comp.criar(sessoes, prof.id, f"{bimestre}º bimestre", 2026, bimestre, "soma", [G("Prova", 10, avaliacoes=[A("Prova", 10)])])
    comp.aplicar(sessoes, cid, [t.id for t in turmas.values()])
    prova = comp.obter(sessoes, cid).avaliacoes[0]
    comp.salvar_notas(sessoes, cid, turmas["EFV6A"].id, {(prova.id, m): v for m, v in notas.items()})
    return cid


def _csrf(cliente, caminho):
    return re.search(r'name="csrf_token" value="([^"]+)"', cliente.get(caminho).text).group(1)


def test_paginas_de_desempenho(config, sessoes, repo, cenario):
    prof, turmas = cenario
    c1 = _bimestre(sessoes, prof, turmas, 1, {"202630270867": "3", "202630441436": "9,5", "202630173200": "5,8"})
    _bimestre(sessoes, prof, turmas, 2, {"202630270867": "4", "202630441436": "9", "202630173200": "7"})

    app = criar_app(config, sessoes, repo, iniciar_worker=False)
    cliente = TestClient(app)
    cliente.post("/login", data={"email": "prof@escola.br", "senha": "senha-prof", "csrf_token": _csrf(cliente, "/login")})

    tid = turmas["EFV6A"].id
    r = cliente.get(f"/notas/{c1}/turmas/{tid}/desempenho")
    assert r.status_code == 200
    texto = r.text
    assert "Menor rendimento" in texto and "Maior rendimento" in texto and "faltam 3,0" in texto  # Ivan 3,0
    assert texto.index("IVAN GLEYSON") < texto.index("ALANNY ESTEFANY")  # mais distante primeiro
    assert "ANDRIELE SAMIRAH" in texto and "Sem média (1)" in texto
    assert "LDB" in texto and "art. 24" in texto

    r = cliente.get(f"/notas/{c1}/desempenho")
    assert r.status_code == 200 and "EFM6A" in r.text and "Comparação entre turmas" in r.text

    r = cliente.get(f"/turmas/{tid}/desempenho")
    assert r.status_code == 200 and "2 de 4" in r.text
    linhas = r.text.split("IVAN GLEYSON")[1]
    assert "Atenção" in linhas.split("</tr>")[0] or "Recuperação" in linhas.split("</tr>")[0]
    # Ivan: 3 e 4 → precisa de (24 - 7) / 2 = 8,5 nos dois bimestres restantes
    assert "8,5" in linhas.split("</tr>")[0]

    # outro professor não vê
    repo.criar("outro@escola.br", "Outro", "senha-outro")
    outro = TestClient(app)
    outro.post("/login", data={"email": "outro@escola.br", "senha": "senha-outro", "csrf_token": _csrf(outro, "/login")})
    assert outro.get(f"/notas/{c1}/turmas/{tid}/desempenho").status_code == 404
    assert outro.get(f"/notas/{c1}/desempenho").status_code == 404
    assert outro.get(f"/turmas/{tid}/desempenho").status_code == 404
