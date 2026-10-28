"""Banco de dados (SQLAlchemy). Padrão: SQLite em apisigeduc.db.

Para PostgreSQL ou MySQL basta mudar APISIGEDUC_BANCO_URL, por exemplo
postgresql+psycopg://usuario:senha@servidor/apisigeduc (instale o driver).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class UsuarioSigeduc(Base):
    """Conta de acesso ao SIGEduc. A senha fica sempre cifrada."""

    __tablename__ = "usuarios_sigeduc"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    nome: Mapped[str] = mapped_column(String(200), default="")
    escola: Mapped[str] = mapped_column(String(200), default="")
    senha_cifrada: Mapped[str] = mapped_column(String(500))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


def criar_sessoes(banco_url: str) -> sessionmaker:
    engine = create_engine(banco_url)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
