# Packages dist\SoloStream into dist\SoloStream.zip and writes a SHA256 sidecar.
# Recipients verify integrity by hashing the zip and matching SoloStream.zip.sha256.
# Run by build.bat after BUILD_INFO.txt; also runnable standalone.
$ErrorActionPreference = 'Stop'

$srcDir  = 'dist\SoloStream'
$zipPath = 'dist\SoloStream.zip'
$shaPath = "$zipPath.sha256"

if (-not (Test-Path $srcDir)) {
    Write-Error "source folder not found: $srcDir (run the build first)"
    exit 1
}

if (Test-Path $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
if (Test-Path $shaPath) { Remove-Item -LiteralPath $shaPath -Force }

# Zip the folder itself so it extracts as SoloStream\...
Compress-Archive -Path $srcDir -DestinationPath $zipPath -CompressionLevel Optimal
$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash

# Sidecar format: "<HASH>  SoloStream.zip" (two spaces — sha256sum-compatible).
"{0}  {1}" -f $zipHash, (Split-Path -Leaf $zipPath) |
    Out-File -FilePath $shaPath -Encoding ascii

$sizeMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host "[release-zip] $zipPath ($sizeMB MB)"
Write-Host "[release-zip] sha256 $zipHash -> $shaPath"
