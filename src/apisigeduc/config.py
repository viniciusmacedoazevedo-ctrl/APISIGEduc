"""Leitura das configurações a partir de variáveis de ambiente / arquivo .env.

As credenciais do SIGEduc não ficam aqui: cada usuário é cadastrado no banco
(veja usuarios.py), com a senha criptografada pela chave SIGEDUC_CHAVE.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


def _bool(valor: str | None, padrao: bool) -> bool:
    if valor is None or valor.strip() == "":
        return padrao
    return valor.strip().lower() in {"1", "true", "sim", "yes", "s"}


@dataclass(frozen=True)
class Config:
    url: str
    banco_url: str
    chave: str
    headless: bool = True
    timeout_ms: int = 30000
    pausa_ms: int = 500

    @classmethod
    def carregar(cls) -> "Config":
        load_dotenv()
        return cls(
            url=os.getenv("SIGEDUC_URL", "https://sigeduc.rn.gov.br/sigeduc").rstrip("/"),
            banco_url=os.getenv("APISIGEDUC_BANCO_URL", "sqlite:///apisigeduc.db"),
            chave=os.getenv("SIGEDUC_CHAVE", ""),
            headless=_bool(os.getenv("SIGEDUC_HEADLESS"), True),
            timeout_ms=int(os.getenv("SIGEDUC_TIMEOUT_MS", "30000")),
            pausa_ms=int(os.getenv("SIGEDUC_PAUSA_MS", "500")),
        )
