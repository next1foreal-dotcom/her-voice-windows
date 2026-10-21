#requires -Version 5.1
[CmdletBinding()]
param([switch]$NoBrowser, [string]$PythonExe = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SpeechPython = Join-Path $ProjectRoot '.venv-speech\Scripts\python.exe'
$DemoPython = Join-Path $ProjectRoot '.venv-demo\Scripts\python.exe'
function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE." }
}
if (Test-Path -LiteralPath $SpeechPython) { $VoicePython = $SpeechPython }
else {
    if (-not (Test-Path -LiteralPath $DemoPython)) {
        if ($PythonExe) {
            Invoke-Checked -Executable $PythonExe -Arguments @('-m', 'venv', (Join-Path $ProjectRoot '.venv-demo'))
        }
        else {
            Invoke-Checked -Executable 'py.exe' -Arguments @('-3.11', '-m', 'venv', (Join-Path $ProjectRoot '.venv-demo'))
        }
        Invoke-Checked -Executable $DemoPython -Arguments @('-m', 'pip', 'install', '--editable', $ProjectRoot)
    }
    $VoicePython = $DemoPython
}
Push-Location $ProjectRoot
try {
    $VoiceArgs = @('-m', 'her_voice', '--demo')
    if (-not $NoBrowser) { $VoiceArgs += '--open' }
    & $VoicePython @VoiceArgs
    $VoiceExitCode = $LASTEXITCODE
}
finally { Pop-Location }
exit $VoiceExitCode
