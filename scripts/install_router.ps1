$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$archive = Join-Path $projectRoot '.local/downloads/zenoh-1.9.0.zip'
$expected = '07af486bedd6e2138e187f277d4d8a74632ec1e7659f10dc76aa18a36b5d2a75'
New-Item -ItemType Directory -Force (Split-Path $archive -Parent) | Out-Null
if (-not (Test-Path -LiteralPath $archive)) {
    Invoke-WebRequest 'https://github.com/eclipse-zenoh/zenoh/releases/download/1.9.0/zenoh-1.9.0-x86_64-pc-windows-msvc-standalone.zip' -OutFile $archive
}
$actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) { throw 'zenohd checksum mismatch; archive retained for inspection' }
Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $projectRoot '.local/zenoh-1.9.0') -Force
& .local/zenoh-1.9.0/zenohd.exe --version
if ($LASTEXITCODE) { throw 'zenohd version check failed' }
