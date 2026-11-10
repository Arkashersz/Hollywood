<#
  TikTok Studio — abre tudo com um clique.
  Inicia (se ainda não estiverem rodando) o worker e o painel local e abre o painel como aplicativo.
  Uso:  .\iniciar-tiktok-studio.ps1          inicia e abre
        .\iniciar-tiktok-studio.ps1 -Parar   encerra o worker e o painel local
#>
param([switch]$Parar, [int]$Porta = 4173)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$web = Join-Path $root 'apps\web'
$pyw = Join-Path $root 'worker\.venv\Scripts\python.exe'

function Get-Procs($needle) {
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like "*$needle*" -and $_.ProcessId -ne $PID }
}

if ($Parar) {
  foreach ($p in @(Get-Procs 'app.main') + @(Get-Procs "vite*preview*$Porta")) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  Write-Host 'Worker e painel local encerrados.'
  exit 0
}

# 1) Painel compilado (a primeira vez, ou se o código mudou)
$dist = Join-Path $web 'dist\index.html'
$newest = (Get-ChildItem (Join-Path $web 'src'), (Join-Path $web 'public'), (Join-Path $web '.env') -Recurse -File -ErrorAction SilentlyContinue | Measure-Object LastWriteTime -Maximum).Maximum
if (!(Test-Path $dist) -or (Get-Item $dist).LastWriteTime -lt $newest) {
  Write-Host 'Preparando o painel…'
  Push-Location $root; npm run build | Out-Null; Pop-Location
}

# 2) Worker
if (!(Get-Procs 'app.main')) {
  if (!(Test-Path (Join-Path $root 'worker\.env'))) { throw 'Falta worker\.env — veja o README.' }
  Start-Process -WindowStyle Minimized -WorkingDirectory (Join-Path $root 'worker') -FilePath $pyw -ArgumentList '-m', 'app.main'
  Write-Host 'Worker iniciado.'
}

# 3) Painel local
$listening = Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue
if (!$listening) {
  Start-Process -WindowStyle Hidden -WorkingDirectory $web -FilePath 'cmd.exe' -ArgumentList '/c', "npx vite preview --port $Porta --strictPort --host 127.0.0.1"
  for ($i = 0; $i -lt 40 -and !(Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 500 }
}
$url = "http://127.0.0.1:$Porta"

# 4) Abre como aplicativo (janela própria, sem barra de endereço)
$browsers = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
              "$env:LocalAppData\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe")
$exe = $browsers | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($exe) {
  # perfil separado do Chrome do worker, para não interferir nele
  $profile = Join-Path $env:LocalAppData 'TikTokStudio\panel-profile'
  Start-Process $exe -ArgumentList "--app=$url", "--user-data-dir=$profile", '--no-first-run', '--window-size=1360,900'
} else { Start-Process $url }
