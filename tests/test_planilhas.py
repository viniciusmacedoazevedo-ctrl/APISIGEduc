from datetime import date
from pathlib import Path

from apisigeduc.planilhas import converter, frequencia_de_linha, ler_linhas, nota_de_linha

EXEMPLOS = Path(__file__).parent.parent / "exemplos"


def test_le_notas_de_exemplo():
    resultado = converter(ler_linhas(EXEMPLOS / "notas.csv"), nota_de_linha)
    assert resultado.erros == []
    assert len(resultado.registros) == 3
    assert resultado.registros[0].nota == 8.5
    assert resultado.registros[0].bimestre == 1


def test_le_frequencia_de_exemplo():
    resultado = converter(ler_linhas(EXEMPLOS / "frequencia.csv"), frequencia_de_linha)
    assert resultado.erros == []
    assert resultado.registros[0].data == date(2026, 3, 3)
    assert [r.presente for r in resultado.registros] == [True, False, True]


def test_acumula_erros_com_numero_da_linha():
    linhas = [
        {"matricula": "1", "turma": "A", "disciplina": "Mat", "bimestre": "5", "nota": "7"},
        {"matricula": "2", "turma": "A", "disciplina": "Mat", "bimestre": "1", "nota": "11"},
        {"matricula": "3", "turma": "A", "disciplina": "Mat", "bimestre": "1"},
        {"matricula": "4", "turma": "A", "disciplina": "Mat", "bimestre": "1", "nota": "6"},
    ]
    resultado = converter(linhas, nota_de_linha)
    assert len(resultado.registros) == 1
    assert resultado.erros[0].startswith("linha 2: bimestre inválido")
    assert resultado.erros[1].startswith("linha 3: nota fora")
    assert resultado.erros[2].startswith("linha 4: coluna ausente")


def test_le_xlsx(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Matricula", "Turma", "Disciplina", "Data", "Presenca"])
    ws.append([20260001, "1º Ano A", "Matemática", "03/03/2026", "F"])
    arquivo = tmp_path / "freq.xlsx"
    wb.save(arquivo)

    resultado = converter(ler_linhas(arquivo), frequencia_de_linha)
    assert resultado.erros == []
    assert resultado.registros[0].matricula == "20260001"
    assert resultado.registros[0].presente is False
