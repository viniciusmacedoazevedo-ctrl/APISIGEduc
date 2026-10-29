"""O app sem internet: telas de lançamento abrem do aparelho, o que é salvo fica na fila
do aparelho e vai ao servidor quando a conexão volta. Roda com Chromium disponível."""

import os
import re
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from apisigeduc import composicoes as comp
from apisigeduc import diario
from apisigeduc import turmas as turmas_repo
from apisigeduc.modelos import Estudante, TurmaPortal
from apisigeduc.web.app import criar_app


def test_sair_apaga_o_que_ficou_no_aparelho(config, sessoes, repo):
    repo.criar("p@e.br", "P", "senha-forte")
    cliente = TestClient(criar_app(config, sessoes, repo, iniciar_worker=False))
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', cliente.get("/login").text).group(1)
    r = cliente.post("/login", data={"email": "p@e.br", "senha": "senha-forte", "csrf_token": csrf})
    assert 'src="/static/offline.js"' in r.text and 'data-usuario="' in r.text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
    r = cliente.post("/sair", data={"csrf_token": csrf}, follow_redirects=False)
    assert r.headers["clear-site-data"] == '"cache", "storage"'


def _porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("banco_url", ["sqlite"], indirect=True)
def test_lancar_sem_internet_e_enviar_quando_voltar(config, sessoes, repo):
    playwright = pytest.importorskip("playwright.sync_api")
    uvicorn = pytest.importorskip("uvicorn")
    u = repo.criar("p@e.br", "Daiane", "senha-forte")
    alunos = [Estudante(f"2026100000{i}", n) for i, n in enumerate(["ANA CLARA SOUZA", "BRUNO HENRIQUE COSTA", "CARLA REGINA MOURA"])]
    turmas_repo.salvar(sessoes, u.id, [(TurmaPortal("EFV6A", componente="História"), alunos)])
    [(turma, _)] = turmas_repo.listar(sessoes, u.id)
    cid = comp.criar(sessoes, u.id, "4º bimestre", 2026, 4, "soma", [comp.GrupoNovo("Prova", 10, avaliacoes=[comp.AvaliacaoNova("Prova", 10)])])
    comp.aplicar(sessoes, cid, [turma.id])

    porta = _porta_livre()
    servidor = uvicorn.Server(uvicorn.Config(criar_app(config, sessoes, repo, iniciar_worker=False), port=porta, log_level="warning"))
    threading.Thread(target=servidor.run, daemon=True).start()
    while not servidor.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{porta}"
    pw = playwright.sync_playwright().start()
    try:
        exe = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        try:
            navegador = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
        except Exception as e:
            pytest.skip(f"Chromium indisponível: {e}")
        contexto = navegador.new_context(**pw.devices["iPhone 13"])
        pg = contexto.new_page()
        pg.goto(base + "/login"); pg.fill("#email", "p@e.br"); pg.fill("#senha", "senha-forte"); pg.click("button:has-text('Entrar')")
        for _ in range(2):  # com internet: o service worker guarda as telas
            pg.goto(f"{base}/turmas/{turma.id}/chamada"); pg.wait_for_timeout(600)
            pg.goto(f"{base}/notas/{cid}/turmas/{turma.id}"); pg.wait_for_timeout(600)

        contexto.set_offline(True)
        pg.goto(f"{base}/turmas/{turma.id}/chamada")
        assert pg.locator("h1").inner_text() == "Chamada"
        pg.click("label.aluno-chamada >> nth=1")
        pg.click("button:has-text('Salvar chamada') >> nth=0")
        pg.locator(".faixa-offline", has_text="ficou guardado no aparelho").wait_for(timeout=15000)
        pg.goto(f"{base}/notas/{cid}/turmas/{turma.id}")
        for i, v in enumerate(["8", "6,5", "9"]):
            pg.locator("input[data-item]").nth(i).fill(v)
        pg.click("button:has-text('Salvar notas')")
        pg.locator(".faixa-offline", has_text="ficou guardado no aparelho").wait_for(timeout=15000)
        assert diario.aulas(sessoes, turma.id) == []

        contexto.set_offline(False)
        pg.locator(".faixa-offline", has_text="foram enviados").wait_for(timeout=20000)
        [(aula, faltas)] = diario.aulas(sessoes, turma.id)
        assert faltas == 1
        assert [l.media for l in sorted(comp.planilha(sessoes, cid, turma.id), key=lambda l: l.matricula)] == [8.0, 6.5, 9.0]
    finally:
        pw.stop()
        servidor.should_exit = True
