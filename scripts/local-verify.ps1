[CmdletBinding()]
param(
    [string]$Timestamp = (Get-Date -Format "yyyyMMdd-HHmmss")
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$EvidenceDir = Join-Path $RepoRoot "artifacts\evidence\infra-local\$Timestamp"
New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null

Write-Host "=== Local Infrastructure & Application Verification ===" -ForegroundColor Cyan
Write-Host "Evidence directory: $EvidenceDir" -ForegroundColor Yellow

function Get-SecretValues {
    $secrets = New-Object System.Collections.Generic.List[string]
    if (Test-Path ".env") {
        foreach ($line in Get-Content ".env") {
            $t = $line.Trim()
            if ($t -and -not $t.StartsWith("#") -and $t.Contains("=")) {
                $parts = $t.Split("=", 2)
                $k = $parts[0].Trim()
                $v = $parts[1].Trim()
                if ($v.Length -ge 6 -and ($k -match "PASSWORD|SECRET|KEY")) {
                    $secrets.Add($v)
                }
            }
        }
    }
    return $secrets
}

$SecretValues = Get-SecretValues

function Save-RedactedText([string]$FileName, [string]$Text) {
    $redacted = $Text
    foreach ($sec in $SecretValues) {
        if ($sec) {
            $redacted = $redacted.Replace($sec, "<REDACTED>")
        }
    }
    $redacted = [regex]::Replace($redacted, "(?i)(password|secret|access_key|secret_key)=([^\s&""']+)", '$1=<REDACTED>')
    $redacted = [regex]::Replace($redacted, "(?i)postgresql(\+\w+)?://([^:]+):([^@]+)@", 'postgresql$1://$2:<REDACTED>@')
    Set-Content -Path (Join-Path $EvidenceDir $FileName) -Value $redacted -Encoding UTF8
}

Write-Host "[1/6] Validating Docker Compose config (default + optional profiles)..." -ForegroundColor Cyan
docker compose --env-file .env -f infra/compose/docker-compose.dev.yml config --quiet
if ($LASTEXITCODE -ne 0) { throw "Default compose config validation failed." }
docker compose --env-file .env -f infra/compose/docker-compose.dev.yml --profile worker --profile kafka config --quiet
if ($LASTEXITCODE -ne 0) { throw "Profile compose config validation failed." }
Save-RedactedText "01-compose-validation.txt" "PASS: docker compose config --quiet (default, worker, kafka profiles)"

Write-Host "[2/6] Building jobs-worker profile image and capturing container status..." -ForegroundColor Cyan
docker compose --env-file .env -f infra/compose/docker-compose.dev.yml --profile worker build jobs-worker | Out-Null
$psOut = (docker compose --env-file .env -f infra/compose/docker-compose.dev.yml ps --format json | Out-String)
$psTable = (docker compose --env-file .env -f infra/compose/docker-compose.dev.yml ps | Out-String)
Save-RedactedText "02-docker-ps.txt" ($psTable + "`r`n" + $psOut)

Write-Host "[3/6] Capturing redacted container logs..." -ForegroundColor Cyan
$logsOut = (docker compose --env-file .env -f infra/compose/docker-compose.dev.yml logs --no-color --tail=80 2>&1 | Out-String)
Save-RedactedText "03-container-logs-redacted.txt" $logsOut

Write-Host "[4/6] Running live integration & security probes (PostgreSQL RLS, Redis, MinIO, Qdrant, E5, HTTP routes)..." -ForegroundColor Cyan
uv run --no-sync python -B scripts/verify_local_stack.py --evidence-dir "$EvidenceDir"
if ($LASTEXITCODE -ne 0) {
    throw "Live stack verification script failed."
}

Write-Host "[5/6] Running pytest contract, unit, and live E5+Qdrant integration suites..." -ForegroundColor Cyan
$envMap = @{}
foreach ($line in Get-Content ".env") {
    $t = $line.Trim()
    if ($t -and -not $t.StartsWith("#") -and $t.Contains("=")) {
        $p = $t.Split("=", 2)
        $envMap[$p[0].Trim()] = $p[1].Trim()
    }
}
$env:E5_MODEL_SNAPSHOT = $envMap["E5_MODEL_SNAPSHOT"]
$env:E5_TEST_QDRANT_URL = "http://127.0.0.1:6333"
$pytestOut = (uv run --no-sync pytest -v `
    tests/contract/test_e5_retrieval.py `
    tests/contract/test_ingestion_quarantine.py `
    tests/contract/test_deep_healthcheck.py `
    tests/integration/test_e5_embedding.py `
    apps/control-api/tests/test_health.py `
    apps/agent-runtime/tests/test_health.py `
    apps/agent-runtime/tests/test_runtime_Auth_and_runs.py `
    apps/jobs-worker/tests/test_worker.py 2>&1 | Out-String)
$pytestExit = $LASTEXITCODE
Save-RedactedText "05-pytest-results.txt" $pytestOut
if ($pytestExit -ne 0) {
    throw "pytest suite failed with exit code $pytestExit"
}

Write-Host "[6/6] Testing restart persistence and dependency-loss readiness transition..." -ForegroundColor Cyan
uv run --no-sync python -B scripts/verify_local_stack.py --evidence-dir "$EvidenceDir" --phase resilience
if ($LASTEXITCODE -ne 0) {
    throw "Resilience / restart persistence check failed."
}

Write-Host "ALL VERIFICATION CHECKS COMPLETED. Evidence saved to: $EvidenceDir" -ForegroundColor Green

