param([string]$BaseUrl='http://127.0.0.1:4824',[string]$Node='node',[string]$PlaywrightModule='', [switch]$Browser)
$ErrorActionPreference='Stop'
$taskRoot=[System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location $taskRoot
try {
  $taskTemp=Join-Path $taskRoot '.test-tmp'
  New-Item -ItemType Directory -Force -Path $taskTemp | Out-Null
  $env:TEMP=$taskTemp; $env:TMP=$taskTemp; $env:PYTHONPATH=$taskRoot
  & '.venv/Scripts/python.exe' -B -m unittest discover -s tests -v
  if ($LASTEXITCODE -ne 0) {throw 'Python regression failed'}
  if (Test-Path (Join-Path $taskRoot '../Pico_4824A/pico4824a')) {
    & '.venv/Scripts/python.exe' -B tests/compare_source_algorithms.py
    if ($LASTEXITCODE -ne 0) {throw 'Reference equivalence failed'}
  }
  if ($Browser) {
    $env:WAVEGUARD_BASE_URL=$BaseUrl
    if ($PlaywrightModule) {$env:WAVEGUARD_PLAYWRIGHT_MODULE=$PlaywrightModule}
    foreach($taskScript in @('tests/workstation_presentation_checks.js','tests/audit-repair-browser.cjs','tests/final-session-browser.cjs','tests/shared-ui-browser-regression.cjs','tests/shared-ui-loaded-regression.cjs','tests/storage-browser-regression.cjs')) {
      & $Node $taskScript
      if ($LASTEXITCODE -ne 0) {throw "Browser regression failed: $taskScript"}
    }
  }
} finally {Pop-Location}
