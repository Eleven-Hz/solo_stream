# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Solo Stream 포터블(onedir) 빌드.

빌드: pyinstaller solo_stream.spec  (또는 build.bat)
산출: dist/SoloStream/SoloStream.exe + _internal/

설계 메모:
- 진입점은 src/main.py 이지만 코드가 `from src.utils...` 패키지 절대 import 를 쓰므로
  pathex 에 프로젝트 루트를 넣어 `src` 패키지를 import 가능하게 한다.
- 번들 리소스(config.yaml, personas/, game_profiles/default.yaml, silero 모델)는
  exe 옆(=sys._MEIPASS / paths.resource_dir())에서 읽힌다. paths.py 와 짝.
- 쓰기 데이터(사용자 config, .env.local, game_profiles 생성물, logs)는 런타임에
  %APPDATA%\\SoloStream 으로 가므로 datas 에 넣지 않는다.
- torch 는 CPU-only 휠 기준(현재 venv 2.x+cpu). CUDA 휠이면 번들이 1GB+ 로 커진다.
"""
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

PROJECT_ROOT = os.path.abspath(os.getcwd())

# ── 번들 데이터 (읽기전용 리소스) ──────────────────────────────────────────────
# (src, dest) — dest 는 번들 루트(_MEIPASS) 기준 상대 경로.
datas = [
    ("config.yaml", "."),
    ("personas", "personas"),
    ("game_profiles/default.yaml", "game_profiles"),
]

# silero-vad 모델 파일(.jit/.onnx)은 패키지 내부 data/ 를 참조하므로 함께 수집.
datas += collect_data_files("silero_vad", includes=["**/*.jit", "**/*.onnx"])

binaries = []
hiddenimports = []

# ── 무거운/동적 import 패키지 전체 수집 ───────────────────────────────────────
for pkg in ("torch", "skimage", "google.genai", "anthropic", "openai", "sounddevice", "soundfile"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# pywin32 동적 모듈 + lazy import 보강.
hiddenimports += [
    "win32gui",
    "win32api",
    "win32con",
    "silero_vad",
    "mss",
    "mss.tools",
    "PIL",
    "PIL.Image",
    # google-genai lazy import 보강
    "google.genai",
    "google.genai.types",
    # multiprocessing spawn 시 자식이 import 하는 우리 패키지 진입 모듈
    "src.main",
]

a = Analysis(
    ["src/main.py"],
    pathex=[PROJECT_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    # 런타임 훅에서 freeze_support() 를 호출하면 안 된다 — spawn 자식이 런타임 훅을
    # __main__ 으로 부트스트랩할 때 타겟 함수(ai_process 등)를 rthook 모듈에서
    # 찾으려다 AttributeError 로 죽는다. freeze_support() 는 main.py 의
    # `if __name__ == "__main__":` 블록에 있으며 그게 올바른 유일한 위치다.
    runtime_hooks=[],
    excludes=[
        # 번들 크기 절감: 테스트/노트북/시각화 등 런타임 불필요 패키지.
        "tkinter",
        "matplotlib",
        "pytest",
        "IPython",
        # cv2(opencv) ~98MB — skimage 가 끌어오지만 SSIM 경로는 cv2 미사용 (실측 확인).
        "cv2",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)


# ── 런타임 불필요 파일 제거 (크기 절감) ───────────────────────────────────────
# torch 는 C++ 헤더(include/)와 정적 링크 라이브러리(*.lib)를 함께 깔지만
# 런타임 추론에는 전혀 쓰이지 않는다. test 디렉터리도 마찬가지.
# a.datas / a.binaries 의 dest 경로(첫 요소) 기준으로 걸러낸다.
def _keep(entry):
    dest = entry[0].replace("\\", "/")
    # torch C++ 개발 헤더
    if "torch/include/" in dest:
        return False
    # 정적 링크 라이브러리 (Windows .lib — 런타임 미사용, DLL 은 유지)
    if dest.endswith(".lib"):
        return False
    # torch 내부 테스트 디렉터리
    if dest.startswith("torch/test/") or "/torch/test/" in dest:
        return False
    return True


_n_datas, _n_bins = len(a.datas), len(a.binaries)
a.datas = [e for e in a.datas if _keep(e)]
a.binaries = [e for e in a.binaries if _keep(e)]
print(
    f"[spec] 크기 절감 필터: datas {_n_datas}->{len(a.datas)}, "
    f"binaries {_n_bins}->{len(a.binaries)}"
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SoloStream",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # --windowed: 콘솔 창 없음 (pythonw 역할 대체)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SoloStream",
)
