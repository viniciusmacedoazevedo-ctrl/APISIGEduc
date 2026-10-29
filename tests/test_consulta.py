"""Consulta (só leitura) das notas e da frequência lançadas no SIGEduc."""

import os
import re

import pytest
from fastapi.testclient import TestClient

from apisigeduc import composicoes as comp
from apisigeduc import consultas_sigeduc, envios
from apisigeduc import turmas as turmas_repo
from apisigeduc.modelos import Estudante, TurmaPortal
from apisigeduc.tarefas.base import Relatorio
from apisigeduc.tarefas.consulta import _JS_NOTAS, LinhaNotas, NotasTurma, notas_da_tela
from apisigeduc.web.app import criar_app

from tela_notas_falsa import tela_notas

TURMA = TurmaPortal("EFV6A", "ESCOLA ESTADUAL MODELO", "ENSINO FUNDAMENTAL", "6º ANO", "História", 4)
ALUNOS = [
    Estudante("202600000001", "ANA CLARA SOUZA LIMA"),
    Estudante("202600000002", "JOÃO PEDRO DA SILVA"),
    Estudante("202600000003", "BRUNO COSTA"),
    Estudante("202600000004", "BRUNO COSTA"),  # nome repetido: não dá para saber quem é
]
LINHAS_TELA = [
    ("ANA CLARA SOUZA LIMA", ["5.0", "6.3", None, None], 16, "84.9", False),
    ("JOAO PEDRO DA SILVA", ["7.7", "9.0", None, None], 0, "100.0", False),  # sem acento no SIGEduc
    ("BRUNO COSTA", ["4.0", None, None, None], 52, "51.0", False),
    ("MARIA FICTICIA TRANSFERIDA", [None, None, None, None], 0, "100.0", True),
]


def _navegador():
    playwright = pytest.importorskip("playwright.sync_api")
    pw = playwright.sync_playwright().start()
    try:
        exe = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        return pw, pw.chromium.launch(**({"executable_path": exe} if exe else {}))
    except Exception as e:
        pw.stop()
        pytest.skip(f"Chromium indisponível: {e}")


def test_le_a_tela_de_notas_sem_gravar_nada():
    pw, navegador = _navegador()
    gravacoes = []

    def responder(rota):
        if rota.request.method != "GET":
            gravacoes.append(rota.request.url)
        rota.fulfill(body=tela_notas(LINHAS_TELA, atraso_ms=800), content_type="text/html; charset=utf-8")

    try:
        pagina = navegador.new_page()
        pagina.route("http://sigeduc.teste/**", responder)
        pagina.goto("http://sigeduc.teste/sigeduc/diario_classe/lancar_notas/form_estudantes_notas.jsf")
        pagina.locator("table[id$='notaEstudante']").wait_for(state="attached")  # chega devagar
        notas = notas_da_tela(pagina.evaluate(_JS_NOTAS))
    finally:
        navegador.close()
        pw.stop()
    assert gravacoes == []
    assert (notas.aulas_dadas, notas.aulas_previstas) == (106, 148)
    assert [(l.nome, l.ativo, l.notas, l.faltas, l.frequencia) for l in notas.linhas] == [
        ("ANA CLARA SOUZA LIMA", True, [5.0, 6.3, None, None], 16, 84.9),
        ("JOAO PEDRO DA SILVA", True, [7.7, 9.0, None, None], 0, 100.0),
        ("BRUNO COSTA", True, [4.0, None, None, None], 52, 51.0),
        ("MARIA FICTICIA TRANSFERIDA", False, [None, None, None, None], 0, 100.0),
    ]


def test_tela_sem_tabela_e_numeros_com_virgula():
    assert notas_da_tela(None).linhas == []
    dados = {"linhas": [{"nome": "  ANA  ", "riscado": False, "notas": ["8,5", "", "x"], "faltas": "3", "frequencia": "97,5"}],
             "totais": ["40"]}
    [linha] = notas_da_tela(dados).linhas
    assert (linha.nome, linha.notas, linha.faltas, linha.frequencia) == ("ANA", [8.5, None, None, None], 3, 97.5)


def _cenario(repo, sessoes):
    prof = repo.criar("prof@escola.br", "Prof", "senha-prof")
    repo.definir_credencial(prof.id, "prof.sigeduc", "segredo")
    turmas_repo.salvar(sessoes, prof.id, [(TURMA, ALUNOS)])
    [(turma, _)] = turmas_repo.listar(sessoes, prof.id)
    return prof, turma


def _notas_lidas():
    return NotasTurma(
        aulas_dadas=106, aulas_previstas=148,
        linhas=[LinhaNotas(nome, not riscado, [float(n) if n else None for n in notas], faltas, float(freq))
                for nome, notas, faltas, freq, riscado in LINHAS_TELA],
    )


