import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

from src.utils import paths

# .env.local 은 쓰기 가능한 사용자 데이터 폴더 기준 (DEV 모드면 프로젝트 루트).
# 사용자가 첫 실행에 입력한 키는 user_data_dir()/.env.local 에 저장된다.
load_dotenv(paths.user_data_dir() / ".env.local")
load_dotenv(paths.user_data_dir() / ".env")  # fallback


def load_config(config_path: str | None = None) -> dict:
    # 명시 경로가 없으면: 사용자 config 우선 → 없으면 번들 기본.
    path = Path(config_path) if config_path else paths.resolve_resource("config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
