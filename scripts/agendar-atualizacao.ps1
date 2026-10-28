# Cria uma tarefa agendada do Windows que roda scripts\atualizar.ps1
# a cada N minutos (padrão: 10) enquanto você estiver logado.
#
# Uso (no PowerShell, dentro da pasta do projeto):
#   powershell -ExecutionPolicy Bypass -File scripts\agendar-atualizacao.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\agendar-atualizacao.ps1 -Minutos 30
#   powershell -ExecutionPolicy Bypass -File scripts\agendar-atualizacao.ps1 -Remover

param(
    [int]$Minutos = 10,
    [switch]$Remover
)

$ErrorActionPreference = "Stop"
$nome = "APISIGEduc - atualizar projeto"

if ($Remover) {
    Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Atualização automática removida."
    exit 0
}

$script = Join-Path $PSScriptRoot "atualizar.ps1"
$projeto = Split-Path $PSScriptRoot -Parent

$acao = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $projeto
$gatilho = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $Minutos) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$opcoes = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $opcoes `
    -Description "Traz as alterações do GitHub para $projeto" -Force | Out-Null

Write-Host "Pronto: o projeto será atualizado a cada $Minutos minutos."
Write-Host "Histórico em: $projeto\logs\atualizacao.log"
Write-Host "Para desligar: powershell -ExecutionPolicy Bypass -File scripts\agendar-atualizacao.ps1 -Remover"
