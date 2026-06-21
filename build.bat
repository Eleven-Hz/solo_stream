@echo off
rem Solo Stream portable(onedir) build.
rem Pre: venv has requirements.txt + requirements-build.txt installed.
rem Out: dist\SoloStream\SoloStream.exe + _internal\
setlocal

echo [build] clean build/ dist/
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [build] run PyInstaller
call venv\Scripts\pyinstaller.exe solo_stream.spec
if errorlevel 1 (
    echo [build] FAILED: PyInstaller exit code %errorlevel%
    exit /b %errorlevel%
)

rem Copy user guide next to exe (deploy root) so users can see it.
rem Putting it in spec datas would bury it inside _internal\.
rem Use PowerShell (not cmd copy) so the Korean filename survives codepage issues.
echo [build] copy user guide
powershell -NoProfile -Command "Get-ChildItem -LiteralPath . -Filter '*.txt' | Where-Object { $_.Name -match '[^\x00-\x7F]' } | Copy-Item -Destination 'dist\SoloStream\' -Force"
if errorlevel 1 echo [build] WARN: failed to copy user guide - check manually

rem Provenance manifest: ties this exe to a commit + records artifact SHA256.
rem Trustworthy only when built on a clean (committed) tree; dirty builds are flagged.
echo [build] write BUILD_INFO.txt
powershell -NoProfile -ExecutionPolicy Bypass -File make_build_info.ps1
if errorlevel 1 echo [build] WARN: BUILD_INFO.txt generation failed - check manually

rem Package the deploy folder + a checksum so recipients can verify the zip is intact/untampered.
echo [build] zip + sha256
powershell -NoProfile -ExecutionPolicy Bypass -File make_release_zip.ps1
if errorlevel 1 echo [build] WARN: zip/sha256 packaging failed - check manually

echo [build] done: dist\SoloStream\SoloStream.exe
endlocal
