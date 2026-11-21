# Start GTA VI Growth Studio (dashboard + API + workers) on http://127.0.0.1:8765
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -q -r requirements-studio.txt
Start-Process "http://127.0.0.1:8765"
.\.venv\Scripts\python -m studio serve --port 8765
