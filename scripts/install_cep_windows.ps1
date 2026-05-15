$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ExtensionSource = Join-Path $Root "extension"
$ExtensionTarget = Join-Path $env:APPDATA "Adobe\CEP\extensions\com.plugin.grospouce"

if (!(Test-Path $ExtensionSource)) {
    throw "Extension folder not found: $ExtensionSource"
}

Write-Host "Installing CEP extension to $ExtensionTarget"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ExtensionTarget) | Out-Null
if (Test-Path $ExtensionTarget) {
    Remove-Item $ExtensionTarget -Recurse -Force
}
Copy-Item $ExtensionSource $ExtensionTarget -Recurse

Write-Host "Enabling unsigned CEP extensions for current user"
foreach ($version in 9, 10, 11, 12, 13) {
    $key = "HKCU:\Software\Adobe\CSXS.$version"
    New-Item -Path $key -Force | Out-Null
    New-ItemProperty -Path $key -Name "PlayerDebugMode" -Value "1" -PropertyType String -Force | Out-Null
}

Write-Host ""
Write-Host "Done. Restart Premiere Pro, then open Window > Extensions > Gros Pouce Subtitles."
