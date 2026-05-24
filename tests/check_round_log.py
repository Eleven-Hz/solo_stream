"""라운드 블록 chat_log 출력 포맷 확인."""
import sys, glob, os
from pathlib import Path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.utils.logger import setup_logger, get_chat_logger
from src.main import _write_round_block
from src.ai.model_client import ChatCallDebug

setup_logger("solo_stream")
chat_log = get_chat_logger()

# 세션 헤더
profile_context = "[게임: CrossCode (크로스코드)]\n2D 액션 RPG.\n\n주요 시스템:\n  - Balls (원거리)\n  - Melee (근접)"
chat_log.info(
    "########## 세션 시작 — 게임 프로필 ##########\n"
    "# 게임: CrossCode (크로스코드)\n#\n# " +
    profile_context.replace("\n", "\n# ") +
    "\n########## (이하 라운드 프롬프트에서는 위 프로필 컨텍스트를 생략) ##########"
)

# 라운드 1
debug = ChatCallDebug()
debug.provider = "gemini"
debug.model = "gemini-2.5-flash-lite"
debug.elapsed_s = 1.42
debug.prompt = (
    "당신은 게임 스트리밍 방송의 시청자 봇들입니다.\n\n"
    + profile_context + "\n\n"
    "[현재 게임 상황]\n묘사: ...\n위치: ...\n\n"
    "[봇 목록 - 2명]\n- 겜린이: ...\n- 백수형: ...\n\n"
    "JSON으로만 응답:"
)
debug.raw_response = '[{"name":"겜린이","message":"오 시작이다!"},{"name":"백수형","message":"고고"}]'

batch = [
    {"name": "겜린이", "user_speech_weight": "high"},
    {"name": "백수형"},
]
_write_round_block(
    chat_log,
    round_num=1,
    game_name="CrossCode (크로스코드)",
    game_context="묘사: 메인 메뉴 화면\n위치: 알 수 없음\n상황: 게임 시작 대기",
    vision_elapsed=2.31,
    user_speech=[{"text": "크로스 코드 갑시다", "seconds_ago": 1.5}],
    batch=batch,
    default_weight="high",
    chats=[{"name": "겜린이", "message": "오 시작이다!"}, {"name": "백수형", "message": "고고"}],
    chat_debug=debug,
    profile_context=profile_context,
)

# STT 없는 라운드 2
debug2 = ChatCallDebug()
debug2.provider = "gemini"
debug2.model = "gemini-2.5-flash-lite"
debug2.elapsed_s = 1.20
debug2.prompt = "프롬프트 본문 (게임 프로필 미포함 케이스)"
debug2.raw_response = "[]"
_write_round_block(
    chat_log,
    round_num=2,
    game_name="CrossCode (크로스코드)",
    game_context="묘사: 전투 중\n위치: Autumn's Rise\n상황: 고슴도치 처치",
    vision_elapsed=1.85,
    user_speech=[],
    batch=batch,
    default_weight="high",
    chats=[],
    chat_debug=debug2,
    profile_context=profile_context,
)

files = sorted(glob.glob(str(ROOT / "logs" / "chat_*.log")), key=os.path.getmtime)
latest = files[-1]
print(f"file: {latest}")
print("-" * 60)
print(Path(latest).read_text(encoding="utf-8"))