def test_guarda_reconhece_pelo_nome_e_compara(repo, sessoes):
    prof, turma = _cenario(repo, sessoes)
    # no APISIGEduc: 1º bimestre da Ana = 5,0 (igual) e do João = 8,0 (diferente do 7,7 do SIGEduc)
    cid = comp.criar(sessoes, prof.id, "1º bimestre", 2026, 1, "soma",
                     [comp.GrupoNovo("Prova", 10, avaliacoes=[comp.AvaliacaoNova("Prova", 10)])])
    comp.aplicar(sessoes, cid, [turma.id])
    prova = comp.obter(sessoes, cid).avaliacoes[0]
    comp.salvar_notas(sessoes, cid, turma.id, {(prova.id, "202600000001"): "5", (prova.id, "202600000002"): "8"})

    outra = TurmaPortal("EFM6B", "OUTRA ESCOLA", componente="História")  # não importada: ignorada
    assert consultas_sigeduc.salvar(sessoes, prof.id, [(TURMA, _notas_lidas()), (outra, NotasTurma())]) == (1, 4, 1)

    c = consultas_sigeduc.comparar(sessoes, turma)
    assert (c.consulta.aulas_dadas, c.ano) == (106, 2026)
    ana, joao, bruno, maria = c.linhas
    assert (ana.matricula, joao.matricula, bruno.matricula) == ("202600000001", "202600000002", None)
    assert ana.diferentes == [False] * 4 and joao.diferentes == [True, False, False, False]
    assert joao.aqui[0] == 8.0 and c.divergencias == 1 and c.faltando == 0
    sem_nota = consultas_sigeduc.Linha("X", "9", True, sigeduc=[None] * 4, aqui=[7.0, None, None, None])
    assert sem_nota.diferentes == [False] * 4 and sem_nota.faltando == [True, False, False, False]
    assert [l.nome for l in c.nao_reconhecidos] == ["BRUNO COSTA"] and not maria.ativo

    # leitura que falhou mantém a consulta anterior
    assert consultas_sigeduc.salvar(sessoes, prof.id, [(TURMA, None)]) == (0, 0, 0)
    assert len(consultas_sigeduc.comparar(sessoes, turma).linhas) == 4


def test_media_anual_do_rn():
    linha = consultas_sigeduc.Linha("A", "1", True, sigeduc=[5.0, 6.3, 7.0, 8.0])
    assert linha.media_anual == 6.6  # 26,3 / 4 = 6,575 -> 6,6
    assert consultas_sigeduc.Linha("A", "1", True, sigeduc=[5.0, None, 7.0, 8.0]).media_anual is None


class ClienteFalso:
    def __init__(self, config, credencial):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def login(self):
        pass


def test_consulta_pela_fila_mesmo_so_leitura(repo, sessoes, config):
    assert config.sigeduc_somente_leitura  # consultar é ler: continua permitido
    prof, turma = _cenario(repo, sessoes)
    envio_id, criado = envios.pedir_consulta(sessoes, prof.id)
    assert criado and envios.pedir_consulta(sessoes, prof.id) == (envio_id, False)

    def consultar(cliente):
        return [(TURMA, _notas_lidas())], Relatorio(sucesso=1)

    envios.processar_proximo(sessoes, repo, config, ClienteFalso, {"consulta": consultar})
    envio = envios.obter(sessoes, envio_id)
    assert envio.status == "concluido"
    assert envio.mensagem.splitlines() == [
        "1 turma(s) consultada(s), 4 estudante(s) lido(s)",
        "1 estudante(s) do SIGEduc sem nome igual na lista da turma",
    ]


def _entrar(app, email, senha):
    cliente = TestClient(app)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', cliente.get("/login").text).group(1)
    r = cliente.post("/login", data={"email": email, "senha": senha, "csrf_token": csrf})
    assert "Sair" in r.text
    return cliente


def test_telas_da_consulta(repo, sessoes, config):
    prof, turma = _cenario(repo, sessoes)
    cliente = _entrar(criar_app(config, sessoes, repo, iniciar_worker=False), "prof@escola.br", "senha-prof")
    assert "ainda não foi consultada" in cliente.get(f"/turmas/{turma.id}/sigeduc").text

    consultas_sigeduc.salvar(sessoes, prof.id, [(TURMA, _notas_lidas())])
    html = cliente.get(f"/turmas/{turma.id}/sigeduc").text
    assert "106 de 148 aulas dadas" in html and "nome não encontrado na lista da turma" in html
    assert "saiu da turma" in html and "84,9%" in html
    tela = cliente.get(f"/turmas/{turma.id}").text
    assert f'href="/turmas/{turma.id}/sigeduc"' in tela
    assert "84,9%" in tela and "SIGEduc</small>" in tela  # sem chamadas aqui, mostra a frequência do SIGEduc

    csrf = re.search(r'name="csrf_token" value="([^"]+)"', cliente.get("/turmas").text).group(1)
    r = cliente.post("/turmas/consultar", data={"csrf_token": csrf})
    assert "Nada é alterado no SIGEduc" in r.text
    assert envios.listar(sessoes)[0].tipo == "consulta"
