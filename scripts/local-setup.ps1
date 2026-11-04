[CmdletBinding()]
param(
    [switch]$SkipDependencySync
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

Write-Host "=== [1/5] Checking local prerequisites ===" -ForegroundColor Cyan
foreach ($cmd in @("docker", "uv", "pnpm", "node")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "Required command '$cmd' was not found in PATH."
    }
}
docker info --format "Docker Engine: {{.ServerVersion}}" | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Docker Engine is not reachable. Ensure Docker Desktop is running."
}

function New-RandomSecret([int]$ByteLength = 24) {
    $bytes = New-Object byte[] $ByteLength
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
}

function Get-EnvMap([string]$Path) {
    $map = @{}
    if (Test-Path $Path) {
        foreach ($line in Get-Content $Path) {
            $trimmed = $line.Trim()
            if ($trimmed -and -not $trimmed.StartsWith("#") -and $trimmed.Contains("=")) {
                $parts = $trimmed.Split("=", 2)
                $map[$parts[0].Trim()] = $parts[1].Trim()
            }
        }
    }
    return $map
}

Write-Host "=== [2/5] Checking .env configuration (preserving existing credentials) ===" -ForegroundColor Cyan
$envPath = Join-Path $RepoRoot ".env"
$envExamplePath = Join-Path $RepoRoot ".env.example"

if (-not (Test-Path $envPath)) {
    Copy-Item $envExamplePath $envPath
    $content = Get-Content $envPath -Raw
    $content = $content.Replace("replace-with-development-minio-access-key", "platform_s3_app")
    $content = $content.Replace("replace-with-development-minio-secret-key", (New-RandomSecret 20))
    $content = $content.Replace("replace-with-development-minio-root-user", "platform_minio_root")
    $content = $content.Replace("replace-with-development-minio-root-password", (New-RandomSecret 20))
    while ($content.Contains("replace-with-a-secret-from-your-secret-manager")) {
        $idx = $content.IndexOf("replace-with-a-secret-from-your-secret-manager")
        $content = $content.Substring(0, $idx) + (New-RandomSecret 20) + $content.Substring($idx + "replace-with-a-secret-from-your-secret-manager".Length)
    }
    while ($content.Contains("replace-with-a-secure-random-secret-key-min-32-chars")) {
        $idx = $content.IndexOf("replace-with-a-secure-random-secret-key-min-32-chars")
        $content = $content.Substring(0, $idx) + (New-RandomSecret 32) + $content.Substring($idx + "replace-with-a-secure-random-secret-key-min-32-chars".Length)
    }
    Set-Content -Path $envPath -Value $content -Encoding UTF8
    Write-Host "Created new .env from .env.example with generated local secrets." -ForegroundColor Green
} else {
    Write-Host "Existing .env detected; preserving all existing keys without rotation." -ForegroundColor Yellow
}

$envMap = Get-EnvMap $envPath
$e5Rev = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
$candidateSnapshots = @(
    (Join-Path $RepoRoot "models\hub\models--intfloat--multilingual-e5-small\snapshots\$e5Rev"),
    (Join-Path (Split-Path $RepoRoot -Parent) "EnterpriseAgentPlatform\models\hub\models--intfloat--multilingual-e5-small\snapshots\$e5Rev")
)
$resolvedSnapshot = $null
if ($envMap.ContainsKey("E5_MODEL_SNAPSHOT") -and $envMap["E5_MODEL_SNAPSHOT"]) {
    $existingSnap = $envMap["E5_MODEL_SNAPSHOT"]
    if (Test-Path (Join-Path $existingSnap "model.safetensors")) {
        $resolvedSnapshot = (Resolve-Path $existingSnap).Path
    }
}
if (-not $resolvedSnapshot) {
    foreach ($cand in $candidateSnapshots) {
        if (Test-Path (Join-Path $cand "model.safetensors")) {
            $resolvedSnapshot = (Resolve-Path $cand).Path
            break
        }
    }
}
if (-not $resolvedSnapshot) {
    $resolvedSnapshot = Join-Path $RepoRoot "models\hub\models--intfloat--multilingual-e5-small\snapshots\$e5Rev"
}

