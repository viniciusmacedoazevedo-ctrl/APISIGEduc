"""Modelos dos registros que serão lançados no SIGEduc."""

from __future__ import annotations

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
