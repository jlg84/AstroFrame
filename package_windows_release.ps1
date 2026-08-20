$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$Version = (Get-Content "VERSION" -Raw).Trim()
$AppDir = "dist\AstroFrame"
$Exe = Join-Path $AppDir "AstroFrame.exe"
$ReleaseDir = "release"
$StageDir = Join-Path $ReleaseDir "windows-staging"
$Zip = Join-Path $ReleaseDir "AstroFrame-$Version-Windows-x64.zip"
$Checksum = "$Zip.sha256"

if (-not (Test-Path $Exe)) {
    Write-Host "AstroFrame.exe was not found at $Exe"
    Write-Host "Build and smoke-test the Windows application first."
    exit 1
}

if (-not (Test-Path "docs\WINDOWS_INSTALL.md")) {
    Write-Host "docs\WINDOWS_INSTALL.md was not found."
    exit 1
}

Remove-Item $StageDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $StageDir -Force | Out-Null
New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null

Copy-Item $AppDir -Destination $StageDir -Recurse
Copy-Item "docs\WINDOWS_INSTALL.md" -Destination (Join-Path $StageDir "READ ME FIRST.md")

Remove-Item $Zip -Force -ErrorAction SilentlyContinue
Remove-Item $Checksum -Force -ErrorAction SilentlyContinue

Compress-Archive -Path "$StageDir\*" -DestinationPath $Zip -Force

$Hash = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower()
"$Hash  $(Split-Path $Zip -Leaf)" |
    Set-Content -Encoding ascii $Checksum

Remove-Item $StageDir -Recurse -Force

Write-Host ""
Write-Host "Created: $Zip"
Write-Host "Checksum: $Checksum"
Write-Host ""
Write-Host "Important: this package is not code-signed."
Write-Host "See docs\WINDOWS_INSTALL.md for first-launch instructions."