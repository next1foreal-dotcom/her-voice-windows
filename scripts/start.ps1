#requires -Version 5.1
[CmdletBinding()]
param([switch]$NoBrowser)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VoicePython = Join-Path $ProjectRoot '.venv-speech\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VoicePython)) {
    throw 'Run scripts/install-windows.ps1 first. The .venv-speech environment is missing.'
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot '.env.example') -Destination (Join-Path $ProjectRoot '.env')
    Write-Host 'Created .env. Enter DEEPSEEK_API_KEY there, then restart this launcher for live mode.'
}
Push-Location $ProjectRoot
try {
    $VoiceArgs = @('-m', 'her_voice')
    if (-not $NoBrowser) { $VoiceArgs += '--open' }
    & $VoicePython @VoiceArgs
    $VoiceExitCode = $LASTEXITCODE
}
finally { Pop-Location }
exit $VoiceExitCode
