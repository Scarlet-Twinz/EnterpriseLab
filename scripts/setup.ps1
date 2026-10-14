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
$tenantAPassword = New-Secret 32
$tenantBPassword = New-Secret 32
$tenantCPassword = New-Secret 32
$keyA = "elab_" + (New-Secret 24)
$keyB = "elab_" + (New-Secret 24)
$keyC = "elab_" + (New-Secret 24)

$apiKeysObject = [ordered]@{}
$apiKeysObject[$keyA] = @{ tenant_id = "northstar"; role = "admin" }
$apiKeysObject[$keyB] = @{ tenant_id = "bluehaven"; role = "operator" }
$apiKeysObject[$keyC] = @{ tenant_id = "cedarstone"; role = "viewer" }
$apiKeys = $apiKeysObject | ConvertTo-Json -Compress -Depth 4
$tenantPasswordsObject = [ordered]@{ northstar = $tenantAPassword; bluehaven = $tenantBPassword; cedarstone = $tenantCPassword }
$tenantPasswords = $tenantPasswordsObject | ConvertTo-Json -Compress

$content = @"
LAB_API_KEYS=$apiKeys
PROXIMA_TENANT_SIGNING_KEY=$signingKey
PROXIMA_TENANT_ROLE_PREFIX=labrole_
PROXIMA_TOKEN_TTL_SECONDS=300
LAB_POSTGRES_ADMIN_PASSWORD=$adminPassword
LAB_TENANT_A_DB_PASSWORD=$tenantAPassword
LAB_TENANT_B_DB_PASSWORD=$tenantBPassword
LAB_TENANT_C_DB_PASSWORD=$tenantCPassword
LAB_TENANT_DB_PASSWORDS=$tenantPasswords
PROXIMA_DATABASE_URL=postgresql://proxima@127.0.0.1:6432/enterprise_lab?sslmode=disable
LAB_ADMIN_DATABASE_URL=postgresql://lab_admin:$adminPassword@127.0.0.1:55432/enterprise_lab
LAB_BASE_URL=http://127.0.0.1:8088
LAB_API_KEY_A=$keyA
LAB_API_KEY_B=$keyB
LAB_API_KEY_C=$keyC
AGATA_CONTROL_PLANE_URL=http://127.0.0.1:8080
AGATA_LAB_EMAIL=
AGATA_LAB_PASSWORD=
AGATA_PROXIMA_COMMIT=
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
Write-Host "Next, in a separate PowerShell terminal, run:"
Write-Host "./scripts/start-proxima.ps1 -AgataRepoPath <path-to-your-local-Agata-Proxima-repository>"
Write-Host "That launches the actual Engine with runtime-only settings; it does not edit Agata Proxima files."
Write-Host "Then start the Enterprise Lab API in another terminal using the README instructions."
Write-Host "Never commit .env or paste its secrets into chat."
