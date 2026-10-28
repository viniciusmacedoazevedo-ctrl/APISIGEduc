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
    exit 0
}

Registrar "Atualizado de $($antes.Substring(0, 7)) para $($depois.Substring(0, 7)):"
git log --oneline "$antes..$depois" | ForEach-Object { Registrar "  $_" }

$mudou = git diff --name-only $antes $depois -- pyproject.toml
if ($mudou) {
    $python = Join-Path $projeto ".venv\Scripts\python.exe"
    if (Test-Path $python) {
        Registrar "pyproject.toml mudou: reinstalando dependências..."
        & $python -m pip install -q -e ".[dev]"
        if ($LASTEXITCODE -ne 0) { Registrar "ERRO ao reinstalar dependências."; exit 1 }
        Registrar "Dependências atualizadas."
    } else {
        Registrar "AVISO: .venv não encontrado; rode: pip install -e "".[dev]"""
    }
}
