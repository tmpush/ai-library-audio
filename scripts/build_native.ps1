$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
& cargo build --release --locked --manifest-path (Join-Path $projectRoot 'client\native\Cargo.toml')
if ($LASTEXITCODE -ne 0) { throw 'Native build failed.' }
