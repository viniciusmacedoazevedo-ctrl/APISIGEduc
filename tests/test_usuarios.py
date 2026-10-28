import pytest
from sqlalchemy import select

from apisigeduc.banco import CredencialSigeduc, Usuario
from apisigeduc.config import ConfigError
from apisigeduc.cripto import Cifra, gerar_chave
from apisigeduc.usuarios import RepositorioUsuarios, UsuarioNaoEncontrado


def test_cria_e_autentica(repo):
    repo.criar("Maria@Escola.br", "Maria", "senha-forte", admin=True)
    usuario = repo.autenticar("maria@escola.br", "senha-forte")
    assert usuario is not None and usuario.admin
    assert repo.autenticar("maria@escola.br", "errada") is None
    assert repo.autenticar("ninguem@escola.br", "senha-forte") is None


def test_senha_de_acesso_fica_com_hash(repo, sessoes):
    repo.criar("a@b.c", "", "senha-forte")
    with sessoes() as s:
        assert "senha-forte" not in s.scalar(select(Usuario.senha_hash))


def test_senha_curta_e_email_duplicado(repo):
    with pytest.raises(ValueError):
        repo.criar("a@b.c", "", "curta")
    repo.criar("a@b.c", "", "senha-forte")
    with pytest.raises(ValueError):
        repo.criar("A@B.C", "", "outra-senha")


def test_usuario_desativado_nao_entra(repo):
    u = repo.criar("a@b.c", "", "senha-forte")
    repo.definir_ativo(u.id, False)
    assert repo.autenticar("a@b.c", "senha-forte") is None


def test_credencial_sigeduc_cifrada(repo, sessoes):
    u = repo.criar("a@b.c", "", "senha-forte")
    with pytest.raises(UsuarioNaoEncontrado):
        repo.credencial(u.id)
    repo.definir_credencial(u.id, "prof.maria", "segredo123", "EE Centro")
    cred = repo.credencial(u.id)
    assert (cred.login, cred.senha) == ("prof.maria", "segredo123")
    assert "segredo123" not in repr(cred)
    with sessoes() as s:
        assert "segredo123" not in s.scalar(select(CredencialSigeduc.senha_cifrada))
    repo.definir_credencial(u.id, "prof.maria", "nova")
    assert repo.credencial(u.id).senha == "nova"


def test_chave_errada_nao_decifra(repo, sessoes):
    u = repo.criar("a@b.c", "", "senha-forte")
    repo.definir_credencial(u.id, "login", "x")
    with pytest.raises(ConfigError):
        RepositorioUsuarios(sessoes, Cifra(gerar_chave())).credencial(u.id)


def test_sem_chave():
    with pytest.raises(ConfigError):
        Cifra("")
