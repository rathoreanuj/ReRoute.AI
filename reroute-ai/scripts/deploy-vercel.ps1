# Deploy ReRoute frontend to Vercel from the terminal.
# Prereq: npm i -g vercel  &&  vercel login
#
# Usage (from repo root):
#   .\reroute-ai\scripts\deploy-vercel.ps1 -ApiUrl https://reroute-api.onrender.com
#   .\reroute-ai\scripts\deploy-vercel.ps1   # reads ApiUrl from .deploy-state.json after deploy-render

param(
    [string]$ApiUrl,
    [string]$ProjectName = "reroute-ai",
    [switch]$Production
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$FrontendDir = Join-Path $RepoRoot "reroute-ai\frontend"
$StateFile = Join-Path $PSScriptRoot ".deploy-state.json"

if (-not $ApiUrl -and (Test-Path $StateFile)) {
    $state = Get-Content $StateFile -Raw | ConvertFrom-Json
    $ApiUrl = $state.renderApiUrl
}

if (-not $ApiUrl) {
    throw "Pass -ApiUrl https://YOUR-SERVICE.onrender.com or run deploy-render.ps1 first."
}

$ApiUrl = $ApiUrl.Trim().TrimEnd("/")
if ($ApiUrl.EndsWith("/api")) { $ApiUrl = $ApiUrl.Substring(0, $ApiUrl.Length - 4) }

if (-not (Get-Command vercel -ErrorAction SilentlyContinue)) {
    throw "Vercel CLI not found. Install: npm install -g vercel"
}

Push-Location $FrontendDir
try {
    if (-not (Test-Path ".vercel\project.json")) {
        Write-Host "Linking Vercel project '$ProjectName' ..."
        vercel link --yes --project $ProjectName 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "vercel link failed" }
    }

    Write-Host "Setting NEXT_PUBLIC_API_URL=$ApiUrl (production) ..."
    $ApiUrl | vercel env add NEXT_PUBLIC_API_URL production --force 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "vercel env add failed" }

    $deployArgs = @("deploy", "--yes")
    if ($Production) { $deployArgs += "--prod" }

    Write-Host "Running: vercel $($deployArgs -join ' ') ..."
    vercel @deployArgs 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "vercel deploy failed" }

    $inspect = vercel inspect --prod 2>&1 | Out-String
    $alias = if ($inspect -match "https://[^\s]+\.vercel\.app") { $Matches[0] } else { $null }
    if ($alias) {
        Write-Host ""
        Write-Host "Production URL: $alias"
        $stateFile = Join-Path $PSScriptRoot ".deploy-state.json"
        $state = if (Test-Path $stateFile) { Get-Content $stateFile -Raw | ConvertFrom-Json } else { @{} }
        $state | Add-Member -NotePropertyName vercelUrl -NotePropertyValue $alias -Force
        $state | ConvertTo-Json | Set-Content -Path $stateFile -Encoding UTF8
    }
    Write-Host ""
    Write-Host "Update Render env (dashboard or re-run deploy-render after editing .env):"
    Write-Host "  CORS_ORIGINS=$alias"
    Write-Host "  FRONTEND_URL=$alias"
    Write-Host "  GOOGLE_OAUTH_REDIRECT_URI remains https://<render-host>/api/auth/google/callback"
}
finally {
    Pop-Location
}
