# Parse KEY=VALUE lines from .env files (no export to process-wide env by default).
param(
    [Parameter(Mandatory = $true)]
    [string[]]$Paths
)

function Import-DotEnvFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return @{} }
    $map = @{}
    Get-Content $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) { return }
        $key = $line.Substring(0, $eq).Trim()
        $val = $line.Substring($eq + 1).Trim()
        if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        $map[$key] = $val
    }
    return $map
}

$merged = @{}
foreach ($p in $Paths) {
    $fileMap = Import-DotEnvFile -Path $p
    foreach ($k in $fileMap.Keys) { $merged[$k] = $fileMap[$k] }
}
return $merged
