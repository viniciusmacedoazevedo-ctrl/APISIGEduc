"""Banco de dados (SQLAlchemy + PostgreSQL).

APISIGEDUC_BANCO_URL, por exemplo:
    postgresql://apisigeduc:senha@localhost:5432/apisigeduc
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
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


class Composicao(Base):
    """Composição de notas de um bimestre (ex.: trabalho + teste + prova), criada
    antes de escolher a turma e aplicável a várias turmas."""

    __tablename__ = "composicoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    nome: Mapped[str] = mapped_column(String(200))
    ano_letivo: Mapped[int] = mapped_column(Integer)
    bimestre: Mapped[int] = mapped_column(Integer)
    calculo: Mapped[str] = mapped_column(String(20), default="soma")  # soma | media
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class GrupoComposicao(Base):
    """Grupo da composição (Trabalho, Teste, Prova), com quanto vale na média do
    bimestre e como combina as suas avaliações (soma ou média)."""

    __tablename__ = "grupos_composicao"

    id: Mapped[int] = mapped_column(primary_key=True)
    composicao_id: Mapped[int] = mapped_column(ForeignKey("composicoes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(100))
    valor: Mapped[float] = mapped_column(Float)
    calculo: Mapped[str] = mapped_column(String(20), default="soma")  # soma | media
    ordem: Mapped[int] = mapped_column(Integer, default=0)


class ItemComposicao(Base):
    """Avaliação de um grupo: Trabalho 1 (vale 2), Prova - Av1 (vale 10)..."""

    __tablename__ = "itens_composicao"

    id: Mapped[int] = mapped_column(primary_key=True)
    composicao_id: Mapped[int] = mapped_column(ForeignKey("composicoes.id", ondelete="CASCADE"), index=True)
    # nulo só em bancos anteriores aos grupos; migrar() cria um grupo para cada um
    grupo_id: Mapped[int | None] = mapped_column(
        ForeignKey("grupos_composicao.id", ondelete="CASCADE"), index=True, nullable=True
    )
    nome: Mapped[str] = mapped_column(String(100))
    valor_maximo: Mapped[float] = mapped_column(Float)
    # nota aplicada a todos os estudantes ao aplicar a composição numa turma
    nota_padrao: Mapped[float | None] = mapped_column(Float, nullable=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)


class ComposicaoTurma(Base):
    """Turma em que a composição foi aplicada."""

    __tablename__ = "composicoes_turmas"
    __table_args__ = (UniqueConstraint("composicao_id", "turma_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    composicao_id: Mapped[int] = mapped_column(ForeignKey("composicoes.id", ondelete="CASCADE"), index=True)
    turma_id: Mapped[int] = mapped_column(ForeignKey("turmas.id", ondelete="CASCADE"), index=True)
    aplicado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # médias enviadas ao SIGEduc


class NotaItem(Base):
    """Nota de um estudante num item. Ligada à turma + matrícula (e não ao id do
    estudante) para sobreviver às atualizações da lista vinda do SIGEduc."""

    __tablename__ = "notas_itens"
    __table_args__ = (UniqueConstraint("item_id", "turma_id", "matricula"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("itens_composicao.id", ondelete="CASCADE"), index=True)
    turma_id: Mapped[int] = mapped_column(ForeignKey("turmas.id", ondelete="CASCADE"), index=True)
    matricula: Mapped[str] = mapped_column(String(50))
    valor: Mapped[float] = mapped_column(Float)


def normalizar_url(url: str) -> str:
    # "postgresql://" usaria o driver psycopg2; o projeto usa o psycopg 3.
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.split("://", 1)[1]
    return url


def migrar(engine) -> None:
    """Ajustes em bancos criados por versões anteriores (create_all não altera tabelas)."""
    colunas = {c["name"] for c in inspect(engine).get_columns("itens_composicao")}
    with engine.begin() as conexao:
        if "grupo_id" not in colunas:
            conexao.execute(text("ALTER TABLE itens_composicao ADD COLUMN grupo_id INTEGER"))
        # itens antigos, sem grupo: cada um vira um grupo com uma avaliação (mesmo resultado)
        for item_id, composicao_id, nome, valor, ordem in conexao.execute(
            text("SELECT id, composicao_id, nome, valor_maximo, ordem FROM itens_composicao WHERE grupo_id IS NULL")
        ).all():
            grupo_id = conexao.execute(
                GrupoComposicao.__table__.insert()
                .values(composicao_id=composicao_id, nome=nome, valor=valor, calculo="soma", ordem=ordem)
                .returning(GrupoComposicao.__table__.c.id)
            ).scalar_one()
            conexao.execute(text("UPDATE itens_composicao SET grupo_id = :g WHERE id = :i"), {"g": grupo_id, "i": item_id})


def criar_sessoes(banco_url: str) -> sessionmaker:
    engine = create_engine(normalizar_url(banco_url), pool_pre_ping=True)
    Base.metadata.create_all(engine)
    migrar(engine)
    return sessionmaker(engine, expire_on_commit=False)
