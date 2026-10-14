# Install locked FastSAM/CLIP SDKs only; weights are prepared separately.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$pythonExe = if ($env:WRS_AGENT_PYTHON) { $env:WRS_AGENT_PYTHON } else { 'python' }
$uvExe = Join-Path $projectRoot '.local/tools/bin/uv.exe'
if (-not (Test-Path -LiteralPath $uvExe)) { throw 'Run scripts/bootstrap.ps1 first.' }
$previousEnvironment = $env:UV_PROJECT_ENVIRONMENT
$previousCache = $env:UV_CACHE_DIR
try {
    $env:UV_CACHE_DIR = Join-Path $projectRoot '.local/uv-cache'
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $projectRoot '.local/venvs/vision'
    & $uvExe sync --locked --no-default-groups --extra fastsam --python $pythonExe
    if ($LASTEXITCODE) { throw 'Vision SDK dependency installation failed' }
} finally {
    $env:UV_PROJECT_ENVIRONMENT = $previousEnvironment
    $env:UV_CACHE_DIR = $previousCache
}
Write-Host 'SDK ready. Explicit weights: scripts/download_fastsam.py --download'
