$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (!(Test-Path '.venv')) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -r requirements.txt
if (!(Test-Path '.env')) { Copy-Item '.env.example' '.env'; Write-Host 'Edite worker\.env com as credenciais do seu Supabase antes de iniciar.'; exit 1 }
& .\.venv\Scripts\python.exe -m app.main
