"""Usuários da tela web e suas credenciais do SIGEduc."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import senhas
from .banco import CredencialSigeduc, Usuario
from .cripto import Cifra

TAMANHO_MINIMO_SENHA = 8


class UsuarioNaoEncontrado(LookupError):
    pass


@dataclass(frozen=True)
class Credencial:
    login: str
    senha: str

    def __repr__(self) -> str:  # nunca exibir a senha em logs
        return f"Credencial(login={self.login!r}, senha='***')"


def _validar_senha_acesso(senha: str) -> None:
    if len(senha) < TAMANHO_MINIMO_SENHA:
        raise ValueError(f"A senha precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres")


class RepositorioUsuarios:
    def __init__(self, sessoes: sessionmaker, cifra: Cifra):
        self._sessoes = sessoes
        self._cifra = cifra

    # --- usuários da tela web -------------------------------------------
    def _buscar(self, sessao, usuario_id: int) -> Usuario:
        usuario = sessao.get(Usuario, usuario_id)
        if usuario is None:
            raise UsuarioNaoEncontrado(f"Usuário {usuario_id} não encontrado")
        return usuario

    def criar(self, email: str, nome: str, senha: str, admin: bool = False) -> Usuario:
        email = email.strip().lower()
        if not email:
            raise ValueError("e-mail é obrigatório")
        _validar_senha_acesso(senha)
        with self._sessoes.begin() as sessao:
            if sessao.scalar(select(Usuario).where(Usuario.email == email)):
                raise ValueError(f"Já existe usuário com o e-mail {email}")
            usuario = Usuario(email=email, nome=nome.strip(), senha_hash=senhas.gerar_hash(senha), admin=admin)
            sessao.add(usuario)
        return usuario

    def por_email(self, email: str) -> Usuario:
        with self._sessoes() as sessao:
            usuario = sessao.scalar(select(Usuario).where(Usuario.email == email.strip().lower()))
            if usuario is None:
                raise UsuarioNaoEncontrado(f"Usuário {email} não encontrado")
            return usuario

    def obter(self, usuario_id: int) -> Usuario:
        with self._sessoes() as sessao:
            return self._buscar(sessao, usuario_id)

    def autenticar(self, email: str, senha: str) -> Usuario | None:
        try:
            usuario = self.por_email(email)
        except UsuarioNaoEncontrado:
            senhas.gerar_hash(senha)  # mesmo custo de tempo: não revela se o e-mail existe
            return None
        if usuario.ativo and senhas.verificar(senha, usuario.senha_hash):
            return usuario
        return None

    def alterar_senha(self, usuario_id: int, senha: str) -> None:
        _validar_senha_acesso(senha)
        with self._sessoes.begin() as sessao:
            self._buscar(sessao, usuario_id).senha_hash = senhas.gerar_hash(senha)

    def definir_ativo(self, usuario_id: int, ativo: bool) -> None:
        with self._sessoes.begin() as sessao:
            self._buscar(sessao, usuario_id).ativo = ativo

    def listar(self) -> list[Usuario]:
        with self._sessoes() as sessao:
            return list(sessao.scalars(select(Usuario).order_by(Usuario.nome, Usuario.email)))

    # --- credencial do SIGEduc ------------------------------------------
    def definir_credencial(self, usuario_id: int, login: str, senha: str, escola: str = "") -> None:
        if not login.strip() or not senha:
            raise ValueError("login e senha do SIGEduc são obrigatórios")
        with self._sessoes.begin() as sessao:
            self._buscar(sessao, usuario_id)
            cred = sessao.scalar(select(CredencialSigeduc).where(CredencialSigeduc.usuario_id == usuario_id))
            if cred is None:
                cred = CredencialSigeduc(usuario_id=usuario_id)
                sessao.add(cred)
            cred.login = login.strip()
            cred.escola = escola.strip()
            cred.senha_cifrada = self._cifra.cifrar(senha)

    def dados_credencial(self, usuario_id: int) -> CredencialSigeduc | None:
        """Login e escola cadastrados (sem decifrar a senha), ou None."""
        with self._sessoes() as sessao:
            return sessao.scalar(select(CredencialSigeduc).where(CredencialSigeduc.usuario_id == usuario_id))

    def credencial(self, usuario_id: int) -> Credencial:
        with self._sessoes() as sessao:
            usuario = self._buscar(sessao, usuario_id)
            if not usuario.ativo:
                raise UsuarioNaoEncontrado(f"Usuário {usuario.email} está desativado")
            cred = sessao.scalar(select(CredencialSigeduc).where(CredencialSigeduc.usuario_id == usuario_id))
            if cred is None:
                raise UsuarioNaoEncontrado(f"Usuário {usuario.email} não cadastrou a senha do SIGEduc")
            return Credencial(cred.login, self._cifra.decifrar(cred.senha_cifrada))
