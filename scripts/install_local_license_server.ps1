$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot
$config = Join-Path $env:APPDATA 'LocalBooksLicenseServer\config.json'
if (!(Test-Path -LiteralPath $config)) { throw 'Provision the private server configuration first.' }
if (Get-Process -Name books-license-server -ErrorAction SilentlyContinue) { throw 'Stop your local license server before updating it.' }
$destination = Join-Path $env:LOCALAPPDATA 'Programs\LocalBooksLicenseServer'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'build\license-server\books-license-server.exe') -Destination $destination
$startup = [Environment]::GetFolderPath('Startup')
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $startup 'Local Books License Server.lnk'))
$shortcut.TargetPath = Join-Path $destination 'books-license-server.exe'
$shortcut.WorkingDirectory = $destination
$shortcut.WindowStyle = 7
$shortcut.Description = 'Local Books license server'
$shortcut.Save()
Write-Output 'Server installed. It will start when this user signs in to Windows.'
