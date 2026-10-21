#requires -Version 5.1
<#
Build the pinned audio.cpp CUDA server for RTX 4080 SUPER (sm_89), then
download and SHA-256-check the 22.91 GiB Drama 3 Q4/Q8 package.
Requires Git, VS 2022 C++ build tools + Windows SDK + CMake/Ninja, CUDA Toolkit,
and the .venv-speech environment created by install-windows.ps1.
-DryRun prints the plan. -BuildOnly skips model download. -ModelFile reuses a
byte-identical existing GGUF without copying it. No inference/API calls occur.
#>
[CmdletBinding()]
param(
    [string]$PythonExe = '',
    [string]$Workspace = '',
    [string]$ModelFile = '',
    [ValidateRange(1, 65535)][int]$Port = 8792,
    [ValidateRange(1, 32)][int]$Jobs = 4,
    [switch]$BuildOnly,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Workspace)) { $Workspace = $ProjectRoot }
$Workspace = [IO.Path]::GetFullPath($Workspace)
$RuntimeRevision = '0bf4d155f2af5f85853415170622dc145e3d62c3'
$RepositoryUrl = 'https://github.com/0xShug0/audio.cpp.git'
$RuntimeDir = Join-Path $Workspace '.runtime\drama3'
$SourceDir = Join-Path $RuntimeDir ('audio.cpp-' + $RuntimeRevision.Substring(0, 12))
$OwnerPath = Join-Path $RuntimeDir 'source-owner.json'
$ServerExe = Join-Path $SourceDir 'build\windows-cuda-release\bin\audiocpp_server.exe'
$PrepareScript = Join-Path $PSScriptRoot 'prepare-drama.py'

function Invoke-NativeChecked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE ($Executable). Existing files were retained."
    }
}

function Write-JsonUtf8 {
    param([string]$Path, [object]$Value)
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
}

try {
    if ($BuildOnly -and -not [string]::IsNullOrWhiteSpace($ModelFile)) {
        throw 'Choose -BuildOnly or -ModelFile, not both.'
    }
    if ($DryRun) {
        [ordered]@{
            mode = 'dry-run'; network_requests = $false; files_written = $false
            runtime_repository = $RepositoryUrl; runtime_revision = $RuntimeRevision
            source_dir = $SourceDir; executable = $ServerExe
            model_size_gib = 22.91; download_model = (-not $BuildOnly -and [string]::IsNullOrWhiteSpace($ModelFile))
            model_file = $ModelFile; port = $Port; cuda_architectures = '89'
            build_arguments = @('-Preset', 'windows-cuda-release', '-ModelSet', 'custom', '-Models', 'fish_drama3', '-Target', 'audiocpp_server', '-Jobs', $Jobs, '-CudaArchitectures', '89', '-CpuArch', 'avx2', '-DeploymentBuild')
            manifest_path = (Join-Path $RuntimeDir 'runtime.json')
        } | ConvertTo-Json -Depth 8
        exit 0
    }
    if ($env:OS -ne 'Windows_NT') { throw 'Run this installer on native Windows x64.' }
    if (-not [Environment]::Is64BitProcess) { throw 'Use 64-bit PowerShell.' }
    $Git = (Get-Command 'git.exe' -ErrorAction Stop).Source
    $ShellExe = (Get-Process -Id $PID).Path
    if ([string]::IsNullOrWhiteSpace($PythonExe)) {
        $PythonExe = Join-Path $ProjectRoot '.venv-speech\Scripts\python.exe'
    }
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw 'Run scripts/install-windows.ps1 first, or provide -PythonExe for its speech environment.'
    }
    if (-not [string]::IsNullOrWhiteSpace($ModelFile)) {
        $ModelFile = [IO.Path]::GetFullPath($ModelFile)
        if (-not (Test-Path -LiteralPath $ModelFile -PathType Leaf)) { throw '-ModelFile does not exist.' }
    }

    # Never adopt a source directory that this installer did not create.
    if (Test-Path -LiteralPath $SourceDir) {
        if (-not (Test-Path -LiteralPath $OwnerPath -PathType Leaf)) {
            throw 'The source directory already exists without this installer ownership record. Move it aside or choose a different -Workspace.'
        }
        $Owner = Get-Content -LiteralPath $OwnerPath -Raw | ConvertFrom-Json
        if ($Owner.owner -ne 'her-voice-drama-setup-v1' -or $Owner.source_dir -ne $SourceDir -or $Owner.revision -ne $RuntimeRevision) {
            throw 'The existing source ownership record differs. No directory was changed.'
        }
    }
    else {
        New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
        New-Item -ItemType Directory -Path $SourceDir | Out-Null
        Write-JsonUtf8 -Path $OwnerPath -Value @{owner='her-voice-drama-setup-v1'; source_dir=$SourceDir; revision=$RuntimeRevision}
    }

    if (-not (Test-Path -LiteralPath (Join-Path $SourceDir '.git'))) {
        Invoke-NativeChecked $Git @('init', $SourceDir)
    }
    $Remote = & $Git -C $SourceDir config --get remote.origin.url
    if ($LASTEXITCODE -ne 0) {
        Invoke-NativeChecked $Git @('-C', $SourceDir, 'remote', 'add', 'origin', $RepositoryUrl)
    }
    elseif (($Remote -join '').Trim() -ne $RepositoryUrl) {
        throw 'The owned checkout has a different origin. No remote was changed.'
    }
    $Head = & $Git -C $SourceDir rev-parse --verify --quiet HEAD
    if ($LASTEXITCODE -ne 0) {
        Invoke-NativeChecked $Git @('-C', $SourceDir, 'fetch', '--depth', '1', '--no-tags', 'origin', $RuntimeRevision)
        Invoke-NativeChecked $Git @('-C', $SourceDir, 'checkout', '--detach', $RuntimeRevision)
    }
    elseif (($Head -join '').Trim() -ne $RuntimeRevision) {
        throw 'The owned checkout was moved to another commit. Preserve your changes separately; this installer will not reset it.'
    }
    $Changes = @(Invoke-NativeChecked $Git @('-C', $SourceDir, 'status', '--porcelain', '--untracked-files=no'))
    if ($Changes.Count -gt 0) {
        throw 'Tracked source changes were found. The installer will not overwrite them.'
    }
    Invoke-NativeChecked $Git @('-C', $SourceDir, '-c', 'submodule.external/audio.cpp-server-frontends.url=https://github.com/0xShug0/audio.cpp-server-frontends.git', 'submodule', 'update', '--init', '--recursive')

    # Use a child PowerShell: upstream uses exit in several branches, and its
    # temporary MSVC/CUDA environment should not alter this launcher session.
    $BuildArgs = @('-NoLogo', '-NoProfile', '-File', (Join-Path $SourceDir 'scripts\build_windows.ps1'),
        '-Preset', 'windows-cuda-release', '-ModelSet', 'custom', '-Models', 'fish_drama3',
        '-Target', 'audiocpp_server', '-Jobs', $Jobs.ToString(), '-CudaArchitectures', '89',
        '-CpuArch', 'avx2', '-DeploymentBuild')
    Invoke-NativeChecked $ShellExe $BuildArgs

    $PrepareArgs = @($PrepareScript, '--workspace', $Workspace, '--source-dir', $SourceDir,
        '--executable', $ServerExe, '--port', $Port.ToString())
    if ($BuildOnly) {
        $PrepareArgs += '--register-build'
    }
    elseif (-not [string]::IsNullOrWhiteSpace($ModelFile)) {
        $PrepareArgs += @('--model-file', $ModelFile)
    }
    else {
        Write-Host 'Downloading the fixed Q4/Q8 GGUF (22.91 GiB); the Hub cache resumes incomplete downloads.'
        $PrepareArgs += '--download'
    }
    Invoke-NativeChecked $PythonExe $PrepareArgs
    Write-Host 'Drama setup completed. No inference or DeepSeek request was performed.'
    if ($BuildOnly) { Write-Host 'The model still needs preparation: run scripts/prepare-drama.py --download.' }
    else { Write-Host ('Runtime manifest: ' + (Join-Path $RuntimeDir 'runtime.json')) }
    exit 0
}
catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    Write-Host 'Setup did not complete. Existing weights, checkouts and working manifests were retained.'
    exit 1
}
