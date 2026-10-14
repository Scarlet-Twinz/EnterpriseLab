$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"

if (Test-Path $envPath) {
    throw ".env already exists. Refusing to overwrite secrets. Edit it manually if you need to reconfigure."
}

function New-Secret([int]$Bytes = 32) {
    $buffer = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
    return [BitConverter]::ToString($buffer).Replace("-", "").ToLowerInvariant()
}

$signingKey = New-Secret 48
$adminPassword = New-Secret 32
$gatewayPassword = New-Secret 32
$keyA = "elab_" + (New-Secret 24)
$keyB = "elab_" + (New-Secret 24)
$keyC = "elab_" + (New-Secret 24)

$apiKeysObject = [ordered]@{}
$apiKeysObject[$keyA] = @{ tenant_id = "tenant_a"; role = "admin" }
$apiKeysObject[$keyB] = @{ tenant_id = "tenant_b"; role = "admin" }
$apiKeysObject[$keyC] = @{ tenant_id = "tenant_c"; role = "viewer" }
$apiKeys = $apiKeysObject | ConvertTo-Json -Compress -Depth 4

$content = @"
LAB_API_KEYS=$apiKeys
PROXIMA_SIGNING_KEY=$signingKey
PROXIMA_TOKEN_TTL_SECONDS=300
LAB_POSTGRES_ADMIN_PASSWORD=$adminPassword
PROXIMA_GATEWAY_PASSWORD=$gatewayPassword
PROXIMA_DATABASE_URL=postgresql://proxima:replace-me@127.0.0.1:6432/enterprise_lab
LAB_ADMIN_DATABASE_URL=postgresql://lab_admin:$adminPassword@127.0.0.1:55432/enterprise_lab
LAB_BASE_URL=http://127.0.0.1:8088
LAB_API_KEY_A=$keyA
LAB_API_KEY_B=$keyB
"@

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($envPath, $content, $utf8NoBom)

Push-Location $repoRoot
try {
    docker compose up -d enterprise-db
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed to start the Enterprise Lab database." }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Enterprise Lab local secrets created in .env (ignored by Git)."
Write-Host "The isolated PostgreSQL database is starting on 127.0.0.1:55432."
Write-Host "Next: configure the REAL Proxima Engine to use database enterprise_lab,"
Write-Host "upstream role proxima_gateway, the PROXIMA_GATEWAY_PASSWORD from .env,"
Write-Host "tenant roles tenant_a/tenant_b/tenant_c, and the same PROXIMA_SIGNING_KEY."
Write-Host "Then set the actual Engine listener password in PROXIMA_DATABASE_URL and run the API."
Write-Host "Never commit .env or paste its secrets into chat."
