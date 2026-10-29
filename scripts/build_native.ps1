$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
# These are public configuration values; never embed the signing key or owner token.
$env:BOOKS_LICENSE_URL = 'http://127.0.0.1:8791'
$publicKey = Join-Path $env:APPDATA 'LocalBooksLicenseServer\public-key.txt'
if (!(Test-Path -LiteralPath $publicKey)) { throw 'License server public key is missing.' }
$env:BOOKS_LICENSE_PUBLIC_KEY = Get-Content -LiteralPath $publicKey -Raw
& cargo build --release --locked --manifest-path (Join-Path $projectRoot 'client\native\Cargo.toml')
if ($LASTEXITCODE -ne 0) { throw 'Native build failed.' }
