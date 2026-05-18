param(
    [string]$Python = "py",
    [string]$TorchIndexUrl = "https://download.pytorch.org/whl/cu126",
    [switch]$IncludeWhisper
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv"

Write-Host "Creating Python virtual environment: $Venv"
if ($Python -eq "py") {
    & py -3.11 -m venv $Venv
} else {
    & $Python -m venv $Venv
}

$PythonExe = Join-Path $Venv "Scripts\python.exe"

Write-Host "Upgrading pip"
& $PythonExe -m pip install --upgrade pip

Write-Host "Installing PyTorch from $TorchIndexUrl"
& $PythonExe -m pip install torch torchvision torchaudio --index-url $TorchIndexUrl

Write-Host "Installing backend dependencies"
& $PythonExe -m pip install -r (Join-Path $Root "requirements-base.txt")
& $PythonExe -m pip install -r (Join-Path $Root "requirements-nemo.txt")
if ($IncludeWhisper) {
    & $PythonExe -m pip install -r (Join-Path $Root "requirements-whisper.txt")
}

Write-Host ""
Write-Host "Done. Start the service with:"
Write-Host "  .\.venv\Scripts\python.exe -m uvicorn gros_pouce.server:app --app-dir backend --host 127.0.0.1 --port 47891"
