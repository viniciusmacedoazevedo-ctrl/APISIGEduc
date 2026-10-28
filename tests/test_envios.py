from contextlib import contextmanager
from pathlib import Path

from apisigeduc import envios
from apisigeduc.tarefas.base import Relatorio

NOTAS = (Path(__file__).parent.parent / "exemplos" / "notas.csv").read_bytes()


class ClienteFalso:
    logins = []

    def __init__(self, config, credencial):
        self.credencial = credencial

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def login(self):
        ClienteFalso.logins.append(self.credencial.login)


def _usuario(repo):
    u = repo.criar("prof@escola.br", "Prof", "senha-forte")
    repo.definir_credencial(u.id, "prof.sigeduc", "segredo")
    return u


def test_fila_processa_com_a_credencial_do_dono(repo, sessoes, config):
    u = _usuario(repo)
    recebidos = []

    def lancar(cliente, registros):
        recebidos.extend(registros)
        return Relatorio(sucesso=len(registros))

    envio_id = envios.criar(sessoes, u.id, "notas", "notas.csv", NOTAS, 3)
    assert envios.processar_proximo(sessoes, repo, config, ClienteFalso, {"notas": lancar})
    assert not envios.processar_proximo(sessoes, repo, config, ClienteFalso, {"notas": lancar})

    envio = envios.obter(sessoes, envio_id)
    assert envio.status == "concluido"
    assert envio.sucesso == 3
    assert len(recebidos) == 3
    assert ClienteFalso.logins[-1] == "prof.sigeduc"


def test_falhas_e_erros_ficam_registrados(repo, sessoes, config):
    u = _usuario(repo)

    def lancar(cliente, registros):
        return Relatorio(sucesso=2, falhas=["20260003: aluno não encontrado"])

    envio_id = envios.criar(sessoes, u.id, "notas", "notas.csv", NOTAS, 3)
    envios.processar_proximo(sessoes, repo, config, ClienteFalso, {"notas": lancar})
    envio = envios.obter(sessoes, envio_id)
    assert envio.status == "concluido_com_falhas"
    assert "aluno não encontrado" in envio.falhas

    def quebra(cliente, registros):
        raise RuntimeError("SIGEduc fora do ar")

    envio_id = envios.criar(sessoes, u.id, "notas", "notas.csv", NOTAS, 3)
    envios.processar_proximo(sessoes, repo, config, ClienteFalso, {"notas": quebra})
    envio = envios.obter(sessoes, envio_id)
    assert envio.status == "falhou"
    assert envio.mensagem == "SIGEduc fora do ar"
