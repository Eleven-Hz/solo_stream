# Generates dist\SoloStream\BUILD_INFO.txt — provenance for the built artifacts.
# Records: source commit + dirty state, tool/package versions, artifact SHA256, build time.
# Run by build.bat after PyInstaller; also runnable standalone for inspection.
$ErrorActionPreference = 'Stop'

$distDir = 'dist\SoloStream'
if (-not (Test-Path $distDir)) {
    Write-Error "dist folder not found: $distDir (run the build first)"
    exit 1
}

# --- source provenance ---
$commit = (& git rev-parse HEAD 2>$null)
if (-not $commit) { $commit = '(not a git repo / git unavailable)' }
$porcelain = (& git status --porcelain 2>$null)
$dirty = if ([string]::IsNullOrWhiteSpace($porcelain)) { 'clean' } else { 'DIRTY (uncommitted changes present)' }

# --- app version (single source: src/__init__.py) ---
$appVersion = '(unknown)'
$initPy = 'src\__init__.py'
if (Test-Path $initPy) {
    $m = Select-String -Path $initPy -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { $appVersion = $m.Matches[0].Groups[1].Value }
}

# --- tool / package versions (from the build venv) ---
$py = 'venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
function Get-PkgVer($name) {
    $v = & $py -c "import importlib.metadata as m; print(m.version('$name'))" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $v) { return '(not installed)' }
    return $v.Trim()
}
$pyVer        = (& $py -c "import sys; print(sys.version.split()[0])" 2>$null)
$piVer        = Get-PkgVer 'pyinstaller'
$torchVer     = Get-PkgVer 'torch'
$sileroVer    = Get-PkgVer 'silero-vad'
$genaiVer     = Get-PkgVer 'google-genai'

# --- artifact hashes ---
$targets = @(
    "$distDir\SoloStream.exe"
)
# Hash the user guide too (Korean-named .txt next to exe), if present.
Get-ChildItem -LiteralPath $distDir -Filter '*.txt' |
    Where-Object { $_.Name -match '[^\x00-\x7F]' -and $_.Name -ne 'BUILD_INFO.txt' } |
    ForEach-Object { $targets += $_.FullName }

$hashLines = foreach ($t in $targets) {
    if (Test-Path -LiteralPath $t) {
        $h = (Get-FileHash -LiteralPath $t -Algorithm SHA256).Hash
        $name = Split-Path -Leaf $t
        "{0}  {1}" -f $h, $name
    } else {
        "(missing)  {0}" -f (Split-Path -Leaf $t)
    }
}

$now = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz')

$lines = @()
$lines += 'Solo Stream - BUILD INFO'
$lines += '========================'
$lines += ''
$lines += "build time   : $now"
$lines += "app version  : $appVersion"
$lines += "source commit: $commit"
$lines += "tree state   : $dirty"
$lines += ''
$lines += 'tooling'
$lines += "  python      : $pyVer"
$lines += "  pyinstaller : $piVer"
$lines += "  torch       : $torchVer"
$lines += "  silero-vad  : $sileroVer"
$lines += "  google-genai: $genaiVer"
$lines += ''
$lines += 'artifact SHA256'
$lines += $hashLines
$lines += ''
$lines += 'note: provenance is trustworthy only when tree state is "clean".'
$lines += '      verify a received release with SoloStream.zip.sha256.'

$out = "$distDir\BUILD_INFO.txt"
$lines -join "`r`n" | Out-File -FilePath $out -Encoding utf8
Write-Host "[build-info] wrote $out (commit $commit, $dirty)"
