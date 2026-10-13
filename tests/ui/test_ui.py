"""채팅창 스크롤 동작 단독 테스트"""
import sys
import time
import threading
from multiprocessing import Queue
from PyQt6.QtWidgets import QApplication
from src.ui.overlay import run_overlay

def feed_messages(q):
    time.sleep(1)
    for i in range(30):
        q.put({"name": f"봇{i%5}", "message": f"테스트 메시지 {i}번입니다"})
        time.sleep(0.5)

if __name__ == "__main__":
    sys.path.insert(0, ".")
    q = Queue()
    t = threading.Thread(target=feed_messages, args=(q,), daemon=True)
    t.start()

    cfg = {
        "ui": {
            "transparency": 15,
            "max_messages": 50,
            "font_size": 14,
            "width": 380,
            "height": 560,
        }
    }
    run_overlay(cfg, q, Queue())
