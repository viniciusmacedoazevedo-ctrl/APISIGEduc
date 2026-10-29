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
# Botão de envio do formulário de login. Se nenhum for encontrado, o login
# formulário é enviado diretamente.
LOGIN_ENTRAR = (
    "form:has(input[name='user.senha']) input[type='submit'], "
    "form:has(input[name='user.senha']) input[type='image'], "
    "form:has(input[name='user.senha']) button"
)
# Verificação humana ("não sou um robô") que o SIGEduc pode exibir no login.
# Ela é resolvida por uma pessoa na janela do navegador; o programa só espera.
LOGIN_VERIFICACAO = (
    "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='turnstile'], "
    "iframe[src*='challenges.cloudflare'], .g-recaptcha, .h-captcha, .cf-turnstile, "
    "[id*='captcha' i], [class*='captcha' i], [name*='captcha' i]"
)
LOGIN_ERRO = ".erros, #painel-erros, .msgErro, .error, .alert-danger, #mensagens-erro"
# Elemento que só existe depois de logado (ex.: link "Sair"). Também se
# considera logado quando o campo de senha some da página.
LOGADO = "a:has-text('Sair'), a:has-text('SAIR')"

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

# --- Portal do Professor: turmas e estudantes --------------------------------
# Após o login aparece "Módulos do SIGEduc"; "Portal do Professor" é um quadro
# (ícone). Dentro da Escola Digital ele aparece no caminho (breadcrumb).
PORTAL_PROFESSOR = (
    "a:has-text('Portal do Professor'), "
    "input[value*='Portal do Professor'], "
    "button:has-text('Portal do Professor'), "
    ":text-is('Portal do Professor')"
)
# A tabela "Minhas Turmas" é localizada pelos nomes das colunas (tarefas/turmas.py);
# este seletor só indica que ela já carregou.
PORTAL_TABELA_TURMAS = "th:has-text('Componente'), td:text-is('Componente')"
# Ao clicar no código da turma (ex.: EFV6A) abre a Escola Digital da turma;
# no menu lateral Turmas > Estudantes ficam os cartões com nome e matrícula.
TURMA_ESTUDANTES = "a:text-is('Estudantes')"
# Menu lateral "Turmas" da Escola Digital. Quando está fechado, o item
# "Estudantes" fica escondido e é preciso abri-lo antes.
TURMA_MENU = "a:text-is('Turmas'), :text-is('Turmas')"
# Indica que a lista de estudantes carregou (cartões com "Matrícula: 000...").
TURMA_LISTA_ESTUDANTES = "text=/Matr[ií]cula\\s*:?\\s*\\d{4,}/"
