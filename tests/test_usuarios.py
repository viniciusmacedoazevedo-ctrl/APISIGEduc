import pytest
from sqlalchemy import select

from apisigeduc.banco import UsuarioSigeduc, criar_sessoes
from apisigeduc.config import ConfigError
from apisigeduc.cripto import Cifra, gerar_chave
from apisigeduc.usuarios import RepositorioUsuarios, UsuarioNaoEncontrado


@pytest.fixture
def sessoes(tmp_path):
    return criar_sessoes(f"sqlite:///{tmp_path / 'teste.db'}")


@pytest.fixture
def repo(sessoes):
    return RepositorioUsuarios(sessoes, Cifra(gerar_chave()))


def test_cadastra_e_recupera_credencial(repo):
    repo.adicionar("prof.maria", "segredo123", nome="Maria", escola="EE Centro")
    cred = repo.credencial("prof.maria")
    assert (cred.login, cred.senha) == ("prof.maria", "segredo123")
    assert "segredo123" not in repr(cred)


def test_senha_fica_cifrada_no_banco(repo, sessoes):
    repo.adicionar("prof.maria", "segredo123")
    with sessoes() as s:
        guardada = s.scalar(select(UsuarioSigeduc.senha_cifrada))
    assert "segredo123" not in guardada


def test_chave_errada_nao_decifra(sessoes):
    RepositorioUsuarios(sessoes, Cifra(gerar_chave())).adicionar("a", "x")
    with pytest.raises(ConfigError):
        RepositorioUsuarios(sessoes, Cifra(gerar_chave())).credencial("a")


def test_login_duplicado(repo):
    repo.adicionar("a", "x")
    with pytest.raises(ValueError):
        repo.adicionar("a", "y")


def test_alterar_desativar_remover(repo):
    repo.adicionar("a", "x")
    repo.alterar_senha("a", "nova")
    assert repo.credencial("a").senha == "nova"
    repo.definir_ativo("a", False)
    with pytest.raises(UsuarioNaoEncontrado):
        repo.credencial("a")
    repo.remover("a")
    assert repo.listar() == []


def test_sem_chave():
    with pytest.raises(ConfigError):
        Cifra("")
