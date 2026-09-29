param([switch]$SkipInstall)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $FilePath $($Arguments -join ' ')"
    }
}

# Find a standard 64-bit Windows CPython. Inkscape/Git/MSYS Python builds can
# create a Unix-style .venv\bin directory and cannot load the Windows PicoSDK
# DLL reliably, so they are rejected here.
$Candidates = @()
$Candidates += Get-Command python -All -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
$Candidates += Get-Command python3 -All -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
$Candidates += Join-Path $env:USERPROFILE ".local\bin\python.exe"
$Candidates += Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Programs\Python") -Filter python.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName

$BasePython = $null
foreach ($Candidate in ($Candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique)) {
    $Probe = & $Candidate -c "import struct,sys,sysconfig; print('PICO_OK' if sys.platform == 'win32' and sysconfig.get_platform().startswith('win-') and struct.calcsize('P') == 8 and sys.version_info >= (3,10) else 'PICO_SKIP')" 2>$null
    if ($LASTEXITCODE -eq 0 -and @($Probe)[-1] -eq "PICO_OK") {
        $BasePython = $Candidate
        break
    }
}

if (-not $BasePython) {
    throw @"
No standard 64-bit Windows Python 3.10+ installation was found.
Install Python 3.12 (64-bit) from https://www.python.org/downloads/windows/
and enable 'Add python.exe to PATH', then run .\setup.ps1 again.
"@
}

Write-Host "Using Windows Python: $BasePython"

if ((Test-Path -LiteralPath $VenvDir) -and -not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "Removing the incompatible virtual environment created by a non-Windows Python build."
    $ResolvedProject = (Resolve-Path -LiteralPath $ProjectDir).Path
    $ResolvedVenv = (Resolve-Path -LiteralPath $VenvDir).Path
    if (-not $ResolvedVenv.StartsWith($ResolvedProject, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a virtual environment outside the project directory."
    }
    Remove-Item -LiteralPath $ResolvedVenv -Recurse -Force
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Invoke-NativeChecked -FilePath $BasePython -Arguments @("-m", "venv", $VenvDir)
}
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Virtual environment creation did not produce $VenvPython"
}

if (-not $SkipInstall) {
    $PipNetworkArgs = @(
        "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple",
        "--extra-index-url", "https://pypi.org/simple",
        "--retries", "10",
        "--timeout", "180",
        "--progress-bar", "off"
    )
    Invoke-NativeChecked -FilePath $VenvPython -Arguments (@("-m", "pip", "install") + $PipNetworkArgs + @("--upgrade", "pip"))
    Invoke-NativeChecked -FilePath $VenvPython -Arguments (@("-m", "pip", "install") + $PipNetworkArgs + @("--only-binary=:all:", "-r", (Join-Path $ProjectDir "requirements.txt")))
    Invoke-NativeChecked -FilePath $VenvPython -Arguments @("-c", "import numpy; print('NumPy', numpy.__version__)")
}

Write-Host ""
Write-Host "Python environment is ready: $VenvDir" -ForegroundColor Green
Write-Host "Next: run .\start_web.ps1"
Write-Host "Install the 64-bit PicoSDK separately before using real hardware."
