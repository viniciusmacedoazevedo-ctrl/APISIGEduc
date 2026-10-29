"""Cópia falsa da tela "Lançar Notas" do SIGEduc (mesma estrutura da real, nomes
inventados), para testar a consulta sem acessar o SIGEduc."""

from __future__ import annotations


def _linha(i: int, nome: str, notas, faltas, freq, riscado: bool) -> str:
    base = f"formulario:notaEstudante:{i}"
    estilo = "text-align:left;" + ("text-decoration:line-through;" if riscado else "")
    desabilitado = ' disabled="disabled"' if riscado else ""
    celulas = [f'<td class="rich-table-cell digCol " id="{base}:j_id_62" style="{estilo}" title="Estudante">{nome}</td>']
    for b, n in enumerate(notas, start=1):
        valor = "" if riscado or n is None else f' value="{n}"'
        celulas.append(
            f'<td class="rich-table-cell digCol " title="Nota {b}º Bimestre"><input id="{base}:nota{b}" type="text" '
            f'name="{base}:nota{b}"{valor} class="id{650 + i}-n nota n{b}" maxlength="4" size="2" tabindex="{b}"{desabilitado}></td>'
        )
    celulas += [
        f'<td class="rich-table-cell digCol " title="Média Anual"><input id="{base}:mediaAnual" type="text" name="{base}:mediaAnual" class="ma noEdit" size="2"></td>',
        f'<td class="rich-table-cell digCol " title="Exame Final"><input id="{base}:exameFinal" type="text" name="{base}:exameFinal" class="ef noEdit" size="2"></td>',
        f'<td class="rich-table-cell digCol " title="Média Final"><input id="{base}:mediaFinal" type="text" name="{base}:mediaFinal" class="mf noEdit" size="2">'
        f'<input id="{base}:idMatriculaComponente" type="hidden" name="{base}:idMatriculaComponente" value="{650 + i}"></td>',
        f'<td class="rich-table-cell digCol " title="Faltas Calc."><input id="{base}:frequencia" type="text" name="{base}:frequencia" value="{faltas}" class="freq noEdit" size="2" disabled="disabled"></td>',
        f'<td class="rich-table-cell digCol " title="%Freq"><input id="{base}:frequePercentual" type="text" name="{base}:frequePercentual" value="{freq}" class="falPercent noEdit" size="3"><span>%</span></td>',
        f'<td class="rich-table-cell digCol " title="Res. Final"><span id="{base}:resultadoFinal" class="rf">-</span></td>',
    ]
    return '<tr class="rich-table-row ">' + "".join(celulas) + "</tr>"


def tela_notas(linhas, atraso_ms: int = 0, portal: str = "portal.html") -> str:
    """linhas: [(nome, [n1, n2, n3, n4], faltas, %freq, riscado)]. Com atraso_ms, a
    tabela só aparece depois (como o SIGEduc respondendo devagar)."""
    corpo = "".join(_linha(i, *l) for i, l in enumerate(linhas))
    tabela = (
        '<form id="formulario" name="formulario" method="post" action="/gravou">'
        '<table class="rich-table formulario responsiva" id="formulario:notaEstudante"><thead><tr>'
        '<th rowspan="2">Estudante</th><th colspan="4">Notas dos Bimestres</th><th>Média Anual</th><th>Exame Final</th>'
        '<th>Média Final</th><th colspan="2">Frequência</th><th>Res. Final</th></tr></thead>'
        f"<tbody>{corpo}</tbody></table>"
        '<input type="submit" name="formulario:notaEstudante:j_id_87" value="&lt;&lt; Voltar">'
        '<input type="submit" name="formulario:notaEstudante:j_id_88" value="Gravar" onclick="show_carregando();">'
        "</form>"
    )
    if atraso_ms:
        tabela = (
            f'<div id="carregando">Carregando...</div><template id="t">{tabela}</template>'
            f"<script>setTimeout(() => {{ document.getElementById('carregando').replaceWith("
            f"document.getElementById('t').content.cloneNode(true)); }}, {atraso_ms});</script>"
        )
    return (
        '<meta charset="utf-8"><div id="painel-mensagens"><ul><li>Atenção: O lançamento de notas só é permitido '
        "após a finalização do bimestre.</li></ul></div>"
        f'<h2><a href="{portal}">Portal do Professor</a> &gt; Lançar Notas </h2>'
        '<table class="formulario"><tr><th>Turma:</th><td>ENSINO FUNDAMENTAL - 6º ANO - EFV6A / VESPERTINO</td></tr>'
        '<tr><th>Total de Aulas Dadas:</th><td><span class="totalAulas">106</span></td></tr>'
        '<tr><th>Total de Aulas Previstas:</th><td><span class="totalAulas">148</span></td></tr>'
        '<tr><th>Percentual de Aulas Dadas:</th><td><span class="totalAulas">71,62%</span></td></tr></table>'
        + tabela
    )
