"""Criptografia das senhas do SIGEduc guardadas no banco.

A senha precisa ser recuperada para ser digitada no SIGEduc, então não dá para
usar hash: ela é cifrada com Fernet (AES-128-CBC + HMAC-SHA256). A chave fica
fora do banco, na variável SIGEDUC_CHAVE. Sem ela as senhas não podem ser lidas;
se ela for perdida, todas as senhas precisam ser cadastradas de novo.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from .config import ConfigError


def gerar_chave() -> str:
    return Fernet.generate_key().decode()


class Cifra:
    def __init__(self, chave: str):
        if not chave:
            raise ConfigError(
                "Defina SIGEDUC_CHAVE no arquivo .env. Gere uma com: apisigeduc gerar-chave"
            )
        try:
            self._fernet = Fernet(chave.encode())
        except ValueError as e:
            raise ConfigError("SIGEDUC_CHAVE inválida. Gere uma com: apisigeduc gerar-chave") from e

    def cifrar(self, texto: str) -> str:
        return self._fernet.encrypt(texto.encode()).decode()

    def decifrar(self, cifrado: str) -> str:
        try:
            return self._fernet.decrypt(cifrado.encode()).decode()
        except InvalidToken as e:
            raise ConfigError(
                "Não foi possível decifrar a senha: SIGEDUC_CHAVE diferente da usada no cadastro."
            ) from e
