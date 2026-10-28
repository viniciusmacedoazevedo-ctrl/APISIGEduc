# APISIGEduc

Sistema para alimentar o SIGEduc (sistema de gestão escolar da rede estadual do RN)
a partir de planilhas.

O SIGEduc não oferece uma API pública. Por isso o projeto automatiza o navegador
com o [Playwright](https://playwright.dev/python/): ele faz login, navega até as telas
de lançamento e preenche os dados como um usuário faria.

## Situação atual

| Parte | Situação |
|---|---|
| Leitura e validação de planilhas (.csv / .xlsx) | pronta e testada |
| Linha de comando (`apisigeduc`) | pronta |
| Login | implementado com os seletores padrão dos sistemas SIG; confirmar no sistema real |
| Lançamento de notas e frequência | estrutura pronta; seletores das telas **a mapear** em `src/apisigeduc/seletores.py` |

## Instalação

Requer Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env        # depois preencha usuário e senha
```

## Uso

```bash
# Testa as credenciais
apisigeduc login

# Valida a planilha sem acessar o SIGEduc
apisigeduc notas exemplos/notas.csv --simular

# Lança de verdade
apisigeduc notas minha_planilha.xlsx
apisigeduc frequencia minha_planilha.csv
```

Se a planilha tiver linhas com erro, nada é lançado e cada erro aparece com o número
da linha. Use `--ignorar-erros` para lançar só as linhas válidas. Em caso de falha
durante o lançamento, uma captura de tela é salva em `screenshots/`.

### Formato das planilhas

O CSV pode usar `;` ou `,` como separador. Os nomes das colunas não diferenciam maiúsculas.

**Notas** (`exemplos/notas.csv`): `matricula; turma; disciplina; bimestre; nota`
(nota de 0 a 10, aceita vírgula decimal).

**Frequência** (`exemplos/frequencia.csv`): `matricula; turma; disciplina; data; presenca`
(data em DD/MM/AAAA; presença `P` ou `F`).

## Mapeando as telas do SIGEduc

Os seletores de cada tela ficam todos em `src/apisigeduc/seletores.py`. Para descobrir os
seletores corretos:

```bash
apisigeduc mapear
```

Esse comando abre o navegador já logado com o Playwright Inspector. Navegue até a tela
desejada, use o botão "Pick locator" para clicar nos campos e copie os seletores para
`seletores.py`. Teste sempre primeiro em uma turma de teste.

## Estrutura

```
src/apisigeduc/
  cli.py          linha de comando
  config.py       leitura do .env
  cliente.py      sessão do navegador e login
  seletores.py    seletores das telas (único arquivo a ajustar se o layout mudar)
  modelos.py      Nota, Frequencia e suas validações
  planilhas.py    leitura de .csv/.xlsx
  tarefas/        rotinas de lançamento (notas, frequência)
tests/            testes (pytest)
exemplos/         planilhas de exemplo
```

## Testes

```bash
pytest
```

## Cuidados

- Nunca versione o arquivo `.env` (ele já está no `.gitignore`).
- Use apenas com credenciais próprias e para turmas sob sua responsabilidade.
- `SIGEDUC_PAUSA_MS` define uma pausa entre lançamentos para não sobrecarregar o sistema.
