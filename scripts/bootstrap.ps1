param([ValidateSet("glm")][string[]]$Extra = @())

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$pythonExe = 'D:\code\venv312\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw "Missing required interpreter: $pythonExe" }
$uvExe = Join-Path $projectRoot '.local\tools\bin\uv.exe'
if (-not (Test-Path -LiteralPath $uvExe)) {
    & $pythonExe -m pip install --target .local/tools 'uv==0.12.15'
    if ($LASTEXITCODE) { throw 'uv installation failed' }
}
$env:UV_CACHE_DIR = Join-Path $projectRoot '.local\uv-cache'
$extraArguments = @()
foreach ($name in $Extra) { $extraArguments += @("--extra", $name) }
& $uvExe export @extraArguments --python $pythonExe --locked --quiet --no-emit-project --format requirements-txt --output-file .local/requirements.txt
if ($LASTEXITCODE) { throw 'Lock export failed' }
& $uvExe pip sync --python $pythonExe --target .local/deps .local/requirements.txt
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
