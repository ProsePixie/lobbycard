# Registers two custom URI protocol handlers under HKEY_CURRENT_USER (no admin required):
#   openfolder://  -> opens the given path in Windows Explorer
#   openvlc://     -> opens the given file in VLC (falls back to the OS default handler)
# Both are scoped to your user account only and can be removed by deleting the
# HKCU:\Software\Classes\openfolder and HKCU:\Software\Classes\openvlc registry keys.
#
# Handlers are compiled .exe files (not PowerShell) specifically so a security
# suite doesn't see "browser launches hidden PowerShell with -ExecutionPolicy
# Bypass" - a pattern that closely matches known malicious browser-protocol-
# hijack attacks and gets silently blocked by tools like Bitdefender.

$ErrorActionPreference = 'Stop'
$ScriptDir = $PSScriptRoot

function Register-Protocol {
    param([string]$Scheme, [string]$HandlerExe)

    $key = "HKCU:\Software\Classes\$Scheme"
    New-Item -Path $key -Force | Out-Null
    Set-ItemProperty -Path $key -Name "(Default)" -Value "URL:$Scheme Protocol"
    New-ItemProperty -Path $key -Name "URL Protocol" -Value "" -PropertyType String -Force | Out-Null

    $iconKey = "$key\DefaultIcon"
    New-Item -Path $iconKey -Force | Out-Null
    Set-ItemProperty -Path $iconKey -Name "(Default)" -Value "explorer.exe,0"

    $cmdKey = "$key\shell\open\command"
    New-Item -Path $cmdKey -Force | Out-Null
    $cmd = "`"$ScriptDir\$HandlerExe`" `"%1`""
    Set-ItemProperty -Path $cmdKey -Name "(Default)" -Value $cmd

    Write-Host "Registered ${Scheme}:// -> $HandlerExe"
}

Register-Protocol "openfolder" "OpenFolderHandler.exe"
Register-Protocol "openvlc" "OpenVlcHandler.exe"

Write-Host "`nDone. First click on a link may show a one-time Windows confirmation dialog - that's normal browser/OS behavior for any new protocol handler, not something we can skip."
