param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"
if (-not (Test-Path $envPath)) {
    throw "Enterprise Lab .env is missing. Run ./scripts/setup.ps1 first."
}

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
Push-Location $repoRoot
try {
    if (-not (Test-Path $python)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Could not create the Python virtual environment." }
    }
    & $python -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) { throw "Could not install Enterprise Lab dependencies." }

    Write-Host "Starting Enterprise Lab API on 127.0.0.1:8088."
    Write-Host "This API connects to the Proxima Engine listener, not directly to PostgreSQL."
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port 8088
    if ($LASTEXITCODE -ne 0) { throw "Enterprise Lab API exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
}
