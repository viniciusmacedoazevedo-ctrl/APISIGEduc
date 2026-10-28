"""Lançamento de notas. Os passos de navegação dependem de seletores A MAPEAR."""

from __future__ import annotations

import logging
from itertools import groupby

from .. import seletores as S
from ..cliente import SigeducCliente
from ..modelos import Nota
from .base import Relatorio

log = logging.getLogger(__name__)


def _chave(n: Nota) -> tuple[str, str, int]:
    return (n.turma, n.disciplina, n.bimestre)


def lancar_notas(cliente: SigeducCliente, notas: list[Nota]) -> Relatorio:
    """Agrupa por turma/disciplina/bimestre para abrir cada diário uma única vez."""
    relatorio = Relatorio()
    page = cliente.page

    for (turma, disciplina, bimestre), grupo in groupby(sorted(notas, key=_chave), key=_chave):
        grupo = list(grupo)
        log.info("Turma %s / %s / %sº bimestre: %d nota(s)", turma, disciplina, bimestre, len(grupo))
        try:
            page.click(S.NOTAS_MENU)
            page.click(S.NOTAS_SELECIONAR_TURMA.format(turma=turma))
            page.click(S.NOTAS_SELECIONAR_DISCIPLINA.format(disciplina=disciplina))
            page.select_option(S.NOTAS_SELECIONAR_BIMESTRE, str(bimestre))

            lancados = 0
            for nota in grupo:
                linha = page.locator(S.NOTAS_LINHA_ALUNO.format(matricula=nota.matricula))
                if linha.count() == 0:
                    relatorio.falhas.append(f"{nota.matricula}: aluno não encontrado na turma {turma}")
                    continue
                linha.locator(S.NOTAS_CAMPO_NOTA).first.fill(f"{nota.nota:.1f}".replace(".", ","))
                lancados += 1

            page.click(S.NOTAS_SALVAR)
            page.locator(S.NOTAS_SUCESSO).first.wait_for()
            relatorio.sucesso += lancados
        except Exception as e:
            cliente.capturar_tela(f"notas-{turma}")
            relatorio.falhas.append(f"{turma}/{disciplina}/{bimestre}: {e}")
        cliente.pausa()

    return relatorio
