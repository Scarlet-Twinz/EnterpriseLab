param(
    [Parameter(Mandatory = $true)]
    [string]$AgataRepoPath
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"
if (-not (Test-Path $envPath)) {
    throw "Enterprise Lab .env is missing. Run ./scripts/setup.ps1 first."
}
if (-not (Test-Path (Join-Path $AgataRepoPath "Cargo.toml"))) {
    throw "AgataRepoPath must point to the local Agata Proxima repository root containing Cargo.toml."
}

$values = @{}
foreach ($line in Get-Content $envPath) {
    if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) { continue }
    $separator = $line.IndexOf("=")
    if ($separator -lt 1) { continue }
    $key = $line.Substring(0, $separator)
    $value = $line.Substring($separator + 1)
    $values[$key] = $value
}
$signingKey = $values["PROXIMA_TENANT_SIGNING_KEY"]
if ([string]::IsNullOrWhiteSpace($signingKey) -or $signingKey.Length -lt 32) {
    throw "PROXIMA_TENANT_SIGNING_KEY is missing or too short in Enterprise Lab .env."
}

# Runtime-only configuration. This script does not edit files in Agata Proxima.
$env:PROXIMA_LISTEN_ADDR = "127.0.0.1:6432"
$env:PROXIMA_UPSTREAM_ADDR = "127.0.0.1:55432"
$env:PROXIMA_TENANT_SIGNING_KEY = $signingKey
$env:PROXIMA_TENANT_ROLE_PREFIX = "proxima_tenant_"
$env:PROXIMA_DASHBOARD_ENABLED = "false"
$env:PROXIMA_UPSTREAM_TLS_MODE = "disable"

Write-Host "Starting the actual Proxima Engine against the isolated Enterprise Lab database."
Write-Host "Listener: 127.0.0.1:6432; upstream: 127.0.0.1:55432."
Write-Host "No Agata Proxima source files will be changed. Press Ctrl+C to stop the Engine."
Push-Location $AgataRepoPath
try {
    cargo run -p proxima-engine
    if ($LASTEXITCODE -ne 0) {
        throw "Proxima Engine exited with code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