$missingLines = New-Object System.Collections.Generic.List[string]
$defaultsToAppend = [ordered]@{
    "POSTGRES_PASSWORD"           = (New-RandomSecret 20)
    "PLATFORM_APP_DB_PASSWORD"    = (New-RandomSecret 20)
    "RUNTIME_JWT_SECRET"          = (New-RandomSecret 32)
    "RUNTIME_JWT_ISSUER"          = "http://localhost:8080/realms/enterprise"
    "RUNTIME_JWT_AUDIENCE"        = "agent-runtime"
    "MINIO_ROOT_USER"             = "platform_minio_root"
    "MINIO_ROOT_PASSWORD"         = (New-RandomSecret 20)
    "E5_MODEL_SNAPSHOT"           = ($resolvedSnapshot -replace "\\", "/")
    "NEXT_PUBLIC_CONTROL_API_URL" = "http://127.0.0.1:8000"
    "NEXT_PUBLIC_RUNTIME_API_URL" = "http://127.0.0.1:8001"
    "CONTROL_API_INTERNAL_URL"    = "http://control-api:8000"
    "RUNTIME_API_URL"             = "http://agent-runtime:8001"
    "WORKER_TENANT_ID"            = ""
    "WORKER_WORKSPACE_ID"         = ""
}

foreach ($key in $defaultsToAppend.Keys) {
    if (-not $envMap.ContainsKey($key)) {
        $missingLines.Add("$key=$($defaultsToAppend[$key])")
    }
}

if ($missingLines.Count -gt 0) {
    Add-Content -Path $envPath -Value ("`r`n# Added by scripts/local-setup.ps1 (missing keys only)`r`n" + ($missingLines -join "`r`n"))
    Write-Host ("Appended {0} missing configuration key(s) to .env: {1}" -f $missingLines.Count, (($missingLines | ForEach-Object { $_.Split("=")[0] }) -join ", ")) -ForegroundColor Green
} else {
    Write-Host "All required Compose environment keys are present in .env." -ForegroundColor Green
}

$envMap = Get-EnvMap $envPath
if ($envMap["S3_ACCESS_KEY"] -eq $envMap["MINIO_ROOT_USER"]) {
    throw "Security policy violation: S3_ACCESS_KEY must not equal MINIO_ROOT_USER."
}

if (-not $SkipDependencySync) {
    Write-Host "=== [3/5] Syncing Python (uv + retrieval extra) and Node (pnpm) workspaces ===" -ForegroundColor Cyan
    uv sync --all-packages --locked --group dev --extra retrieval
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed." }

    pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw "pnpm install failed." }
} else {
    Write-Host "=== [3/5] Skipping dependency sync (-SkipDependencySync) ===" -ForegroundColor Yellow
}

Write-Host "=== [4/5] Verifying offline E5 model snapshot ($e5Rev) ===" -ForegroundColor Cyan
$snapshotPath = $envMap["E5_MODEL_SNAPSHOT"]
if (-not (Test-Path (Join-Path $snapshotPath "model.safetensors"))) {
    Write-Host "Provisioning intfloat/multilingual-e5-small @ $e5Rev into $snapshotPath ..." -ForegroundColor Yellow
    uv run --no-sync python -B -c "from pathlib import Path; from huggingface_hub import snapshot_download; target = Path(r'$snapshotPath'); target.parent.mkdir(parents=True, exist_ok=True); snapshot_download(repo_id='intfloat/multilingual-e5-small', revision='$e5Rev', local_dir=str(target))"
    if ($LASTEXITCODE -ne 0) { throw "Failed to provision E5 model snapshot." }
}
$leafName = Split-Path $snapshotPath -Leaf
if ($leafName -ne $e5Rev) {
    throw "E5_MODEL_SNAPSHOT final folder name must be '$e5Rev', got '$leafName'."
}
Write-Host "Verified E5 model snapshot at: $snapshotPath" -ForegroundColor Green

Write-Host "=== [5/5] Validating Docker Compose configuration (quiet mode, no secret output) ===" -ForegroundColor Cyan
docker compose --env-file .env -f infra/compose/docker-compose.dev.yml config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "docker compose config validation failed."
}
Write-Host "PASS: docker compose config --quiet succeeded." -ForegroundColor Green

