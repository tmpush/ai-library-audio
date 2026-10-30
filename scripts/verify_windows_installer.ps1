param([string]$Installer)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot
if (!$Installer) { $Installer = (Get-ChildItem (Join-Path $projectRoot 'dist/windows/*-setup.exe') | Select-Object -First 1).FullName }
if (!$Installer -or !(Test-Path -LiteralPath $Installer)) { throw 'Installer not found' }
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('lbo-installer-' + [guid]::NewGuid().ToString('N'))
$target = Join-Path $testRoot 'program'
New-Item -ItemType Directory -Path $testRoot | Out-Null
# This test is intended for disposable Windows CI, not the owner's current installation.
if ($env:CI -ne 'true') { throw 'Run installation/uninstallation acceptance only on a disposable CI runner.' }
$argsList = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"/DIR=`"$target`"",'/MERGETASKS=!desktopicon',"/LOG=`"$testRoot\install.log`"")
$p = Start-Process -FilePath $Installer -ArgumentList $argsList -WindowStyle Hidden -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Install failed: $($p.ExitCode)" }
foreach ($item in @('local-books-order.exe','engine/books-engine.exe','unins000.exe')) {
    if (!(Test-Path -LiteralPath (Join-Path $target $item))) { throw "Missing installed file: $item" }
}
$shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'Книжная мастерская.lnk'
if (!(Test-Path -LiteralPath $shortcut)) { throw 'Start menu shortcut was not created' }
$link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
if ($link.TargetPath -ne (Join-Path $target 'local-books-order.exe')) { throw 'Shortcut points to the wrong executable' }
& (Join-Path $projectRoot '.venv/Scripts/python.exe') (Join-Path $projectRoot 'scripts/verify_sidecar.py') --executable (Join-Path $target 'engine/books-engine.exe')
if ($LASTEXITCODE -ne 0) { throw 'Installed engine failed verification' }
$userData = Join-Path $env:APPDATA 'app.localbooks.studio'
New-Item -ItemType Directory -Force $userData | Out-Null
$sentinel = Join-Path $userData 'installer-preservation-check.txt'
Set-Content -LiteralPath $sentinel -Value 'Keep user data'
$p = Start-Process -FilePath (Join-Path $target 'unins000.exe') -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -WindowStyle Hidden -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Uninstall failed: $($p.ExitCode)" }
if (Test-Path -LiteralPath (Join-Path $target 'local-books-order.exe')) { throw 'Uninstall left the executable behind' }
if (Test-Path -LiteralPath $shortcut) { throw 'Uninstall left the shortcut behind' }
if (!(Test-Path -LiteralPath $sentinel)) { throw 'Uninstall deleted user data' }
@{install=$true;shortcut=$true;installed_engine=$true;uninstall=$true;user_data_preserved=$true} | ConvertTo-Json | Set-Content (Join-Path $projectRoot 'artifacts/installer-check.json')
Write-Output 'Installer acceptance passed: install, relocated engine, uninstall, user data preserved.'
