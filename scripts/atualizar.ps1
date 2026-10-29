# Atualiza a pasta do projeto com as alterações publicadas no GitHub.
#
# Uso (no PowerShell, dentro da pasta do projeto):
#   powershell -ExecutionPolicy Bypass -File scripts\atualizar.ps1
#
# Traz os commits novos da branch atual (git pull --ff-only). Se o
# pyproject.toml mudou, reinstala as dependências no .venv. Os arquivos
# locais .env, apisigeduc.db, .venv e screenshots não são tocados.
# O resultado de cada execução fica em logs\atualizacao.log.

# "Continue": o git escreve progresso no stderr, o que o PowerShell 5.1
# trataria como erro. As falhas são verificadas por $LASTEXITCODE.
$ErrorActionPreference = "Continue"
# Nunca parar esperando senha (a tarefa agendada roda escondida).
$env:GIT_TERMINAL_PROMPT = "0"
$env:GCM_INTERACTIVE = "never"
$projeto = Split-Path $PSScriptRoot -Parent
Set-Location $projeto

New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$log = Join-Path $projeto "logs\atualizacao.log"

function Registrar([string]$mensagem) {
    $linha = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $mensagem
    Write-Host $linha
    Add-Content -Path $log -Value $linha -Encoding UTF8
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Registrar "ERRO: Git não encontrado. Instale em https://git-scm.com/download/win"
    exit 1
}

$antes = (git rev-parse HEAD).Trim()
$saida = cmd /c "git pull --ff-only 2>&1" | Out-String
if ($LASTEXITCODE -ne 0) {
    Registrar "ERRO ao atualizar: $($saida.Trim())"
    Registrar "Provavelmente há arquivos do projeto alterados nesta máquina. Veja com: git status"
    exit 1
}
$depois = (git rev-parse HEAD).Trim()

if ($antes -eq $depois) {
    Registrar "Já está atualizado ($($depois.Substring(0, 7)))."
} else {
    Registrar "Atualizado de $($antes.Substring(0, 7)) para $($depois.Substring(0, 7)):"
    git log --oneline "$antes..$depois" | ForEach-Object { Registrar "  $_" }
}

# --- dependências --------------------------------------------------------------
# Reinstala quando o pyproject.toml mudou, quando uma reinstalação anterior falhou
# (marca em logs\reinstalar-pendente) ou quando o pacote não está instalado.
$python = Join-Path $projeto ".venv\Scripts\python.exe"
$pendente = Join-Path $projeto "logs\reinstalar-pendente"
if (-not (Test-Path $python)) {
    Registrar "AVISO: .venv não encontrado; crie com: python -m venv .venv"
    exit 0
}
$motivo = $null
if ($antes -ne $depois -and (git diff --name-only $antes $depois -- pyproject.toml)) { $motivo = "o pyproject.toml mudou" }
elseif (Test-Path $pendente) { $motivo = "a reinstalação anterior não terminou" }
else {
    & $python -c "import apisigeduc" 2>$null
    if ($LASTEXITCODE -ne 0) { $motivo = "o pacote apisigeduc não está instalado" }
}
if (-not $motivo) { exit 0 }

# No Windows, o apisigeduc.exe em uso (apisigeduc web aberto) impede a reinstalação
$emUso = Get-Process -Name "apisigeduc" -ErrorAction SilentlyContinue
if ($emUso) {
    New-Item -ItemType File -Force -Path $pendente | Out-Null
    Registrar "AVISO: é preciso reinstalar as dependências ($motivo), mas o apisigeduc está aberto."
    Registrar "Feche o apisigeduc web (Ctrl+C na janela dele) e rode este script de novo."
    exit 1
}

Registrar "Reinstalando dependências ($motivo)..."
New-Item -ItemType File -Force -Path $pendente | Out-Null
$saida = & $python -m pip install --disable-pip-version-check -e ".[dev]" 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    $final = ($saida.Trim() -split "`r?`n" | Select-Object -Last 15) -join "`n"
    Registrar "ERRO ao reinstalar dependências. Últimas linhas do pip:`n$final"
    Registrar "Corrija e rode este script de novo (ele vai tentar outra vez)."
    exit 1
}
Remove-Item $pendente -ErrorAction SilentlyContinue
Registrar "Dependências atualizadas."
