param([switch]$RefreshDetails)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    if ($RefreshDetails) { uv run --locked cartalytix export --refresh-details }
    else { uv run --locked cartalytix export }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }
