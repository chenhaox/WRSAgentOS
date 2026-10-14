# SDK only by default. -BuildExtensions also prepares local CUDA tools and pinned source.
param([switch]$BuildExtensions)

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
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $projectRoot '.local/venvs/graspnet'
    # Keep the separately compiled local extension package when refreshing the SDK.
    & $uvExe sync --inexact --locked --no-default-groups --extra graspnet --python $pythonExe
    if ($LASTEXITCODE) { throw 'GraspNet SDK installation failed' }
    if (-not $BuildExtensions) { return }

    # No installer or driver change. NVIDIA publishes hashes for these individual archives.
    $baseUrl = 'https://developer.download.nvidia.com/compute/cuda/redist/'
    $manifest = Invoke-RestMethod ($baseUrl + 'redistrib_12.8.1.json')
    $cudaRoot = Join-Path $projectRoot '.local/cuda/12.8.1'
    $downloads = Join-Path $projectRoot '.local/downloads/cuda-12.8.1'
    New-Item -ItemType Directory -Force -Path $cudaRoot, $downloads | Out-Null
    $manifest | ConvertTo-Json -Depth 12 | Set-Content (Join-Path $downloads 'manifest.json')
    foreach ($component in @('cuda_nvcc', 'cuda_cudart', 'cuda_cccl')) {
        $entry = $manifest.$component.'windows-x86_64'
        $filename = Split-Path $entry.relative_path -Leaf
        $archive = Join-Path $downloads $filename
        if (-not (Test-Path -LiteralPath $archive)) {
            Write-Host "Downloading $component ($($entry.size) bytes)"
            Invoke-WebRequest ($baseUrl + $entry.relative_path) -OutFile ($archive + '.part')
            if ((Get-Item -LiteralPath ($archive + '.part')).Length -ne [long]$entry.size -or
                (Get-FileHash -LiteralPath ($archive + '.part')).Hash.ToLowerInvariant() -ne $entry.sha256) {
                throw "Invalid $component download"
            }
            Move-Item -LiteralPath ($archive + '.part') -Destination $archive
        }
        if ((Get-Item -LiteralPath $archive).Length -ne [long]$entry.size -or
            (Get-FileHash -LiteralPath $archive).Hash.ToLowerInvariant() -ne $entry.sha256) {
            throw "Invalid cached $component archive"
        }
        Expand-Archive -LiteralPath $archive -DestinationPath $downloads -Force
        $expanded = Join-Path $downloads ([IO.Path]::GetFileNameWithoutExtension($filename))
        foreach ($folder in @('bin', 'include', 'lib', 'nvvm')) {
            $part = Join-Path $expanded $folder
            if (Test-Path -LiteralPath $part) {
                Copy-Item -LiteralPath $part -Destination $cudaRoot -Recurse -Force
            }
        }
        # Preserve each component's notices; they may share the same filename.
        $notices = Join-Path $cudaRoot "licenses/$component"
        New-Item -ItemType Directory -Path $notices -Force | Out-Null
        Get-ChildItem -LiteralPath $expanded -File | Copy-Item -Destination $notices -Force
    }
    $source = Join-Path $projectRoot '.local/src/graspnet-baseline'
    $revision = '280c215129f759ed8649cb4e89fc5dfee55f4f80'
    if (-not (Test-Path -LiteralPath $source)) {
        & git -c core.autocrlf=false clone --no-checkout https://github.com/graspnet/graspnet-baseline.git $source
        if ($LASTEXITCODE) { throw 'GraspNet source download failed' }
        & git -C $source -c core.autocrlf=false checkout --detach $revision
        if ($LASTEXITCODE) { throw 'GraspNet source checkout failed' }
    }
    # The Python build checks revision and source cleanliness before copying any sources.
    & (Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts/python.exe') scripts/build_graspnet.py --source $source --cuda-home $cudaRoot
    if ($LASTEXITCODE) { throw 'GraspNet extension build failed' }
} finally {
    $env:UV_PROJECT_ENVIRONMENT = $previousEnvironment
    $env:UV_CACHE_DIR = $previousCache
}
Write-Host 'GraspNet SDK ready; no trained weights or hardware were loaded.'
