"""Leitura das configurações a partir de variáveis de ambiente / arquivo .env.

As credenciais do SIGEduc não ficam aqui: cada usuário cadastra a sua na tela
web e ela é guardada no banco, criptografada pela chave SIGEDUC_CHAVE.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv


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
    segredo_sessao: str = ""
    headless: bool = True
    timeout_ms: int = 30000
    pausa_ms: int = 500
    # quanto tempo esperar uma pessoa resolver a verificação humana do login
    espera_verificacao_s: int = 300
    # média mínima para aprovação (rede estadual do RN: 6,0)
    media_minima: float = 6.0
    # janela diária de carga para a SEEC ("19:00"; vazio = só carga manual) e o modo
    janela_sincronia: str = ""
    modo_sincronia: str = "arquivo"  # arquivo | sigeduc

    @classmethod
    def carregar(cls) -> "Config":
        load_dotenv(find_dotenv(usecwd=True))  # .env da pasta onde o comando roda
        load_dotenv()  # senão, o .env da pasta do projeto
        banco_url = os.getenv("APISIGEDUC_BANCO_URL", "")
        if not banco_url:
            raise ConfigError(
                "Defina APISIGEDUC_BANCO_URL no .env, por exemplo "
                "postgresql://apisigeduc:senha@localhost:5432/apisigeduc"
            )
        return cls(
            url=os.getenv("SIGEDUC_URL", "https://sigeduc.rn.gov.br/sigeduc").rstrip("/"),
            banco_url=banco_url,
            chave=os.getenv("SIGEDUC_CHAVE", ""),
            segredo_sessao=os.getenv("APISIGEDUC_SEGREDO_SESSAO", ""),
            headless=_bool(os.getenv("SIGEDUC_HEADLESS"), True),
            timeout_ms=int(os.getenv("SIGEDUC_TIMEOUT_MS", "30000")),
            pausa_ms=int(os.getenv("SIGEDUC_PAUSA_MS", "500")),
            espera_verificacao_s=int(os.getenv("SIGEDUC_ESPERA_VERIFICACAO_S", "300")),
            media_minima=float(os.getenv("APISIGEDUC_MEDIA_MINIMA", "6").replace(",", ".")),
            janela_sincronia=os.getenv("APISIGEDUC_JANELA_SINCRONIA", "").strip(),
            modo_sincronia=os.getenv("APISIGEDUC_MODO_SINCRONIA", "arquivo").strip() or "arquivo",
        )
