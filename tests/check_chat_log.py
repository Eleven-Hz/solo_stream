"""채팅 프롬프트 로깅 동작 확인."""
import sys, glob, os
from pathlib import Path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.utils.logger import setup_logger, get_chat_logger
from src.ai.model_client import _log_chat_call

setup_logger("solo_stream")
chat_log = get_chat_logger()
chat_log.info("=== 라운드 #1 [test] ===")
chat_log.info("[게임 상황] 묘사: 테스트 화면")

_log_chat_call(
    provider="gemini",
    model="gemini-2.5-flash-lite",
    prompt="[봇 목록]\n- 겜린이: 반말 / [스트리머 발화 반응] high\n\n반드시 JSON으로 응답:",
    raw_response='[{"name":"겜린이","message":"안녕!"}]',
    parsed=[{"name": "겜린이", "message": "안녕!"}],
)

files = sorted(glob.glob(str(ROOT / "logs" / "chat_*.log")), key=os.path.getmtime)
latest = files[-1]
print(f"latest: {latest}")
print("--- content ---")
print(Path(latest).read_text(encoding="utf-8"))
