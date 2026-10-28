"""Lançamento de frequência. Os passos de navegação dependem de seletores A MAPEAR."""

from __future__ import annotations

import logging
from datetime import date
from itertools import groupby

from .. import seletores as S
from ..cliente import SigeducCliente
from ..modelos import Frequencia
from .base import Relatorio

log = logging.getLogger(__name__)


def _chave(f: Frequencia) -> tuple[str, str, date]:
    return (f.turma, f.disciplina, f.data)


def lancar_frequencia(cliente: SigeducCliente, registros: list[Frequencia]) -> Relatorio:
    """Agrupa por turma/disciplina/data para abrir cada aula uma única vez."""
    relatorio = Relatorio()
    page = cliente.page

    for (turma, disciplina, dia), grupo in groupby(sorted(registros, key=_chave), key=_chave):
        grupo = list(grupo)
        log.info("Turma %s / %s / %s: %d aluno(s)", turma, disciplina, f"{dia:%d/%m/%Y}", len(grupo))
        try:
            page.click(S.FREQ_MENU)
            page.click(S.FREQ_SELECIONAR_TURMA.format(turma=turma))
            page.click(S.FREQ_SELECIONAR_DISCIPLINA.format(disciplina=disciplina))
            page.fill(S.FREQ_DATA, f"{dia:%d/%m/%Y}")

            lancados = 0
            for registro in grupo:
                linha = page.locator(S.FREQ_LINHA_ALUNO.format(matricula=registro.matricula))
                if linha.count() == 0:
                    relatorio.falhas.append(f"{registro.matricula}: aluno não encontrado na turma {turma}")
                    continue
                linha.locator(S.FREQ_CHECK_FALTA).first.set_checked(not registro.presente)
                lancados += 1

            page.click(S.FREQ_SALVAR)
            page.locator(S.FREQ_SUCESSO).first.wait_for()
            relatorio.sucesso += lancados
        except Exception as e:
            cliente.capturar_tela(f"frequencia-{turma}")
            relatorio.falhas.append(f"{turma}/{disciplina}/{dia:%d/%m/%Y}: {e}")
        cliente.pausa()

    return relatorio
