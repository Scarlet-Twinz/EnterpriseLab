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
$resolvedAgataPath = (Resolve-Path $AgataRepoPath).Path
if (-not (Test-Path (Join-Path $resolvedAgataPath "Cargo.toml"))) {
    throw "AgataRepoPath must point to the local Agata Proxima repository root containing Cargo.toml."
}

$values = @{}
foreach ($line in Get-Content $envPath) {
    if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) { continue }
    $separator = $line.IndexOf("=")
    if ($separator -lt 1) { continue }
    $key = $line.Substring(0, $separator).Trim()
    $value = $line.Substring($separator + 1).Trim()
    $values[$key] = $value
}

$signingKey = $values["PROXIMA_TENANT_SIGNING_KEY"]
if ([string]::IsNullOrWhiteSpace($signingKey) -or $signingKey.Length -lt 32) {
    throw "PROXIMA_TENANT_SIGNING_KEY is missing or too short in Enterprise Lab .env."
}
$rolePrefix = $values["PROXIMA_TENANT_ROLE_PREFIX"]
if ([string]::IsNullOrWhiteSpace($rolePrefix) -or $rolePrefix -notmatch '^[A-Za-z0-9_]+$') {
    throw "PROXIMA_TENANT_ROLE_PREFIX must contain only ASCII letters, digits, and underscores."
}

# Runtime-only configuration. No Agata Proxima source files are edited.
$env:PROXIMA_LISTEN_ADDR = "127.0.0.1:6432"
$env:PROXIMA_UPSTREAM_ADDR = "127.0.0.1:55432"
$env:PROXIMA_TENANT_SIGNING_KEY = $signingKey
$env:PROXIMA_TENANT_ROLE_PREFIX = $rolePrefix
$env:PROXIMA_DASHBOARD_ENABLED = "false"
$env:PROXIMA_UPSTREAM_TLS_MODE = "disable"

Write-Host "Starting the actual Proxima Engine against the isolated Enterprise Lab database."
Write-Host "Tenant role prefix: $rolePrefix"
Write-Host "Listener: 127.0.0.1:6432; upstream: 127.0.0.1:55432."
Write-Host "No Agata Proxima source files will be changed. Press Ctrl+C to stop the Engine."
Push-Location $resolvedAgataPath
try {
    cargo run -p proxima-engine
    if ($LASTEXITCODE -ne 0) {
        throw "Proxima Engine exited with code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
