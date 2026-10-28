from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Relatorio:
    sucesso: int = 0
    falhas: list[str] = field(default_factory=list)

    def resumo(self) -> str:
        return f"{self.sucesso} lançado(s) com sucesso, {len(self.falhas)} falha(s)"
