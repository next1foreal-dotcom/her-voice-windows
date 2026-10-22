#requires -Version 5.1
<#
Install the inspected Windows speech profile into .venv-speech.
This downloads Python packages only. It never calls DeepSeek or loads model weights.
Run from PowerShell: .\scripts\install-windows.ps1
Use -PythonExe 'C:\Path With Spaces\Python311\python.exe' if py.exe is unavailable.
#>
[CmdletBinding()]
param(
    [string]$PythonExe = '',
    [switch]$SkipGpuCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvRoot = Join-Path $ProjectRoot '.venv-speech'
$VenvPython = Join-Path $VenvRoot 'Scripts\python.exe'
$Constraints = Join-Path $ProjectRoot 'config\dependencies-windows.txt'
$RuntimeDir = Join-Path $ProjectRoot '.runtime'
$EngineSha = '0ed1bb95bd335aa89bb7c172b6a347b6dae596f8'
$EngineRequirement = "speech-to-speech[faster-whisper] @ https://github.com/huggingface/speech-to-speech/archive/$EngineSha.zip"

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE ($Executable). The existing environment was kept for diagnosis."
    }
}

function Assert-TargetPython {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$Prefix = @()
    )
    # Python uses single quotes so Windows PowerShell 5.1's native argument
    # marshalling does not strip the string delimiters in this -c payload.
    $ProbeCode = "import json,platform,struct,sys; print(json.dumps({'system':platform.system(),'machine':platform.machine(),'bits':struct.calcsize('P')*8,'implementation':platform.python_implementation(),'version':list(sys.version_info[:3])}))"
    $ProbeLines = @(Invoke-NativeChecked -Executable $Executable -Arguments ($Prefix + @('-c', $ProbeCode)))
    $Info = ($ProbeLines -join "`n") | ConvertFrom-Json
    if ($Info.system -ne 'Windows' -or $Info.bits -ne 64 -or
        $Info.machine -notin @('AMD64', 'x86_64') -or
        $Info.implementation -ne 'CPython' -or
        $Info.version[0] -ne 3 -or $Info.version[1] -ne 11) {
        throw 'Use native Windows x64 CPython 3.11. This installer does not target WSL, ARM64, PyPy, or a different Python minor version.'
    }
    Write-Host ("Python {0} ({1}, {2}-bit)" -f ($Info.version -join '.'), $Info.machine, $Info.bits)
}

try {
    if ($env:OS -ne 'Windows_NT') {
        throw 'Run this installer in Windows PowerShell or PowerShell on native Windows.'
    }
    if (-not (Test-Path -LiteralPath $Constraints -PathType Leaf)) {
        throw 'config/dependencies-windows.txt is missing. Keep the project directory intact.'
    }

    if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
        Assert-TargetPython -Executable $VenvPython
        Write-Host 'Reusing the existing .venv-speech.'
    }
    else {
        if (Test-Path -LiteralPath $VenvRoot) {
            throw '.venv-speech exists but has no Scripts/python.exe. Move the incomplete directory aside, then rerun; the installer will not delete it.'
        }
        $LauncherPrefix = @()
        if ([string]::IsNullOrWhiteSpace($PythonExe)) {
            $PythonCommand = (Get-Command 'py.exe' -ErrorAction Stop).Source
            $LauncherPrefix = @('-3.11')
        }
        else {
            $PythonCommand = (Get-Command $PythonExe -ErrorAction Stop).Source
        }
        Assert-TargetPython -Executable $PythonCommand -Prefix $LauncherPrefix
        Invoke-NativeChecked -Executable $PythonCommand -Arguments ($LauncherPrefix + @('-m', 'venv', $VenvRoot))
        Assert-TargetPython -Executable $VenvPython
    }

    # The venv isolates project packages. No global Python packages are removed.
    Invoke-NativeChecked -Executable $VenvPython -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel')

    # Install CUDA wheels first, using PyTorch's official CUDA 12.8 index.
    # The second resolver pass keeps these exact installed versions via constraints.
    Invoke-NativeChecked -Executable $VenvPython -Arguments @(
        '-m', 'pip', 'install', '--constraint', $Constraints,
        '--index-url', 'https://download.pytorch.org/whl/cu128',
        'torch==2.11.0+cu128', 'torchaudio==2.11.0+cu128'
    )

    # Install the inspected commit and only the optional ASR backend we use.
    # Keep the dependency resolver enabled; upstream has no supported [all] profile.
    Invoke-NativeChecked -Executable $VenvPython -Arguments @(
        '-m', 'pip', 'install', '--constraint', $Constraints,
        $EngineRequirement, 'aiohttp==3.14.5', 'soxr==1.0.0'
    )
    Invoke-NativeChecked -Executable $VenvPython -Arguments @(
        '-m', 'pip', 'install', '--constraint', $Constraints, '--editable', $ProjectRoot
    )

    # One disclosed source change pins Silero's torch.hub reference. The helper
    # verifies this isolated venv, exact upstream provenance and whole-file hashes.
    # Unknown source is refused; rerunning an already applied patch is harmless.
    Invoke-NativeChecked -Executable $VenvPython -Arguments @((Join-Path $PSScriptRoot 'pin-silero.py'))
    Invoke-NativeChecked -Executable $VenvPython -Arguments @((Join-Path $PSScriptRoot 'trim-startup.py'))
    Invoke-NativeChecked -Executable $VenvPython -Arguments @('-m', 'pip', 'check')

    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    $FreezeLines = @(Invoke-NativeChecked -Executable $VenvPython -Arguments @('-m', 'pip', 'freeze', '--all'))
    $FreezePath = Join-Path $RuntimeDir 'pip-freeze-windows.txt'
    [System.IO.File]::WriteAllLines($FreezePath, [string[]]$FreezeLines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Resolved environment recorded: $FreezePath"

    $DoctorArgs = @((Join-Path $PSScriptRoot 'doctor.py'))
    if ($SkipGpuCheck) { $DoctorArgs += '--skip-gpu' }
    Invoke-NativeChecked -Executable $VenvPython -Arguments $DoctorArgs

    Write-Host ''
    Write-Host 'Package installation and selected local checks completed.'
    if ($SkipGpuCheck) { Write-Host 'CUDA validation was explicitly skipped.' }
    Write-Host 'Next, prepare Drama: .\scripts\install-drama.ps1 (builds CUDA and downloads the 22.91 GiB GGUF).'
    Write-Host 'To prefetch ASR/Smart Turn: .\.venv-speech\Scripts\python.exe .\scripts\download-models.py --backend drama3 --download'
    Write-Host 'For the Qwen backup, use --backend qwen3 --download; the UI selects one TTS at a time.'
    Write-Host 'Import a reference WAV and transcript in the UI before starting live inference.'
    Write-Host 'No DeepSeek request was made. Configure DEEPSEEK_API_KEY locally before a conversation.'
    Write-Host ("Python executable: {0}" -f $VenvPython)
    exit 0
}
catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    Write-Host 'Installation did not pass every required check. No existing environment was deleted.'
    exit 1
}
