"""Seletores das telas do SIGEduc, centralizados em um só lugar.

Os seletores de login seguem o padrão dos sistemas SIG (SIGAA/SIGEduc).
Os demais estão marcados com "A MAPEAR": precisam ser confirmados no sistema
real com `apisigeduc mapear` (abre o Playwright Inspector já logado).
Quando o layout do SIGEduc mudar, ajuste apenas este arquivo.
"""

# --- Login -----------------------------------------------------------------
LOGIN_PATH = "/verTelaLogin.do"
LOGIN_USUARIO = "input[name='user.login']"
LOGIN_SENHA = "input[name='user.senha']"
LOGIN_ENTRAR = "input[type='submit'][value='Entrar'], button:has-text('Entrar')"
LOGIN_ERRO = ".erros, #painel-erros, .msgErro"
# Elemento que só existe depois de logado (ex.: link "Sair").
LOGADO = "a:has-text('Sair')"

# --- Lançamento de notas (A MAPEAR) ----------------------------------------
NOTAS_MENU = "text=Lançar Notas"
NOTAS_SELECIONAR_TURMA = "text={turma}"
NOTAS_SELECIONAR_DISCIPLINA = "text={disciplina}"
NOTAS_SELECIONAR_BIMESTRE = "select[name*='bimestre']"
# Linha da tabela do aluno, localizada pela matrícula.
NOTAS_LINHA_ALUNO = "tr:has(td:text-is('{matricula}'))"
NOTAS_CAMPO_NOTA = "input[type='text']"
NOTAS_SALVAR = "input[value='Salvar'], button:has-text('Salvar')"
NOTAS_SUCESSO = ".info, .msgSucesso, text=sucesso"

# --- Lançamento de frequência (A MAPEAR) -----------------------------------
FREQ_MENU = "text=Lançar Frequência"
FREQ_SELECIONAR_TURMA = "text={turma}"
FREQ_SELECIONAR_DISCIPLINA = "text={disciplina}"
FREQ_DATA = "input[name*='data']"
FREQ_LINHA_ALUNO = "tr:has(td:text-is('{matricula}'))"
FREQ_CHECK_FALTA = "input[type='checkbox']"
FREQ_SALVAR = "input[value='Salvar'], button:has-text('Salvar')"
FREQ_SUCESSO = ".info, .msgSucesso, text=sucesso"
