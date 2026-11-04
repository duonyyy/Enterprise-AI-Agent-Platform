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

docker compose --env-file .env -f infra/compose/docker-compose.dev.yml @profileArgs ps
Write-Host "Local stack is running." -ForegroundColor Green

