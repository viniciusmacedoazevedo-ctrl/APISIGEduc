import os
from pathlib import Path

import pytest

from apisigeduc import envios
from apisigeduc import turmas as turmas_repo
from apisigeduc.modelos import Estudante, TurmaPortal, descrever_turma
from apisigeduc.tarefas.base import Relatorio
from apisigeduc.tarefas.turmas import (
    _JS_CARTOES,
    _JS_TABELAS,
    estudantes_dos_cartoes,
    turmas_da_tabela,
)

# Trechos no formato das telas reais do SIGEduc (Portal do Professor e Escola Digital)
PORTAL = """
<table><tr><td>
 <table>
  <tr><th colspan="12">Escola Digital  Web Conferência</th></tr>
  <tr><th colspan="2">Turma</th><th>Ano</th><th>Escola</th><th>Etapa de Ensino</th><th>Ano / Série</th>
      <th>Componente</th><th>Qtd. de Estudantes</th><th colspan="4"></th></tr>
  <tr><td><a href="#"><img></a></td><td><a href="#">EFV6A</a></td><td>2026.1</td>
      <td>ESCOLA ESTADUAL EXEMPLO UM</td><td>ENSINO FUNDAMENTAL</td><td>6º ANO</td>
      <td>História</td><td>36</td><td></td><td></td><td></td><td></td></tr>
  <tr><td><a href="#"><img></a></td><td><a href="#">EFM6A</a></td><td>2026.1</td>
      <td>ESCOLA ESTADUAL EXEMPLO DOIS</td><td>ENSINO FUNDAMENTAL</td><td>6º ANO</td>
      <td>História</td><td>38</td><td></td><td></td><td></td><td></td></tr>
 </table>
</td><td>
 <table><tr><th colspan="2">Dados Pessoais</th></tr><tr><td>Matrícula:</td><td>1000000</td></tr></table>
</td></tr></table>
"""
CARTAO = """<td><div><span>● {nome}</span> <img></div><table><tr><td><img></td><td>
  <p>Matrícula: {mat}</p><p>Usuário: Sem cadastro no sistema</p><p>E-mail: Desconhecido</p></td></tr></table></td>"""
ESTUDANTES = "<h2>Estudantes (3)</h2><table><tr>" + "".join(
    CARTAO.format(nome=n, mat=m)
    for n, m in [
        ("ANA CLARA SOUZA LIMA", "202600000001"),
        ("BRUNO HENRIQUE COSTA DA SILVA", "202600000002"),
        ("CARLA REGINA MOURA DIAS", "202600000003"),
    ]
) + "</tr></table>"


def test_descrever_turma():
    assert descrever_turma("EFV6A") == "Ensino Fundamental · Vespertino · 6º ano A"
    assert descrever_turma("EFM6B") == "Ensino Fundamental · Matutino · 6º ano B"
    assert descrever_turma("qualquer") == ""


def test_turmas_com_colspan_e_icone():
    # como o JS devolve: células com colspan repetidas
    tabela = [
        ["Escola Digital Web Conferência"] * 12,
        ["Turma", "Turma", "Ano", "Escola", "Etapa de Ensino", "Ano / Série", "Componente", "Qtd. de Estudantes", "", "", "", ""],
        ["", "EFV6A", "2026.1", "ESCOLA X", "ENSINO FUNDAMENTAL", "6º ANO", "História", "36", "", "", "", ""],
    ]
    [turma] = turmas_da_tabela([[["Nome:", "x"]], tabela])
    assert turma == TurmaPortal("EFV6A", "ESCOLA X", "ENSINO FUNDAMENTAL", "6º ANO", "História", 36)


def test_cartoes_sem_duplicar_e_sem_nome_vazio():
    cartoes = [
        {"matricula": "1", "nome": "ANA  MARIA"},
        {"matricula": "1", "nome": "ANA MARIA"},
        {"matricula": "2", "nome": ""},
    ]
    assert estudantes_dos_cartoes(cartoes) == [Estudante("1", "ANA MARIA")]


def _navegador():
    playwright = pytest.importorskip("playwright.sync_api")
    pw = playwright.sync_playwright().start()
    try:
        exe = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        return pw, pw.chromium.launch(**({"executable_path": exe} if exe else {}))
    except Exception as e:
        pw.stop()
        pytest.skip(f"Chromium indisponível: {e}")


def test_leitura_no_navegador():
    pw, navegador = _navegador()
    try:
        pagina = navegador.new_page()
        pagina.set_content('<meta charset="utf-8">' + PORTAL)
        turmas = turmas_da_tabela(pagina.evaluate(_JS_TABELAS))
        assert [(t.codigo, t.qtd_estudantes, t.ano_serie) for t in turmas] == [("EFV6A", 36, "6º ANO"), ("EFM6A", 38, "6º ANO")]

        pagina.set_content('<meta charset="utf-8">' + ESTUDANTES)
        estudantes = estudantes_dos_cartoes(pagina.evaluate(_JS_CARTOES))
        assert estudantes == [
            Estudante("202600000001", "ANA CLARA SOUZA LIMA"),
            Estudante("202600000002", "BRUNO HENRIQUE COSTA DA SILVA"),
            Estudante("202600000003", "CARLA REGINA MOURA DIAS"),
        ]
    finally:
        navegador.close()
        pw.stop()


def _prof(repo):
    u = repo.criar("prof@escola.br", "Prof", "senha-forte")
    repo.definir_credencial(u.id, "prof.sigeduc", "segredo")
    return u


def test_salvar_substitui_estudantes_e_mantem_quando_falha(repo, sessoes):
    u = _prof(repo)
    t = TurmaPortal("EFV6A", "ESCOLA X", "EF", "6º ANO", "História", 2)
    turmas_repo.salvar(sessoes, u.id, [(t, [Estudante("1", "ANA"), Estudante("2", "BIA")])])
    turmas_repo.salvar(sessoes, u.id, [(t, [Estudante("1", "ANA"), Estudante("3", "CAIO")])])
    [(turma, n)] = turmas_repo.listar(sessoes, u.id)
    assert n == 2
    assert [e.nome for e in turmas_repo.estudantes(sessoes, turma.id)] == ["ANA", "CAIO"]

    turmas_repo.salvar(sessoes, u.id, [(t, None)])  # leitura falhou: mantém a lista
    assert [e.nome for e in turmas_repo.estudantes(sessoes, turma.id)] == ["ANA", "CAIO"]


class ClienteFalso:
    def __init__(self, config, credencial):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def login(self):
        pass


def test_sincronizacao_pela_fila(repo, sessoes, config):
    u = _prof(repo)
    envio_id, criado = envios.pedir_sincronizacao(sessoes, u.id)
    assert criado
    assert envios.pedir_sincronizacao(sessoes, u.id) == (envio_id, False)  # não duplica

    def sincronizar(cliente):
        rel = Relatorio(sucesso=1, falhas=["EFM6A/História: falhou"])
        return [
            (TurmaPortal("EFV6A", componente="História"), [Estudante("1", "ANA")]),
            (TurmaPortal("EFM6A", componente="História"), None),
        ], rel

    envios.processar_proximo(sessoes, repo, config, ClienteFalso, {"turmas": sincronizar})
    envio = envios.obter(sessoes, envio_id)
    assert envio.status == "concluido_com_falhas"
    assert (envio.total, envio.sucesso) == (2, 1)
    assert envio.mensagem == "2 turma(s) encontrada(s), 1 estudante(s) importado(s)"
    assert len(turmas_repo.listar(sessoes, u.id)) == 2
