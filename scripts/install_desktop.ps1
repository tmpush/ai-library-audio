param([string]$Destination = (Join-Path $env:LOCALAPPDATA 'Programs\LocalBooksStudio'), [switch]$ShortcutsOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot
$release = Join-Path $projectRoot 'client\native\target\release'
$sourceEngine = Join-Path $projectRoot 'build\sidecar-full\books-engine'
$executable = Join-Path $release 'local-books-order.exe'
if (!(Test-Path -LiteralPath $executable) -or !(Test-Path -LiteralPath (Join-Path $sourceEngine 'books-engine.exe'))) {
    throw 'Build the full desktop application first.'
}
$target = [IO.Path]::GetFullPath($Destination)
if (!$ShortcutsOnly -and (Get-Process -Name local-books-order -ErrorAction SilentlyContinue)) { throw 'Close the desktop app before updating it.' }
New-Item -ItemType Directory -Force -Path $target | Out-Null
if (!$ShortcutsOnly) {
if (Test-Path -LiteralPath (Join-Path $target 'local-books-order.exe')) {
    $backup = Join-Path $target ('backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    Copy-Item -LiteralPath (Join-Path $target 'local-books-order.exe') -Destination $backup
}
Copy-Item -LiteralPath $executable -Destination (Join-Path $target 'local-books-order.exe')
$engineTarget = Join-Path $target 'engine'
New-Item -ItemType Directory -Force -Path $engineTarget | Out-Null
& robocopy $sourceEngine $engineTarget /E /MT:8 /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Engine copy failed: robocopy exit $LASTEXITCODE" }
$global:LASTEXITCODE = 0
}
# A separate icon path prevents Explorer from reusing the old executable icon.
New-Item -ItemType Directory -Force (Join-Path $target 'licenses') | Out-Null
Get-ChildItem (Join-Path $projectRoot 'client/native/assets') -File | Where-Object { $_.Name -like '*-OFL.txt' -or $_.Name -like '*-LICENSE.txt' } | Copy-Item -Destination (Join-Path $target 'licenses')
$shortcutIcon = Join-Path $target 'lunatica-icon.ico'
Copy-Item -LiteralPath (Join-Path $projectRoot 'client/native/assets/icon.ico') -Destination $shortcutIcon -Force
$shell = New-Object -ComObject WScript.Shell
$shortcutDirs = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($shortcutDir in $shortcutDirs) {
    # WSH Save uses the system ANSI code page on some Windows installations.
    # Save under an ASCII name, then rename with PowerShell's Unicode file API.
    $temporaryLink = Join-Path $shortcutDir ('LocalBooks-' + [guid]::NewGuid().ToString('N') + '.lnk')
    $link = $shell.CreateShortcut($temporaryLink)
    $link.TargetPath = Join-Path $target 'local-books-order.exe'
    $link.WorkingDirectory = $target
    $link.IconLocation = "$shortcutIcon,0"
    $link.Description = 'Lunatica — локальная подготовка и озвучка книг'
    $link.Save()
    Move-Item -LiteralPath $temporaryLink -Destination (Join-Path $shortcutDir 'Lunatica.lnk') -Force
    $oldShortcut = Join-Path $shortcutDir 'Книжная мастерская.lnk'
    if (Test-Path -LiteralPath $oldShortcut) {
        $oldLink = $shell.CreateShortcut($oldShortcut)
        if ($oldLink.TargetPath -eq $link.TargetPath) { Remove-Item -LiteralPath $oldShortcut }
    }
}
Write-Output "Installed: $target"
Write-Output 'Desktop and Start menu shortcuts created.'
