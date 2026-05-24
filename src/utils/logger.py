import logging
import sys
from datetime import datetime
from pathlib import Path

_LOG_DIR = Path(__file__).parent.parent.parent / "logs"
_CHAT_LOG_PATH = None  # 세션 시작 시 초기화


def setup_logger(name: str = "solo_stream", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)

    # 자식 로거(예: "solo_stream.stt")는 핸들러를 따로 붙이지 않고,
    # propagate 를 통해 부모("solo_stream")의 콘솔/파일 핸들러를 공유한다.
    # 그렇지 않으면 자식이 콘솔 핸들러 + 부모 콘솔 핸들러 양쪽으로 중복 출력된다.
    if name != "solo_stream":
        logger.setLevel(level)
        return logger

    if logger.handlers:
        return logger

    logger.setLevel(level)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # 콘솔 핸들러
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # 파일 핸들러 (루트 로거에만 추가해서 중복 방지)
    _LOG_DIR.mkdir(exist_ok=True)
    session_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = _LOG_DIR / f"session_{session_time}.log"

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 자식 로거들은 propagate 를 통해 이 핸들러를 공유한다.
    # 루트 로거 자신은 Python 루트 logger 로 전파될 필요가 없으므로 False.
    logger.propagate = False
    logger.info(f"로그 파일: {log_path}")

    return logger


class _BlockFormatter(logging.Formatter):
    """멀티라인 메시지를 줄 첫머리 타임스탬프 한 번만 찍어 출력.

    `\n` 으로 줄바꿈된 메시지를 받으면 첫 줄에만 timestamp 를 찍고,
    이어지는 줄은 들여쓰기 없이 그대로 출력한다.
    한 라운드 안에서 vision / STT / 프롬프트 / 응답이 시각적으로
    한 블록으로 보이도록 하기 위함.
    """
    def format(self, record):
        ts = self.formatTime(record, self.datefmt)
        msg = record.getMessage()
        lines = msg.splitlines() or [""]
        out = [f"[{ts}] {lines[0]}"]
        out.extend(lines[1:])
        return "\n".join(out)


def get_chat_logger() -> logging.Logger:
    """채팅 전용 로거. logs/chat_YYYYMMDD_HHMMSS.log 에 기록."""
    name = "solo_stream.chat_log"
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    # 부모(solo_stream) 핸들러로 올라가지 않도록 propagate=False
    logger.propagate = False

    _LOG_DIR.mkdir(exist_ok=True)
    session_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    chat_log_path = _LOG_DIR / f"chat_{session_time}.log"

    file_handler = logging.FileHandler(chat_log_path, encoding="utf-8")
    file_handler.setFormatter(_BlockFormatter(datefmt="%H:%M:%S"))
    logger.addHandler(file_handler)

    logging.getLogger("solo_stream").info(f"채팅 로그 파일: {chat_log_path}")
    return logger
