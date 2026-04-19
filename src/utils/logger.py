import logging
import sys
from datetime import datetime
from pathlib import Path

_LOG_DIR = Path(__file__).parent.parent.parent / "logs"
_CHAT_LOG_PATH = None  # 세션 시작 시 초기화


def setup_logger(name: str = "solo_stream", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
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
    if name == "solo_stream":
        _LOG_DIR.mkdir(exist_ok=True)
        session_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = _LOG_DIR / f"session_{session_time}.log"

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # 하위 로거들도 파일에 기록되도록 propagate 활성화
        logger.propagate = False
        logger.info(f"로그 파일: {log_path}")

    return logger


def get_chat_logger() -> logging.Logger:
    """채팅 전용 로거. logs/chat_YYYYMMDD_HHMMSS.log 에 기록."""
    name = "solo_stream.chat_log"
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = True  # 부모(solo_stream)의 파일 핸들러로도 전달

    _LOG_DIR.mkdir(exist_ok=True)
    session_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    chat_log_path = _LOG_DIR / f"chat_{session_time}.log"

    formatter = logging.Formatter(
        "[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    file_handler = logging.FileHandler(chat_log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 콘솔에는 채팅 로그 출력 안 함
    logger.propagate = False

    logging.getLogger("solo_stream").info(f"채팅 로그 파일: {chat_log_path}")
    return logger
