"""채팅창 스크롤 동작 단독 테스트"""
import sys
import time
import threading
from pathlib import Path
from multiprocessing import Queue

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ui.overlay import run_overlay


def feed_messages(q):
    time.sleep(1)
    for i in range(30):
        q.put({"name": f"봇{i%5}", "message": f"테스트 메시지 {i}번입니다"})
        time.sleep(0.5)


if __name__ == "__main__":
    q = Queue()
    settings_q = Queue()
    t = threading.Thread(target=feed_messages, args=(q,), daemon=True)
    t.start()

    cfg = {
        "ai": {"vision_provider": "gemini"},
        "ui": {
            "opacity": 0.85,
            "max_messages": 50,
            "font_size": 14,
            "width": 360,
            "height": 500,
            "window_excludes": [],
        },
    }
    run_overlay(cfg, q, settings_q)
