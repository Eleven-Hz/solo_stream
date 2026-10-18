import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

from src.utils import paths

# .env.local 은 쓰기 가능한 사용자 데이터 폴더 기준 (DEV 모드면 프로젝트 루트).
# 사용자가 첫 실행에 입력한 키는 user_data_dir()/.env.local 에 저장된다.
load_dotenv(paths.user_data_dir() / ".env.local")
load_dotenv(paths.user_data_dir() / ".env")  # fallback


def _deep_merge(base: dict, override: dict) -> dict:
    """base 에 override 를 재귀 병합. override 값이 우선하되, 양쪽이 dict 인
    키는 깊게 병합한다(번들 기본에만 있는 신규 하위 키가 보존됨).

    base/override 를 변형하지 않고 새 dict 를 반환한다.
    """
    merged = dict(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


_MODEL_SECTIONS = ("ai", "stt")


def _migrate_models(user_cfg: dict, migrations: dict) -> list[str]:
    """사용자 설정에 저장된 퇴역 모델 이름을 번들 ``model_migrations`` 표대로 바꾼다.

    번들 기본값은 업데이트 때 새 모델로 바뀌지만, 사용자가 UI 에서 고른 모델은
    config.local.yaml 에 남아 계속 우선한다 → 서비스가 끝난 모델이면 호출이 실패한다.
    ``ai``/``stt`` 섹션의 ``*model`` 키만 본다. 표의 값이 비어 있으면(null) 사용자 선택을
    지워 번들 기본값을 따르게 한다. 바뀐 항목 설명 목록을 반환(없으면 빈 목록).
    """
    import logging

    changed: list[str] = []
    for section in _MODEL_SECTIONS:
        sec = user_cfg.get(section)
        if not isinstance(sec, dict):
            continue
        for key, val in list(sec.items()):
            if not key.endswith("model") or not isinstance(val, str) or val not in migrations:
                continue
            new = migrations[val]
            if new:
                sec[key] = new
            else:
                del sec[key]
            changed.append(f"{section}.{key}: {val} → {new or '(기본값)'}")
    if changed:
        logging.getLogger("solo_stream.config").info("퇴역 모델 이름 교체: " + ", ".join(changed))
    return changed


def load_config(config_path: str | None = None) -> dict:
    """앱 설정 로드.

    번들 기본 config 위에 사용자 config 를 deep-merge 한다. 이렇게 하면 앱
    업데이트로 **새 설정 키가 추가돼도** 구버전 사용자 config 에 그 키가 없어
    `config["new_key"]` 가 KeyError 로 터지는 일을 막는다(신규 키는 기본값으로
    자동 채워지고, 사용자가 바꾼 값은 그대로 보존).
    """
    # 명시 경로가 주어지면 그 파일만 그대로 사용(테스트/특수 케이스).
    if config_path:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    # 번들 기본(읽기전용 config.yaml)을 베이스로, 사용자 오버라이드
    # (config.local.yaml)를 위에 deep-merge 한다. 사용자가 바꾼 값은 전부
    # config.local.yaml 에만 쌓이므로 앱 업데이트로 번들 기본이 바뀌어도 원복되지
    # 않고, 새로 추가된 기본 키는 자동 반영된다.
    bundled_path = paths.resource_dir() / "config.yaml"
    local_path = paths.user_config_path()  # user_data_dir()/config.local.yaml

    base: dict = {}
    if bundled_path.exists():
        with open(bundled_path, "r", encoding="utf-8") as f:
            base = yaml.safe_load(f) or {}

    if local_path.exists() and local_path.resolve() != bundled_path.resolve():
        with open(local_path, "r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
        if _migrate_models(user_cfg, base.get("model_migrations") or {}):
            paths.save_user_config(user_cfg)
        return _deep_merge(base, user_cfg)

    # 오버라이드 파일이 없으면 베이스(번들 기본) 그대로.
    return base


def get_api_key(config: dict) -> str | None:
    """Gemini API 키 반환. 없으면 None.

    예전엔 키가 없으면 ValueError 를 raise 했지만, 포터블(frozen) 배포에서는
    콘솔이 없어 조용히 죽어버린다. 첫 실행 온보딩에서 키 입력을 안내하기 위해
    None 을 반환하고, 호출부(main)가 graceful degrade 한다."""
    env_name = config["ai"].get("api_key_env", "GEMINI_API_KEY")
    return os.getenv(env_name) or os.getenv("GEMINI_API") or os.getenv("GEMINI_API_KEY")


def get_claude_api_key(config: dict) -> str | None:
    """Claude API 키 반환. 설정되지 않았으면 None 반환 (선택적 사용)."""
    env_name = config["ai"].get("claude_api_key_env", "CLAUDE_API_KEY")
    return os.getenv(env_name) or os.getenv("ANTHROPIC_API_KEY")


def get_openai_api_key(config: dict) -> str | None:
    """OpenAI API 키 반환. 설정되지 않았으면 None 반환 (STT provider=openai 일 때만 필요)."""
    env_name = (config.get("stt") or {}).get("openai_api_key_env", "OPENAI_API_KEY")
    return os.getenv(env_name) or os.getenv("OPENAI_API_KEY")
