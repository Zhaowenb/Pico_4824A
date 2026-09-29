$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Python environment is not ready. Run .\setup.ps1 first."
}
Set-Location -LiteralPath $ProjectDir
Write-Host "Open http://127.0.0.1:4824 in your browser."
& $VenvPython -m pico4824a web --host 127.0.0.1 --port 4824
