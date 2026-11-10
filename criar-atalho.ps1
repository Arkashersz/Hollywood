<#
  Cria o atalho "TikTok Studio" na Área de Trabalho (abre o worker + o painel como aplicativo).
  Opcional: -InicioAutomatico também registra o worker para iniciar sozinho quando você entrar no Windows.
#>
param([switch]$InicioAutomatico)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $desktop 'TikTok Studio.lnk'))
$link.TargetPath = (Get-Command powershell.exe).Source
$link.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $root 'iniciar-tiktok-studio.ps1')`""
$link.WorkingDirectory = $root
$link.IconLocation = Join-Path $root 'apps\web\public\tiktok-studio.ico'
$link.Description = 'Abre o TikTok Studio'
$link.Save()
Write-Host "Atalho criado em $desktop"

if ($InicioAutomatico) {
  $action = New-ScheduledTaskAction -Execute (Get-Command powershell.exe).Source `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $root 'worker\start-worker.ps1')`"" -WorkingDirectory (Join-Path $root 'worker')
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName 'TikTokStudioWorker' -Action $action -Trigger $trigger -Settings $settings -Description 'Worker do TikTok Studio' -Force | Out-Null
  Write-Host 'Worker registrado para iniciar ao entrar no Windows (remova com: Unregister-ScheduledTask TikTokStudioWorker).'
}
