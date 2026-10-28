import os

import pytest
from sqlalchemy import create_engine

from apisigeduc.banco import Base, criar_sessoes, normalizar_url
from apisigeduc.config import Config
from apisigeduc.cripto import Cifra, gerar_chave
from apisigeduc.usuarios import RepositorioUsuarios

# Para testar também no PostgreSQL, defina por exemplo:
#   APISIGEDUC_TESTE_BANCO_URL=postgresql://apisigeduc:senha@localhost/apisigeduc_teste
BANCOS = ["sqlite"] + (["postgresql"] if os.getenv("APISIGEDUC_TESTE_BANCO_URL") else [])


@pytest.fixture(params=BANCOS)
def banco_url(request, tmp_path):
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'teste.db'}"
        return
    url = os.environ["APISIGEDUC_TESTE_BANCO_URL"]
    engine = create_engine(normalizar_url(url))
    Base.metadata.drop_all(engine)
    yield url
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def config(banco_url):
    return Config(url="http://sigeduc.teste", banco_url=banco_url, chave=gerar_chave(), segredo_sessao="x" * 40)


@pytest.fixture
def sessoes(config):
    return criar_sessoes(config.banco_url)


@pytest.fixture
def repo(sessoes, config):
    return RepositorioUsuarios(sessoes, Cifra(config.chave))
