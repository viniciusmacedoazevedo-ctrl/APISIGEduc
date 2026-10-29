# APISIGEduc

Sistema para alimentar o SIGEduc (sistema de gestão escolar da rede estadual do RN)
a partir de planilhas.

Cada pessoa entra numa **tela web** com seu e-mail e senha, cadastra a própria senha do
SIGEduc e envia planilhas de notas ou frequência. As planilhas entram numa fila e são
lançadas em segundo plano, com o login do SIGEduc de quem enviou.

O SIGEduc não oferece uma API pública. Por isso o lançamento automatiza o navegador com o
[Playwright](https://playwright.dev/python/): ele faz login e preenche as telas como uma
pessoa faria.

## Situação atual

| Parte | Situação |
|---|---|
| Tela web: login, envio e conferência de planilhas, histórico de envios | pronta e testada |
| Usuários e administração (criar, desativar, redefinir senha) | pronta e testada |
| Senha do SIGEduc por usuário, criptografada no PostgreSQL | pronta e testada |
| Fila de processamento (worker) | pronta e testada |
| Importar turmas e estudantes do Portal do Professor | pronta; testada com telas no formato do SIGEduc, confirmar no sistema real |
| Composição de notas do bimestre (itens, nota para todos, média) e envio das médias | pronta e testada |
| Login no SIGEduc | funcionando; a verificação humana é resolvida por uma pessoa |
| Lançamento de notas e frequência | estrutura pronta; seletores das telas **a mapear** em `src/apisigeduc/seletores.py` |

## Como funciona

```
 navegador ──► tela web (FastAPI) ──► PostgreSQL ◄── worker ──► SIGEduc (Playwright)
                 login, envio          usuarios        pega o envio da fila,
                 de planilhas          credenciais     entra com a senha do
                                       envios          dono e lança os dados
```

- **Senha de acesso à tela web:** guardada só como hash (scrypt), nunca pode ser lida.
- **Senha do SIGEduc:** precisa ser recuperada para ser digitada no SIGEduc, então é
  **criptografada** (Fernet/AES) com a chave `SIGEDUC_CHAVE`, que fica no `.env`, fora do
  banco. Quem tiver só o banco não lê as senhas.
- Os formulários têm proteção CSRF e a sessão expira em 8 horas.

## Instalação

Requer Python 3.10+ e PostgreSQL 13+.

### 1. Banco de dados

No PostgreSQL (pelo pgAdmin ou `psql`), crie um usuário e um banco:

```sql
CREATE USER apisigeduc WITH PASSWORD 'troque-esta-senha';
CREATE DATABASE apisigeduc OWNER apisigeduc;
```

As tabelas são criadas automaticamente na primeira execução.

### 2. Projeto

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env        # Windows: copy .env.example .env
apisigeduc gerar-chave      # copie as linhas geradas para o .env
```

No `.env`, preencha `APISIGEDUC_BANCO_URL` com a senha do banco e cole as chaves geradas.
**Guarde uma cópia da `SIGEDUC_CHAVE` em local seguro**: se ela for perdida, todas as
senhas do SIGEduc precisam ser cadastradas de novo.

### 3. Primeiro administrador

```bash
apisigeduc usuario adicionar seu@email.com --nome "Seu Nome" --admin
```

### 4. Iniciar

```bash
apisigeduc web
```

Abra http://127.0.0.1:8000 e entre com o administrador. Pela tela **Usuários** você cria
as contas das demais pessoas. Para outros computadores da rede acessarem, use
`apisigeduc web --host 0.0.0.0` e acesse pelo IP da máquina (ex.: http://192.168.0.10:8000).

O comando `web` já processa a fila. Para separar, rode `apisigeduc web --sem-worker` e,
em outro terminal, `apisigeduc worker`. Vários workers podem rodar ao mesmo tempo sem
pegar o mesmo envio.

### Verificação humana no login

O SIGEduc pode pedir uma verificação ("não sou um robô") no login. O programa **não**
tenta resolvê-la: com o navegador visível (`SIGEDUC_HEADLESS=false`), ele preenche
usuário e senha, avisa no terminal e espera uma pessoa resolver a verificação e clicar em
Entrar na janela do navegador (até `SIGEDUC_ESPERA_VERIFICACAO_S` segundos). Depois
continua sozinho. Com o navegador invisível, o login falha com uma mensagem explicando isso.

Para usar a fila da tela web nesse caso, rode o `apisigeduc web` com
`SIGEDUC_HEADLESS=false` no computador onde alguém possa resolver a verificação quando a
janela abrir.

## Uso pela tela web

1. **Senha do SIGEduc:** cada pessoa cadastra o próprio login e senha do SIGEduc.
2. **Enviar planilha:** escolha Notas ou Frequência e o arquivo.
   - **Só conferir** mostra os erros e uma prévia, sem lançar nada.
   - **Conferir e lançar** coloca a planilha na fila se não houver erros. Se houver
     erros, nada é lançado, a menos que você marque "Lançar só as linhas válidas".
3. Acompanhe a situação na lista de envios; a página se atualiza sozinha. Clique no
   número do envio para ver as falhas linha a linha.

O administrador vê os envios de todos os usuários.

### Turmas e estudantes

No menu **Turmas**, o botão **Buscar turmas e estudantes no SIGEduc** põe na fila uma busca
que, com o login do SIGEduc da pessoa:

1. abre o **Portal do Professor** e lê a tabela "Minhas Turmas" (turma, escola, etapa de
   ensino, ano/série, componente e quantidade de estudantes);
2. em cada turma, abre a Escola Digital, vai em **Turmas > Estudantes** e lê o nome e a
   matrícula de cada estudante.

O código da turma é interpretado: `EFV6A` = Ensino Fundamental, Vespertino, 6º ano A
(`EFM` = Matutino). Se a quantidade lida diferir da informada pelo portal, a turma é
marcada. A cada busca a lista de estudantes de cada turma é substituída pela nova; se a
leitura de uma turma falhar, a lista anterior é mantida.

Em cada turma dá para baixar a lista de estudantes e **modelos de planilha de notas e de
frequência** já preenchidos com matrícula, nome, turma e componente.

Pelo terminal: `apisigeduc turmas --usuario seu@email.com`.

### Composição de notas

No SIGEduc entra só a média do bimestre. No menu **Notas** você monta como ela é formada,
**antes** de escolher a turma:

1. **Nova composição:** nome (ex.: "3º bimestre - 6º anos"), bimestre, ano e os itens, cada
   um com quanto vale (ex.: Trabalho vale 2, Teste vale 3, Prova vale 5).
   - **Nota para todos** (opcional): quando todos tiraram a mesma nota no item (ex.: um
     trabalho em grupo valendo 2). Ela é lançada para todos os alunos ao aplicar.
   - **Cálculo:** *soma dos itens* (os itens somam até 10) ou *média dos itens* (cada item
     convertido para 0 a 10). A média é arredondada para 1 casa (8,25 → 8,3).
2. **Aplicar em turmas:** marque as turmas (6º A, 6º B...). A mesma composição vale para
   todas e pode ser **copiada** para outro bimestre.
3. **Lançar notas:** em cada turma, uma grade com os estudantes e os itens. A linha
   "todos" preenche a coluna inteira; depois é só ajustar aluno por aluno. A média aparece
   enquanto você digita, e notas acima do valor do item são recusadas.
4. **Enviar médias ao SIGEduc:** as médias entram na fila de lançamento com o seu login.
   Se faltar nota de algum aluno, dá para enviar só os completos.

As notas ficam ligadas à turma e à matrícula, então continuam valendo depois de
**Atualizar turmas** a partir do SIGEduc.

### Formato das planilhas

O CSV pode usar `;` ou `,` como separador (CSV salvo pelo Excel também funciona). Os
nomes das colunas não diferenciam maiúsculas.

**Notas** (`exemplos/notas.csv`): `matricula; turma; disciplina; bimestre; nota`
(nota de 0 a 10, aceita vírgula decimal).

**Frequência** (`exemplos/frequencia.csv`): `matricula; turma; disciplina; data; presenca`
(data em DD/MM/AAAA; presença `P` ou `F`).

## Linha de comando

```bash
apisigeduc usuario listar
apisigeduc usuario adicionar maria@escola.br --nome "Maria Silva"
apisigeduc usuario senha maria@escola.br           # redefine a senha de acesso
apisigeduc usuario desativar maria@escola.br       # bloqueia sem apagar o histórico
apisigeduc credencial maria@escola.br --login prof.maria   # senha do SIGEduc

apisigeduc login --usuario maria@escola.br         # testa o login no SIGEduc
apisigeduc notas exemplos/notas.csv --simular      # só confere a planilha
apisigeduc notas planilha.xlsx --usuario maria@escola.br   # lança direto, sem fila
```

As senhas são sempre pedidas no terminal (não aparecem na tela nem ficam no histórico).
Use `apisigeduc -v ...` para ver detalhes de um erro.

## Mapeando as telas do SIGEduc

Os seletores de cada tela ficam todos em `src/apisigeduc/seletores.py`. Para descobrir
os seletores corretos:

```bash
apisigeduc mapear --usuario seu@email.com
```

Esse comando abre o navegador já logado com o Playwright Inspector. Navegue até a tela
desejada, use o botão "Pick locator" para clicar nos campos e copie os seletores para
`seletores.py`. Teste sempre primeiro em uma turma de teste.

## Manter a pasta do projeto atualizada (Windows)

Com a pasta do projeto ligada ao GitHub (cópia Git), as alterações publicadas chegam
nela com:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\atualizar.ps1
```

Para atualizar sozinho a cada 10 minutos (tarefa agendada do Windows):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\agendar-atualizacao.ps1
```

O histórico fica em `logs\atualizacao.log`. `.env`, `.venv` e `screenshots` nunca são
alterados pela atualização. Se o `pyproject.toml` mudar, as dependências são
reinstaladas automaticamente. Depois de uma atualização, reinicie o `apisigeduc web`.

## Estrutura

```
src/apisigeduc/
  cli.py          linha de comando
  config.py       leitura do .env
  banco.py        tabelas: usuarios, credenciais_sigeduc, envios, turmas, estudantes,
                  composicoes, itens_composicao, composicoes_turmas, notas_itens
  senhas.py       hash das senhas de acesso
  cripto.py       criptografia das senhas do SIGEduc
  usuarios.py     usuários e credenciais
  envios.py       fila de planilhas e worker
  turmas.py       turmas e estudantes importados
  composicoes.py  composição das notas do bimestre e cálculo da média
  web/            tela web (FastAPI + modelos HTML); web/notas.py: telas de notas
  cliente.py      sessão do navegador e login no SIGEduc
  seletores.py    seletores das telas (único arquivo a ajustar se o layout mudar)
  modelos.py      Nota, Frequencia e suas validações
  planilhas.py    leitura de .csv/.xlsx
  tarefas/        rotinas no SIGEduc (notas, frequência, leitura de turmas)
tests/            testes (pytest)
exemplos/         planilhas de exemplo
```

## Testes

```bash
pytest
```

Os testes usam SQLite. Para rodá-los também no PostgreSQL, crie um banco vazio só para
testes e defina `APISIGEDUC_TESTE_BANCO_URL` (ele é apagado a cada teste).

## Cuidados

- Nunca versione o arquivo `.env` (ele já está no `.gitignore`).
- Use apenas com credenciais próprias e para turmas sob sua responsabilidade.
- Nomes e matrículas de estudantes são dados pessoais (LGPD): restrinja o acesso ao
  servidor e ao banco, e não compartilhe as listas exportadas fora da escola.
- Para acesso fora da rede local, coloque a tela web atrás de HTTPS (ex.: IIS, nginx ou
  Caddy como proxy reverso) e inicie com `apisigeduc web --https`.
- `SIGEDUC_PAUSA_MS` define uma pausa entre lançamentos para não sobrecarregar o sistema.
