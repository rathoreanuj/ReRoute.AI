# Wrapper: run Render CLI from repo (installs binary on first use).
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
$cli = . (Join-Path $PSScriptRoot "Get-RenderCli.ps1")
& $cli @Args
exit $LASTEXITCODE
