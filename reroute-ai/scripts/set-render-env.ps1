# Print Render env vars to add in the dashboard (secrets loaded from local .env, not echoed here).
# Usage: .\reroute-ai\scripts\set-render-env.ps1
# Then open the URL printed and paste each KEY=VALUE.

param(
    [string]$ServiceId = "srv-d8a7kimgvqtc73ck2ni0"
)

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$dot = & (Join-Path $PSScriptRoot "Load-DotEnv.ps1") -Paths @(
    (Join-Path $RepoRoot ".env"),
    (Join-Path $RepoRoot "reroute-ai\.env")
)

$StateFile = Join-Path $PSScriptRoot ".deploy-state.json"
if (Test-Path $StateFile) {
    $state = Get-Content $StateFile -Raw | ConvertFrom-Json
    if ($state.serviceId) { $ServiceId = $state.serviceId }
    if ($state.vercelUrl) {
        $dot["CORS_ORIGINS"] = $state.vercelUrl
        $dot["FRONTEND_URL"] = $state.vercelUrl
    }
    if ($state.renderApiUrl) {
        $dot["GOOGLE_OAUTH_REDIRECT_URI"] = "$($state.renderApiUrl.TrimEnd('/'))/api/auth/google/callback"
    }
}

$keys = @(
    "DATABASE_URL", "DATABASE_USE_ALEMBIC_ONLY", "API_PREFIX",
    "COOKIE_SECURE", "COOKIE_SAMESITE", "EMAIL_VIA_CELERY",
    "JWT_SECRET_KEY", "CORS_ORIGINS", "FRONTEND_URL",
    "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI",
    "DUFFEL_API_KEY", "OPENAI_API_KEY", "RESEND_API_KEY",
    "AVIATION_STACK_API_KEY", "OPENROUTESERVICE_API_KEY", "OPEN_ROUTE_SERVICE_API_KEY"
)

$defaults = @{
    DATABASE_URL              = "sqlite+aiosqlite:///./data/reroute.db"
    DATABASE_USE_ALEMBIC_ONLY = "false"
    API_PREFIX                = "/api"
    COOKIE_SECURE             = "true"
    COOKIE_SAMESITE           = "none"
    EMAIL_VIA_CELERY          = "false"
}

Write-Host "Open: https://dashboard.render.com/web/$ServiceId/env"
Write-Host ""
Write-Host "Add these variables (values from your local .env):"
Write-Host ""

foreach ($key in $keys) {
    $hasLocal = [bool]($dot[$key] -or ($key -eq "OPENROUTESERVICE_API_KEY" -and $dot["OPEN_ROUTE_SERVICE_API_KEY"]))
    $val = if ($defaults[$key]) { $defaults[$key] } elseif ($hasLocal) { "<from your .env>" } else { "" }
    if ($val) {
        Write-Host "$key=$val"
    } else {
        Write-Host "# optional: $key"
    }
}

Write-Host ""
Write-Host "After saving env, redeploy:"
Write-Host "  .\reroute-ai\scripts\render.ps1 -o json deploys create $ServiceId --confirm"
