param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot
$source = Join-Path $projectRoot 'build/license-server/books-license-server.exe'
$installed = Join-Path $env:LOCALAPPDATA 'Programs/LocalBooksLicenseServer/books-license-server.exe'
if (!(Test-Path -LiteralPath $source) -or !(Test-Path -LiteralPath $installed)) { throw 'Build or installed server is missing' }
$backup = Join-Path (Split-Path $installed) ('backups/' + (Get-Date -Format yyyyMMdd-HHmmss))
New-Item -ItemType Directory -Force $backup | Out-Null
Copy-Item -LiteralPath $installed -Destination $backup
Get-Process -Name books-license-server -ErrorAction SilentlyContinue | Where-Object Path -eq $installed | Stop-Process
Start-Sleep -Seconds 2
Copy-Item -LiteralPath $source -Destination $installed -Force
Start-Process -FilePath $installed -WorkingDirectory (Split-Path $installed) -WindowStyle Hidden
Write-Output 'Server updated. Open http://127.0.0.1:8791. Configuration and database were preserved.'
