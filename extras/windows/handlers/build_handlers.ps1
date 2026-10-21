# Builds the two protocol handlers with the compiler that ships with Windows
# (no SDK needed), then registers them for the current user.
#
#   powershell -ExecutionPolicy Bypass -File build_handlers.ps1
#
# Run it from PowerShell, not Git Bash: Bash rewrites csc's /flag arguments.
$ErrorActionPreference = 'Stop'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { throw "csc.exe not found at $csc" }
foreach ($name in 'OpenFolderHandler', 'OpenVlcHandler') {
    & $csc /nologo /codepage:65001 /target:winexe "/out:$PSScriptRoot\$name.exe" `
        /reference:System.dll /reference:System.Core.dll "$PSScriptRoot\$name.cs"
    if ($LASTEXITCODE -ne 0) { throw "csc.exe failed for $name" }
    Write-Host "Built $name.exe"
}
& "$PSScriptRoot\setup_protocol_handlers.ps1"
