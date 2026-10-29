# APISIGEduc — contexto para continuar o desenvolvimento

Sistema para professores da rede estadual do RN: importa turmas e estudantes do SIGEduc,
monta a composição das notas do bimestre, calcula a média e envia ao SIGEduc. O SIGEduc
não tem API: o acesso é por automação de navegador (Playwright). Visão geral e instalação
no `README.md`.

## Ambiente do usuário

- Windows, PowerShell, pasta `C:\Acronis\Denis\APISIGEDUC`, ligada ao GitHub (cópia Git).
- Atualiza com `scripts\atualizar.ps1` (puxa a branch e reinstala dependências quando
  preciso). **O `apisigeduc web` precisa estar fechado** ao reinstalar: o Windows bloqueia
  o `apisigeduc.exe` em uso.
- Inicia o servidor com duplo clique em `Iniciar APISIGEduc.bat` (ou `apisigeduc web` com
  o `.venv` ativado: sem `(.venv)` no prompt, o comando `apisigeduc` não é encontrado).
- PostgreSQL local (banco e usuário `apisigeduc`). Usuário de teste do SIGEduc liberado
  para testes (dados reais desse usuário podem ser usados nos testes).
- Branch de trabalho: `claude/bold-heisenberg-d2vk9n`.
- Comunicação em português, passo a passo, com os comandos de PowerShell um por bloco.

## Situação

| Parte | Situação |
|---|---|
| Tela web multiusuário (FastAPI + Jinja), PostgreSQL, fila/worker | pronta |
| Senha do SIGEduc por usuário, cifrada (Fernet, `SIGEDUC_CHAVE`) | pronta |
| Login no SIGEduc | funciona; a verificação humana ("não sou um robô") é resolvida por uma pessoa com o navegador visível |
| Importar turmas e estudantes (Portal do Professor > Escola Digital > Turmas > Estudantes) | **funcionando no SIGEduc real** |
| Composição de notas em grupos e avaliações, média, Excel, modo chamada no celular, PWA | pronta e testada (falta uso real) |
| Desempenho: indicadores, distribuição, rankings, comparação de turmas, ano letivo | pronta e testada |
| Diário: chamada, aulas, frequência, ficha do aluno, anotações privadas (importante = sinal), fotos | pronta e testada |
| Sincronização: professor libera, carga manual (admin) ou agendada, arquivo padronizado ou robô, sem reenvio | pronta e testada |
| **Digitar as médias na tela de notas do SIGEduc** | **pendente**: seletores "A MAPEAR" em `seletores.py`, rotina em `tarefas/notas.py` |
| Lançamento de frequência no SIGEduc | pendente, mesma situação |

## Próximos passos

1. **Mapear a tela "Lançar Notas" do SIGEduc** (menu da Escola Digital da turma, ou menu
   "Notas" do Portal do Professor). Pedir ao usuário prints da tela com a lista de alunos e
   os campos do bimestre, o botão salvar e a mensagem de confirmação. Melhor ainda: os
   `.png` e `.html` salvos por `apisigeduc capturar --usuario EMAIL` (Enter no terminal
   salva a tela aberta no navegador). Depois reescrever
   `tarefas/notas.py` no estilo de `tarefas/turmas.py` (esperar o conteúdo de cada tela,
   localizar pela matrícula, testar contra uma cópia falsa da tela).
2. Mesmo processo para frequência.
3. Hospedagem com HTTPS para os professores instalarem o app no celular (PWA).
4. Ideias registradas, não iniciadas: gerar provas com IA (Taxonomia de Bloom) ligadas às
   avaliações das composições; correção assistida por IA só como sugestão confirmada pelo
   professor (dados de menores, LGPD).

## Decisões já tomadas (não refazer)

- **Não contornar a verificação humana do login** (nem mover o mouse/clicar
  automaticamente). Caminhos legítimos se voltar ao tema: reaproveitar a sessão aberta,
  pedir acesso oficial à SEEC.
- Senha do SIGEduc é cifrada (precisa ser recuperada); senha de acesso à tela usa hash.
- Notas ligadas a turma + matrícula (sobrevivem à atualização das turmas).
- Média: soma ou média em dois níveis (grupos e avaliações); arredondamento escolar com
  1 casa (8,25 → 8,3). Planilha Excel usa fórmulas com a mesma regra.
- Offline (decisão do usuário): o celular guarda só pendentes (apagados após sincronizar), a
  lista da turma e as fotos. Anotações: privadas de quem escreveu (nem o admin lê); só as
  marcadas como importantes sinalizam o aluno na nota e na chamada. Fotos guardadas no sistema,
  visíveis só a professores do aluno.
- Integração com a SEEC: proposta em doc (https://claude.ai/artifact/9eaHtcdx9FGUgQ9AfKLRdW);
  recomendação inicial = carga manual pelo administrador em janela combinada + arquivo padronizado.
- Média mínima 6,0 (RN, `APISIGEDUC_MEDIA_MINIMA`), média anual = média dos 4 bimestres.
  Termos da LDB: "rendimento escolar", "estudos de recuperação", "menor rendimento".
  Indicadores são de acompanhamento (a frequência, 75%, ainda não é importada).
- Gráficos em HTML/CSS seguindo a skill dataviz: uma cor (verde-quadro), referência 6,0
  rotulada, "abaixo da média" é situação em amarelo-giz com ícone e texto, tabela equivalente.
- Interface em português com só a primeira letra maiúscula em títulos e botões.
- Identidade visual: verde-quadro `#1E4D3B`, papel `#F6F8F6`, caneta vermelha só para
  erro de nota, giz amarelo para pendências, fonte Atkinson Hyperlegible.

## Como trabalhar neste repositório

- Código em `src/apisigeduc/`; seletores do SIGEduc só em `seletores.py`.
- Testes: `pytest` (SQLite). Para incluir PostgreSQL:
  `APISIGEDUC_TESTE_BANCO_URL=postgresql://...apisigeduc_teste`. Teste de leitura no
  navegador usa `PLAYWRIGHT_CHROMIUM_EXECUTABLE` quando o Chromium não é o padrão.
- Antes de enviar mudanças de tela do SIGEduc, testar contra páginas falsas no formato
  das telas reais (inclusive respondendo devagar e abrindo em nova aba).
- Bancos já existentes não são alterados por `create_all`: mudanças de tabela precisam de
  ajuste em `banco.migrar()`.
- `scripts/*.ps1` são para Windows PowerShell 5.1: UTF-8 com BOM, CRLF, e verificar
  `$LASTEXITCODE` (o `git`/`pip` escrevem no stderr).
