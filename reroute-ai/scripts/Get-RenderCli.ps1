# Resolve Render CLI binary (install on first use).
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$CliDir = Join-Path $RepoRoot "tools\render"
$CliVersion = "2.18.0"
$CliExe = Join-Path $CliDir "cli_v$CliVersion.exe"

if (-not (Test-Path $CliExe)) {
    Write-Host "Installing Render CLI v$CliVersion to tools/render ..."
    New-Item -ItemType Directory -Force -Path $CliDir | Out-Null
    $zip = Join-Path $CliDir "render-cli.zip"
    $url = "https://github.com/render-oss/cli/releases/download/v$CliVersion/cli_${CliVersion}_windows_amd64.zip"
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $CliDir -Force
}

if (-not (Test-Path $CliExe)) {
    throw "Render CLI not found at $CliExe"
}

return $CliExe
