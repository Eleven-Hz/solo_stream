"""실행 환경(스크립트 / frozen onedir)에 무관하게 올바른 파일 위치를 찾는 단일 모듈.

두 가지 루트 개념을 분리한다.

- ``resource_dir()`` — 읽기전용 번들 리소스 (기본 config.yaml, personas/*.yaml,
  game_profiles/default.yaml, silero 모델 등). frozen 이면 exe 옆, 아니면 프로젝트 루트.
- ``user_data_dir()`` — 쓰기 가능한 사용자 폴더 (사용자 config.yaml, .env.local,
  game_profiles/ 자동생성, logs/). 기본 ``%APPDATA%\\SoloStream``.

새 파일 입출력은 ``Path(__file__)...`` 을 직접 쓰지 말고 이 모듈을 거친다.
  - 읽기전용 리소스 → ``resource_dir()`` (사용자 오버라이드는 ``resolve_resource``)
  - 쓰기/사용자 데이터 → ``user_data_dir()``

DEV 모드: 환경변수 ``SOLO_STREAM_DEV=1`` 이면 ``user_data_dir()`` 가 ``%APPDATA%`` 대신
프로젝트 루트를 반환한다 → 개발 중엔 지금처럼 프로젝트 폴더의 config/game_profiles/logs 를
직접 읽고 쓴다. ("기본값 바꿨는데 안 보임" 캐시 함정 제거.)
"""
import os
import shutil
import sys
from pathlib import Path

# 이 파일은 src/utils/paths.py — 프로젝트 루트는 3단계 위.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_APP_NAME = "SoloStream"


def is_frozen() -> bool:
    """PyInstaller 등으로 동결(frozen)된 실행 파일인지."""
    return bool(getattr(sys, "frozen", False))


def is_dev() -> bool:
    """DEV 모드 여부. SOLO_STREAM_DEV=1 이면 사용자 데이터를 프로젝트 루트로 둔다.

    frozen 빌드에서는 항상 False (배포본은 DEV 분기를 타지 않는다)."""
    if is_frozen():
        return False
    return os.getenv("SOLO_STREAM_DEV", "") == "1"


def resource_dir() -> Path:
    """읽기전용 번들 리소스 루트.

    - frozen: onedir 의 exe 옆 (PyInstaller 가 풀어둔 ``sys._MEIPASS`` 우선).
    - 스크립트: 프로젝트 루트.
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return _PROJECT_ROOT


def user_data_dir() -> Path:
    """쓰기 가능한 사용자 데이터 루트.

    - DEV 모드: 프로젝트 루트 (지금과 동일 동작).
    - 그 외: ``%APPDATA%\\SoloStream`` (APPDATA 없으면 홈 디렉터리 fallback).
    """
    if is_dev():
        return _PROJECT_ROOT
    appdata = os.getenv("APPDATA")
    base = Path(appdata) if appdata else Path.home()
    return base / _APP_NAME


def resolve_resource(rel: str) -> Path:
    """읽기전용 리소스 경로 해석: 사용자 오버라이드 우선 → 없으면 번들 기본.

    예: ``resolve_resource("config.yaml")`` 는 사용자가 편집한
    ``user_data_dir()/config.yaml`` 이 있으면 그것을, 없으면 번들 기본을 돌려준다.
    """
    user_path = user_data_dir() / rel
    if user_path.exists():
        return user_path
    return resource_dir() / rel


def ensure_user_data() -> Path:
    """최초 실행 시딩. 사용자 데이터 폴더와 필수 하위 디렉터리를 만들고
    번들 기본 config.yaml / personas/ 를 user_data 로 복사한다.

    DEV 모드면 user_data == 프로젝트 루트라 시딩이 사실상 no-op (이미 존재).
    반환값은 ``user_data_dir()``.
    """
    udir = user_data_dir()
    udir.mkdir(parents=True, exist_ok=True)
    (udir / "game_profiles").mkdir(exist_ok=True)
    (udir / "logs").mkdir(exist_ok=True)

    # DEV 모드면 원본을 덮어쓰지 않도록 시딩 스킵 (user_data == 프로젝트 루트).
    if is_dev():
        return udir

    res = resource_dir()

    # 기본 config.yaml 시딩 (사용자가 아직 안 만든 경우만).
    src_config = res / "config.yaml"
    dst_config = udir / "config.yaml"
    if src_config.exists() and not dst_config.exists():
        shutil.copy2(src_config, dst_config)

    # personas/ 시딩 — 사용자 커스텀 가능해야 하므로 파일로 복사해둔다.
    # 폴더가 비어있을 때만 번들 기본을 복사 (사용자 편집본은 보존).
    src_personas = res / "personas"
    dst_personas = udir / "personas"
    dst_personas.mkdir(exist_ok=True)
    if src_personas.exists() and not any(dst_personas.glob("*.yaml")):
        for f in src_personas.glob("*.yaml"):
            shutil.copy2(f, dst_personas / f.name)

    # game_profiles/default.yaml 시딩 (있으면).
    src_default_profile = res / "game_profiles" / "default.yaml"
    dst_default_profile = udir / "game_profiles" / "default.yaml"
    if src_default_profile.exists() and not dst_default_profile.exists():
        shutil.copy2(src_default_profile, dst_default_profile)

    return udir


def relaunch_command() -> list[str]:
    """앱을 새 프로세스로 다시 실행하기 위한 커맨드(argv) 리스트.

    - frozen(onedir): exe 자체를 다시 실행.
    - 스크립트: 현재 파이썬으로 `-m src.main` 재실행.
    """
    if is_frozen():
        return [sys.executable]
    return [sys.executable, "-m", "src.main"]


def restart_app() -> None:
    """현재 프로세스를 새 인스턴스로 교체(재시작). 키 저장 후 온보딩에서 호출.

    새 프로세스를 띄운 뒤 호출자가 현재 프로세스를 정리/종료하도록 한다.
    스크립트 모드에선 프로젝트 루트를 cwd 로 둬야 `-m src.main` 이 동작한다.
    """
    import subprocess

    cmd = relaunch_command()
    cwd = None if is_frozen() else str(_PROJECT_ROOT)
    subprocess.Popen(cmd, cwd=cwd, close_fds=True)
