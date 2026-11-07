[CmdletBinding()]
param(
    [string[]]$Profiles = @(),
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

if (-not (Test-Path ".env")) {
    throw ".env file not found. Run .\scripts\local-setup.ps1 first."
}

Write-Host "=== [1/4] Checking host port availability (127.0.0.1) ===" -ForegroundColor Cyan
$requiredPorts = @(3000, 5432, 6333, 6379, 8000, 8001, 9000, 9001)
if ($Profiles -contains "kafka") {
    $requiredPorts += 9092
}

$runningComposeContainers = docker compose --env-file .env -f infra/compose/docker-compose.dev.yml ps -q 2>$null
foreach ($port in $requiredPorts) {
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listeners -and -not $runningComposeContainers) {
        $pids = ($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
        throw "Port $port is already in use by PID(s): $pids. Resolve the conflict before starting (do not kill unrelated services)."
    }
}
Write-Host "Port check OK." -ForegroundColor Green

Write-Host "=== [2/4] Validating Compose configuration ===" -ForegroundColor Cyan
uv run python -B scripts/run_migrations.py --write-bootstrap artifacts/evidence/local-bootstrap/01-migrations.sql
if ($LASTEXITCODE -ne 0) { throw "Generating transactional database bootstrap failed." }
$profileArgs = @()
foreach ($p in $Profiles) {
    if ($p) {
        $profileArgs += @("--profile", $p)
    }
}
docker compose --env-file .env -f infra/compose/docker-compose.dev.yml @profileArgs config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "docker compose config failed."
}

Write-Host "=== [3/4] Building & starting local stack ===" -ForegroundColor Cyan
# Migrate before application startup. Do not reset a legacy volume without history.
docker compose --env-file .env -f infra/compose/docker-compose.dev.yml up -d postgres
if ($LASTEXITCODE -ne 0) { throw "Starting PostgreSQL failed." }
$dbDeadline = (Get-Date).AddMinutes(2)
do {
    $dbHealth = docker inspect --format "{{.State.Health.Status}}" enterprise-postgres
    if ($dbHealth -eq "healthy") { break }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $dbDeadline)
if ($dbHealth -ne "healthy") { throw "PostgreSQL did not become ready." }
$passwordLine = Get-Content .env | Where-Object { $_ -match '^POSTGRES_PASSWORD=' } | Select-Object -First 1
if (-not $passwordLine) { throw "POSTGRES_PASSWORD is missing from .env." }
$ownerPassword = $passwordLine.Split('=', 2)[1].Trim().Trim('"').Trim("'")
$previousMigrationUrl = $env:MIGRATION_DATABASE_URL
try {
    $env:MIGRATION_DATABASE_URL = 'postgresql://platform_owner:' + [uri]::EscapeDataString($ownerPassword) + '@127.0.0.1:5432/enterprise_agent_platform'
    uv run python -B scripts/run_migrations.py
    if ($LASTEXITCODE -ne 0) { throw "Database migration failed. Keep the volume and review the error." }
} finally {
    $env:MIGRATION_DATABASE_URL = $previousMigrationUrl
    $ownerPassword = $null
}
$upArgs = @("--env-file", ".env", "-f", "infra/compose/docker-compose.dev.yml") + $profileArgs + @("up", "-d")
if (-not $NoBuild) {
    $upArgs += "--build"
}
docker compose @upArgs
if ($LASTEXITCODE -ne 0) {
    throw "docker compose up failed."
}

Write-Host "=== [4/4] Waiting for service readiness ===" -ForegroundColor Cyan
$servicesToWait = @("enterprise-postgres", "enterprise-redis", "enterprise-qdrant", "enterprise-minio", "enterprise-control-api", "enterprise-agent-runtime", "enterprise-studio-web")
$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) {
    $allHealthy = $true
    foreach ($svc in $servicesToWait) {
        $health = (docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" $svc 2>$null)
        if ($health -ne "healthy" -and $health -ne "running") {
            $allHealthy = $false
            break
        }
        $hasHealthcheck = (docker inspect --format "{{if .State.Health}}yes{{else}}no{{end}}" $svc 2>$null)
        if ($hasHealthcheck -eq "yes" -and $health -ne "healthy") {
            $allHealthy = $false
            break
        }
    }
    if ($allHealthy) {
        break
    }
    Start-Sleep -Seconds 3
}

if (-not $allHealthy) { throw "Local stack did not become ready within the deadline." }
docker compose --env-file .env -f infra/compose/docker-compose.dev.yml @profileArgs ps
Write-Host "Local stack is running." -ForegroundColor Green

