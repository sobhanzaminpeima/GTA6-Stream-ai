# Builds dist\GTA6StreamAI.exe (single file, per-user install on first run).
# Usage (from the repo root):  powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path ".venv\Scripts\python.exe")) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -q -r requirements-studio.txt pyinstaller
.\.venv\Scripts\python -m PyInstaller --noconfirm --clean --onefile --console `
  --name GTA6StreamAI --icon packaging\app.ico `
  --add-data "studio\web;studio\web" `
  --collect-all imageio_ffmpeg `
  --collect-submodules uvicorn --collect-submodules websockets --collect-submodules studio `
  --hidden-import sqlalchemy.dialects.sqlite `
  --exclude-module pytest --exclude-module tkinter --exclude-module matplotlib `
  packaging\run_app.py
Write-Host "Built: dist\GTA6StreamAI.exe"
