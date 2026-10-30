param([string]$Compiler = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot
foreach ($item in @('client\native\target\release\local-books-order.exe','build\sidecar-full\books-engine\books-engine.exe')) {
    if (!(Test-Path -LiteralPath (Join-Path $projectRoot $item))) { throw "Missing build: $item" }
}
if (!(Test-Path -LiteralPath $Compiler)) { throw 'Install Inno Setup 6.5+ and supply -Compiler with its ISCC.exe path.' }
& $Compiler "/DProjectRoot=$projectRoot" (Join-Path $projectRoot 'packaging\windows\studio.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
Get-ChildItem (Join-Path $projectRoot 'dist\windows') -File | Where-Object Name -ne 'SHA256SUMS.txt' | ForEach-Object {
    $hash = Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256
    "$($hash.Hash.ToLower())  $($_.Name)"
} | Set-Content -Encoding ascii (Join-Path $projectRoot 'dist\windows\SHA256SUMS.txt')
