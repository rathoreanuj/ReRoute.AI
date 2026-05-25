# Deploy / update ReRoute API on Render from the terminal.
# Prereq: render login  OR  $env:RENDER_API_KEY = rnd_...
#
# Usage (from repo root):
#   .\reroute-ai\scripts\deploy-render.ps1
#   .\reroute-ai\scripts\deploy-render.ps1 -ServiceName reroute-api -CreateIfMissing

param(
    [string]$ServiceName = "reroute-api",
    [string]$RepoUrl = "https://github.com/rathoreanuj/ReRoute.AI.git",
    [string]$Branch = "main",
    [switch]$CreateIfMissing
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RenderCli = . (Join-Path $PSScriptRoot "Get-RenderCli.ps1")
. (Join-Path $PSScriptRoot "Load-DotEnv.ps1") | Out-Null

$envFiles = @(
    (Join-Path $RepoRoot ".env"),
    (Join-Path $RepoRoot "reroute-ai\.env")
)
$dot = & (Join-Path $PSScriptRoot "Load-DotEnv.ps1") -Paths $envFiles

$StateFile = Join-Path $PSScriptRoot ".deploy-state.json"
if (Test-Path $StateFile) {
    $state = Get-Content $StateFile -Raw | ConvertFrom-Json
    if ($state.vercelUrl) {
        $dot["CORS_ORIGINS"] = $state.vercelUrl
        $dot["FRONTEND_URL"] = $state.vercelUrl
    }
    if ($state.renderApiUrl) {
        $dot["GOOGLE_OAUTH_REDIRECT_URI"] = "$($state.renderApiUrl.TrimEnd('/'))/api/auth/google/callback"
    }
}

if (-not $env:RENDER_API_KEY) {
    Write-Host "Tip: run once: render login   (or set `$env:RENDER_API_KEY)"
}

# Production overrides for Vercel + Render (FRONTEND_URL / CORS updated after Vercel deploy)
$renderEnv = [ordered]@{
    DATABASE_URL                 = "sqlite+aiosqlite:///./data/reroute.db"
    DATABASE_USE_ALEMBIC_ONLY    = "false"
    API_PREFIX                   = "/api"
    COOKIE_SECURE                = "true"
    COOKIE_SAMESITE              = "none"
    EMAIL_VIA_CELERY             = "false"
}
foreach ($key in @(
    "JWT_SECRET_KEY", "DUFFEL_API_KEY", "OPENAI_API_KEY", "RESEND_API_KEY",
    "AVIATION_STACK_API_KEY", "OPENROUTESERVICE_API_KEY", "OPEN_ROUTE_SERVICE_API_KEY",
    "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
    "CORS_ORIGINS", "FRONTEND_URL", "GOOGLE_OAUTH_REDIRECT_URI"
)) {
    if ($dot.ContainsKey($key) -and $dot[$key]) { $renderEnv[$key] = $dot[$key] }
}
if (-not $renderEnv["OPENROUTESERVICE_API_KEY"] -and $dot["OPEN_ROUTE_SERVICE_API_KEY"]) {
    $renderEnv["OPENROUTESERVICE_API_KEY"] = $dot["OPEN_ROUTE_SERVICE_API_KEY"]
}
if ($renderEnv["JWT_SECRET_KEY"] -eq "change-me-use-long-random-string-in-production") {
    Write-Warning "JWT_SECRET_KEY is still the dev default; set a strong value in .env before production."
}

$envVarArgs = @()
foreach ($kv in $renderEnv.GetEnumerator()) {
    if ($kv.Value) { $envVarArgs += "--env-var"; $envVarArgs += "$($kv.Key)=$($kv.Value)" }
}

$servicesRaw = & $RenderCli -o json services 2>&1
if ($LASTEXITCODE -ne 0) { throw "render services failed. Run: render login" }
$services = $servicesRaw | ConvertFrom-Json
$svc = $services | Where-Object { $_.service.name -eq $ServiceName } | Select-Object -First 1
$serviceId = $null
if ($svc) { $serviceId = $svc.service.id }

if (-not $serviceId -and $CreateIfMissing) {
    Write-Host "Creating Render web service '$ServiceName' ..."
    $createArgs = @(
        "-o", "json", "services", "create",
        "--name", $ServiceName,
        "--type", "web_service",
        "--repo", $RepoUrl,
        "--branch", $Branch,
        "--root-directory", "reroute-ai/backend",
        "--runtime", "python",
        "--plan", "free",
        "--region", "oregon",
        "--build-command", "pip install .",
        "--start-command", 'mkdir -p data && uvicorn main:app --host 0.0.0.0 --port $PORT',
        "--health-check-path", "/api/health",
        "--auto-deploy", "true"
    ) + $envVarArgs
    $createdRaw = & $RenderCli @createArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host $createdRaw
        throw "render services create failed"
    }
    $created = ($createdRaw | Out-String) | ConvertFrom-Json
    $serviceId = $created.id
    if (-not $serviceId -and $created.service) { $serviceId = $created.service.id }
    Write-Host "Created service id: $serviceId"
}

if (-not $serviceId) {
    throw "Service '$ServiceName' not found. Re-run with -CreateIfMissing or create it in the Render dashboard."
}

Write-Host ""
Write-Host "Set environment variables in Render (CLI cannot patch env on existing services):"
Write-Host "  https://dashboard.render.com/web/$serviceId/env"
Write-Host "  Copy keys from reroute-ai/docs/DEPLOY_VERCEL_RENDER.md or your .env files."
Write-Host ""

Write-Host "Triggering deploy for $ServiceName ($serviceId) ..."
& $RenderCli -o json deploys create $serviceId --confirm 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { throw "render deploys create failed" }

$servicesRaw2 = & $RenderCli -o json services 2>&1
$all = $servicesRaw2 | ConvertFrom-Json
$match = $all | Where-Object { $_.service.id -eq $serviceId } | Select-Object -First 1
$publicUrl = $match.service.serviceDetails.url
if (-not $publicUrl) {
    $publicUrl = "https://$ServiceName.onrender.com"
    Write-Host "Assuming default URL: $publicUrl (confirm in Render dashboard)"
} else {
    Write-Host "API URL: $publicUrl"
}

# Save for Vercel script
$deployDir = Join-Path $RepoRoot "reroute-ai\scripts"
$stateFile = Join-Path $deployDir ".deploy-state.json"
@{ renderApiUrl = $publicUrl; serviceId = $serviceId; updatedAt = (Get-Date).ToString("o") } |
    ConvertTo-Json | Set-Content -Path $stateFile -Encoding UTF8

Write-Host ""
Write-Host "Next: .\reroute-ai\scripts\deploy-vercel.ps1 -ApiUrl $publicUrl"
