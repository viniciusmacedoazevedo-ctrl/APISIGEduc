"""Modelos dos registros lidos do SIGEduc ou lançados nele."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Nota:
    matricula: str
    turma: str
    disciplina: str
    bimestre: int
    nota: float

    def validar(self) -> None:
        if not self.matricula:
            raise ValueError("matrícula vazia")
        if not self.turma:
            raise ValueError("turma vazia")
        if not self.disciplina:
            raise ValueError("disciplina vazia")
        if self.bimestre not in (1, 2, 3, 4):
            raise ValueError(f"bimestre inválido: {self.bimestre}")
        if not 0 <= self.nota <= 10:
            raise ValueError(f"nota fora do intervalo 0-10: {self.nota}")


@dataclass(frozen=True)
class Frequencia:
    matricula: str
    turma: str
    disciplina: str
    data: date
    presente: bool

    def validar(self) -> None:
        if not self.matricula:
            raise ValueError("matrícula vazia")
        if not self.turma:
            raise ValueError("turma vazia")
        if not self.disciplina:
            raise ValueError("disciplina vazia")


MODALIDADES = {"EF": "Ensino Fundamental", "EM": "Ensino Médio"}
TURNOS = {"M": "Matutino", "V": "Vespertino", "N": "Noturno", "I": "Integral"}
CODIGO_TURMA = re.compile(r"\b([A-Z]{2})([A-Z])(\d{1,2})([A-Z]{1,2})\b")


def extrair_codigo_turma(texto: str) -> str:
    """Acha um código como EFV6A num texto; devolve o texto limpo se não achar."""
    achado = CODIGO_TURMA.search(texto.upper())
    return achado.group(0) if achado else texto.strip()


def descrever_turma(codigo: str) -> str:
    """EFV6A -> 'Ensino Fundamental, vespertino, 6º ano A'. Vazio se não reconhecer."""
    achado = CODIGO_TURMA.fullmatch(codigo.upper())
    if not achado:
        return ""
    modalidade, turno, serie, letra = achado.groups()
    partes = [
        MODALIDADES.get(modalidade, modalidade),
        TURNOS.get(turno, f"turno {turno}").lower(),
        f"{serie}º ano {letra}",
    ]
    return ", ".join(partes)


@dataclass(frozen=True)
class TurmaPortal:
    """Linha da tabela de turmas do Portal do Professor."""

    codigo: str
    escola: str = ""
    etapa: str = ""
    ano_serie: str = ""
    componente: str = ""
    qtd_estudantes: int | None = None


@dataclass(frozen=True)
class Estudante:
    matricula: str
    nome: str
