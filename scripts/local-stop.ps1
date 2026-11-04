[CmdletBinding()]
param(
    [string[]]$Profiles = @("worker", "kafka")
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$profileArgs = @()
foreach ($p in $Profiles) {
    if ($p) {
        $profileArgs += @("--profile", $p)
    }
}

Write-Host "=== Stopping local stack (preserving all persistent volumes and data) ===" -ForegroundColor Cyan
# Explicitly omit -v / --volumes so postgres_data, qdrant_data, and minio_data remain intact.
docker compose --env-file .env -f infra/compose/docker-compose.dev.yml @profileArgs down --remove-orphans
if ($LASTEXITCODE -ne 0) {
    throw "docker compose down failed."
}

Write-Host "=== Persistent volumes retained ===" -ForegroundColor Green
docker volume ls --filter "name=compose_"

