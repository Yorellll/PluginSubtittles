$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"

if (!(Test-Path $PythonExe)) {
    throw "Virtualenv not found. Run scripts\setup_windows_nemo.ps1 first."
}

Set-Location $Root
& $PythonExe -m uvicorn gros_pouce.server:app --app-dir backend --host 127.0.0.1 --port 47891
