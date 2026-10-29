"""Banco de dados (SQLAlchemy + PostgreSQL).

APISIGEDUC_BANCO_URL, por exemplo:
    postgresql://apisigeduc:senha@localhost:5432/apisigeduc
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Usuario(Base):
    """Pessoa que acessa a tela web. A senha de acesso fica com hash (scrypt)."""

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    nome: Mapped[str] = mapped_column(String(200), default="")
    senha_hash: Mapped[str] = mapped_column(String(300))
    admin: Mapped[bool] = mapped_column(Boolean, default=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CredencialSigeduc(Base):
    """Login do SIGEduc de um usuário. A senha fica cifrada (precisa ser recuperada)."""

    __tablename__ = "credenciais_sigeduc"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), unique=True)
    login: Mapped[str] = mapped_column(String(100))
    escola: Mapped[str] = mapped_column(String(200), default="")
    senha_cifrada: Mapped[str] = mapped_column(String(500))
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Envio(Base):
    """Planilha enviada para lançamento no SIGEduc, processada em segundo plano."""

    __tablename__ = "envios"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(20))  # notas | frequencia | turmas
    arquivo_nome: Mapped[str] = mapped_column(String(255))
    arquivo: Mapped[bytes] = mapped_column(LargeBinary)
    # pendente -> processando -> concluido | concluido_com_falhas | falhou
    status: Mapped[str] = mapped_column(String(30), default="pendente", index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    sucesso: Mapped[int] = mapped_column(Integer, default=0)
    falhas: Mapped[str] = mapped_column(Text, default="")
    mensagem: Mapped[str] = mapped_column(Text, default="")
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    iniciado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Turma(Base):
    """Turma do professor, importada do Portal do Professor do SIGEduc."""

    __tablename__ = "turmas"
    __table_args__ = (UniqueConstraint("usuario_id", "codigo", "componente", "escola"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    codigo: Mapped[str] = mapped_column(String(50))  # ex.: EFV6A
    escola: Mapped[str] = mapped_column(String(300), default="")
    etapa: Mapped[str] = mapped_column(String(200), default="")
    ano_serie: Mapped[str] = mapped_column(String(100), default="")
    componente: Mapped[str] = mapped_column(String(200), default="")
    qtd_estudantes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # informado pelo portal
    sincronizado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Estudante(Base):
    __tablename__ = "estudantes"
    __table_args__ = (UniqueConstraint("turma_id", "matricula"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    turma_id: Mapped[int] = mapped_column(ForeignKey("turmas.id", ondelete="CASCADE"), index=True)
    matricula: Mapped[str] = mapped_column(String(50))
    nome: Mapped[str] = mapped_column(String(300))


def normalizar_url(url: str) -> str:
    # "postgresql://" usaria o driver psycopg2; o projeto usa o psycopg 3.
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.split("://", 1)[1]
    return url


def criar_sessoes(banco_url: str) -> sessionmaker:
    engine = create_engine(normalizar_url(banco_url), pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
