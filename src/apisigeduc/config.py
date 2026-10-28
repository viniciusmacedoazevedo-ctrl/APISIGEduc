"""Leitura das configurações a partir de variáveis de ambiente / arquivo .env."""

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
    usuario: str
    senha: str
    headless: bool = True
    timeout_ms: int = 30000
    pausa_ms: int = 500

    @classmethod
    def carregar(cls, exigir_credenciais: bool = True) -> "Config":
        load_dotenv()
        usuario = os.getenv("SIGEDUC_USUARIO", "")
        senha = os.getenv("SIGEDUC_SENHA", "")
        if exigir_credenciais and (not usuario or not senha):
            raise ConfigError(
                "Defina SIGEDUC_USUARIO e SIGEDUC_SENHA no arquivo .env "
                "(veja .env.example)."
            )
        return cls(
            url=os.getenv("SIGEDUC_URL", "https://sigeduc.rn.gov.br/sigeduc").rstrip("/"),
            usuario=usuario,
            senha=senha,
            headless=_bool(os.getenv("SIGEDUC_HEADLESS"), True),
            timeout_ms=int(os.getenv("SIGEDUC_TIMEOUT_MS", "30000")),
            pausa_ms=int(os.getenv("SIGEDUC_PAUSA_MS", "500")),
        )
