$ErrorActionPreference = "Stop"
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Run scripts\setup.ps1 first." }
& .\.venv\Scripts\python.exe -m uvicorn backend.game:app --host 0.0.0.0 --port 8081 --reload
