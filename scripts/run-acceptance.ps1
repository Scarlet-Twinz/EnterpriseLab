param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment missing. Run ./scripts/start-lab-api.ps1 once to install dependencies, then stop the API and retry."
}

$checks = @(
    @{ Name = "unit tests"; Args = @("-m", "pytest", "-q") },
    @{ Name = "live Proxima Engine protocol suite"; Args = @("scripts/proxima_protocol_suite.py") },
    @{ Name = "live Enterprise Lab attack suite"; Args = @("scripts/attack_suite.py") },
    @{ Name = "bounded performance sample"; Args = @("scripts/performance_suite.py") }
)

Push-Location $repoRoot
try {
    foreach ($check in $checks) {
        Write-Host ""
        Write-Host ("========== " + $check.Name + " ==========") -ForegroundColor Cyan
        $arguments = $check.Args
        & $python @arguments
        if ($LASTEXITCODE -ne 0) {
            throw ("Acceptance stage failed: " + $check.Name + " (exit code " + $LASTEXITCODE + "). Review the output and reports before continuing.")
        }
    }
    Write-Host ""
    Write-Host "All local acceptance stages passed. This still does not prove Control Plane, billing, SSO, TLS, backup/restore, or production acceptance." -ForegroundColor Green
} finally {
    Pop-Location
}
