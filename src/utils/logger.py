import logging
import time
from datetime import datetime
from pathlib import Path

from src.utils import paths

# 로그는 쓰기 가능한 사용자 데이터 폴더에 기록 (DEV 모드면 프로젝트 루트의 logs/).
_LOG_DIR = paths.user_data_dir() / "logs"
# 세션/채팅 로그는 '하루 1개' 파일에 append 한다 (session_YYYYMMDD.log).
# 같은 날 실행되는 모든 프로세스(메인/AI/STT)가 같은 파일에 append.
_RETENTION_DAYS = 7


def _session_log_path() -> Path:
    return _LOG_DIR / f"session_{datetime.now():%Y%m%d}.log"


def _chat_log_path() -> Path:
    return _LOG_DIR / f"chat_{datetime.now():%Y%m%d}.log"


def prune_old_logs(days: int = _RETENTION_DAYS) -> int:
    """logs/ 의 session_*.log / chat_*.log 중 마지막 수정이 days 일 지난 것을 삭제.

    파일명 형식과 무관하게 mtime 기준이라 과거 timestamp 로그(레거시 포함)도 정리된다.
    메인 시작 시 1회 호출한다. 삭제한 파일 수를 반환.
    """
    if not _LOG_DIR.exists():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for pattern in ("session_*.log", "chat_*.log"):
        for p in _LOG_DIR.glob(pattern):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
            except OSError:
                pass
    return removed


def save_to_file_enabled() -> bool:
    """config.yaml 의 logging.save_to_file (기본 True). 파일을 직접 읽어
    config_loader 의존을 피한다 (logger 는 import 시점에 쓰여 순환을 피해야 함)."""
    try:
        import yaml
        cfg_path = paths.resolve_resource("config.yaml")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return bool((cfg.get("logging") or {}).get("save_to_file", True))
    except Exception:
        return True


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

    # 콘솔 핸들러 (stdout) — 무콘솔(pythonw) 실행 시 stdout 이 None 일 수 있어 가드
    import sys
    if sys.stdout is not None:
        # 한국어 Windows 콘솔은 cp949 라 '—' 같은 문자를 못 써 UnicodeEncodeError 가 난다.
        # 인코딩은 그대로 두고(한글은 정상 표시) 못 쓰는 문자만 '?' 로 바꾼다. 파일 로그는 UTF-8 이라 무관.
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(errors="replace")
            except Exception:
                pass
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(level)
        console.setFormatter(formatter)
        logger.addHandler(console)

    # 파일 핸들러 (루트 로거에만 추가해서 중복 방지). save_to_file=false 면 생략.
    # 여러 프로세스(메인/AI/STT)가 같은 일별 파일에 append 한다.
    session_path = _session_log_path()
    if save_to_file_enabled():
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(session_path, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # 자식 로거들은 propagate 를 통해 이 핸들러를 공유한다.
    # 루트 로거 자신은 Python 루트 logger 로 전파될 필요가 없으므로 False.
    logger.propagate = False
    if save_to_file_enabled():
        logger.info(f"로그 파일: {session_path} (append)")

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
    """채팅 전용 로거. logs/chat.log 에 append 기록 (save_to_file=false 면 비활성)."""
    name = "solo_stream.chat_log"
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    # 부모(solo_stream) 핸들러로 올라가지 않도록 propagate=False
    logger.propagate = False

    if not save_to_file_enabled():
        # 파일 저장 비활성 — 핸들러 없이 메시지를 버린다 (lastResort 로 새지 않도록 Null).
        logger.addHandler(logging.NullHandler())
        return logger

    _LOG_DIR.mkdir(exist_ok=True)
    chat_path = _chat_log_path()
    file_handler = logging.FileHandler(chat_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(_BlockFormatter(datefmt="%H:%M:%S"))
    logger.addHandler(file_handler)

    logging.getLogger("solo_stream").info(f"채팅 로그 파일: {chat_path} (append)")
    return logger
