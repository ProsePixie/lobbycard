# Builds Vortex.cs -> Vortex.exe with the compiler that ships with Windows
# (no SDK needed).
#
#   powershell -ExecutionPolicy Bypass -File extras\vortex\build_vortex.ps1
#
# Run it from PowerShell, not Git Bash: Bash rewrites csc's /flag arguments.
param(
    [string]$Out = "Vortex.exe"
)

$ErrorActionPreference = 'Stop'

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$src = Join-Path $PSScriptRoot "Vortex.cs"
$outPath = Join-Path $PSScriptRoot $Out

if (-not (Test-Path $csc)) { throw "csc.exe not found at $csc" }
if (-not (Test-Path $src)) { throw "Vortex.cs not found at $src" }

# /target:winexe -> no console window on run.
# /codepage:65001 -> read Vortex.cs as UTF-8 (it has no BOM; without this csc
# falls back to the system ANSI codepage and mangles non-ASCII characters in
# string literals, e.g. the middot in the status line).
& $csc /nologo /codepage:65001 /target:winexe "/out:$outPath" `
    /reference:System.dll `
    /reference:System.Core.dll `
    /reference:System.Drawing.dll `
    /reference:System.Windows.Forms.dll `
    /reference:System.Web.Extensions.dll `
    /reference:Microsoft.VisualBasic.dll `
    "$src"

if ($LASTEXITCODE -ne 0) { throw "csc.exe failed with exit code $LASTEXITCODE" }
Write-Host "Built $outPath"
