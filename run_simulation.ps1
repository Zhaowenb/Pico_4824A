$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Python environment is not ready. Run .\setup.ps1 first."
}
Set-Location -LiteralPath $ProjectDir
& $VenvPython -m pico4824a capture --simulate --config config.example.json --format npz
