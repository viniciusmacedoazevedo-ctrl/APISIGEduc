"""Cadastro dos usuários do SIGEduc no banco."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .banco import UsuarioSigeduc
from .cripto import Cifra


class UsuarioNaoEncontrado(LookupError):
    pass


@dataclass(frozen=True)
class Credencial:
    login: str
    senha: str

    def __repr__(self) -> str:  # nunca exibir a senha em logs
        return f"Credencial(login={self.login!r}, senha='***')"


class RepositorioUsuarios:
    def __init__(self, sessoes: sessionmaker, cifra: Cifra):
        self._sessoes = sessoes
        self._cifra = cifra

    def _buscar(self, sessao, login: str) -> UsuarioSigeduc:
        usuario = sessao.scalar(select(UsuarioSigeduc).where(UsuarioSigeduc.login == login))
        if usuario is None:
            raise UsuarioNaoEncontrado(f"Usuário {login!r} não cadastrado")
        return usuario

    def adicionar(self, login: str, senha: str, nome: str = "", escola: str = "") -> None:
        if not login or not senha:
            raise ValueError("login e senha são obrigatórios")
        with self._sessoes.begin() as sessao:
            existente = sessao.scalar(select(UsuarioSigeduc).where(UsuarioSigeduc.login == login))
            if existente is not None:
                raise ValueError(f"Usuário {login!r} já cadastrado")
            sessao.add(
                UsuarioSigeduc(
                    login=login, nome=nome, escola=escola, senha_cifrada=self._cifra.cifrar(senha)
                )
            )

    def alterar_senha(self, login: str, senha: str) -> None:
        if not senha:
            raise ValueError("senha é obrigatória")
        with self._sessoes.begin() as sessao:
            self._buscar(sessao, login).senha_cifrada = self._cifra.cifrar(senha)

    def definir_ativo(self, login: str, ativo: bool) -> None:
        with self._sessoes.begin() as sessao:
            self._buscar(sessao, login).ativo = ativo

    def remover(self, login: str) -> None:
        with self._sessoes.begin() as sessao:
            sessao.delete(self._buscar(sessao, login))

    def listar(self) -> list[UsuarioSigeduc]:
        with self._sessoes() as sessao:
            return list(sessao.scalars(select(UsuarioSigeduc).order_by(UsuarioSigeduc.login)))

    def credencial(self, login: str) -> Credencial:
        with self._sessoes() as sessao:
            usuario = self._buscar(sessao, login)
            if not usuario.ativo:
                raise UsuarioNaoEncontrado(f"Usuário {login!r} está desativado")
            return Credencial(usuario.login, self._cifra.decifrar(usuario.senha_cifrada))
