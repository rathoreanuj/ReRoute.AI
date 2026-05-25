# Full deploy: Render API then Vercel frontend (terminal workflow).
#
# One-time setup:
#   1. render login          (opens browser; or set RENDER_API_KEY)
#   2. vercel login          (already done if vercel whoami works)
#
# From repo root:
#   .\reroute-ai\scripts\deploy.ps1 -CreateRenderService
#   .\reroute-ai\scripts\deploy.ps1 -ApiUrl https://reroute-api.onrender.com -Production

param(
    [switch]$CreateRenderService,
    [string]$ApiUrl,
    [switch]$Production,
    [switch]$SkipRender,
    [switch]$SkipVercel
)

$scriptDir = $PSScriptRoot

if (-not $SkipRender) {
    $renderArgs = @()
    if ($CreateRenderService) { $renderArgs += "-CreateIfMissing" }
    & (Join-Path $scriptDir "deploy-render.ps1") @renderArgs
}

if (-not $SkipVercel) {
    $vercelArgs = @{}
    if ($ApiUrl) { $vercelArgs["ApiUrl"] = $ApiUrl }
    if ($Production) { $vercelArgs["Production"] = $true }
    & (Join-Path $scriptDir "deploy-vercel.ps1") @vercelArgs
}

Write-Host ""
Write-Host "Done. Verify:"
Write-Host "  curl https://<render-host>/api/health"
Write-Host "  Open your Vercel URL and sign in"
