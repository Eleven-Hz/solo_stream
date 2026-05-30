import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

# 프로젝트 루트 기준으로 .env.local 로드
_ROOT = Path(__file__).parent.parent.parent
load_dotenv(_ROOT / ".env.local")
load_dotenv(_ROOT / ".env")  # fallback


def load_config(config_path: str | None = None) -> dict:
    path = Path(config_path) if config_path else _ROOT / "config.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_api_key(config: dict) -> str:
    env_name = config["ai"].get("api_key_env", "GEMINI_API_KEY")
    key = os.getenv(env_name) or os.getenv("GEMINI_API") or os.getenv("GEMINI_API_KEY")
    if not key:
        raise ValueError(
            f"API 키를 찾을 수 없습니다. .env.local에 {env_name}=your_key 형태로 설정하세요."
        )
    return key


def get_claude_api_key(config: dict) -> str | None:
    """Claude API 키 반환. 설정되지 않았으면 None 반환 (선택적 사용)."""
    env_name = config["ai"].get("claude_api_key_env", "CLAUDE_API_KEY")
    return os.getenv(env_name) or os.getenv("ANTHROPIC_API_KEY")


def get_openai_api_key(config: dict) -> str | None:
    """OpenAI API 키 반환. 설정되지 않았으면 None 반환 (STT provider=openai 일 때만 필요)."""
    env_name = (config.get("stt") or {}).get("openai_api_key_env", "OPENAI_API_KEY")
    return os.getenv(env_name) or os.getenv("OPENAI_API_KEY")
