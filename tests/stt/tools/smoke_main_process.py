"""src/main.py 의 stt_process 가 독립 프로세스로 띄워졌을 때 동작 점검.

사용:
  .\venv\Scripts\python.exe tests\stt\tools\smoke_main_process.py [duration_seconds]

duration_seconds 동안 chat_active=True 신호를 보내 마이크 캡처/STT를 켰다가,
들어온 발화 이벤트를 화면에 출력하고 종료한다.
"""
from __future__ import annotations

import multiprocessing
import queue as queue_mod
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.utils.config_loader import load_config, get_api_key


def main():
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0

    config = load_config()
    # 강제로 stt enabled
    config.setdefault("stt", {})["enabled"] = True
    api_key = get_api_key(config)

    from src.main import stt_process  # 늦은 import (multiprocessing fork-safety)

    stt_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=200)
    settings_queue: multiprocessing.Queue = multiprocessing.Queue()

    proc = multiprocessing.Process(
        target=stt_process,
        args=(stt_queue, settings_queue, config, api_key),
        name="STTProcess",
        daemon=True,
    )
    proc.start()
    print(f"STTProcess 시작 PID={proc.pid}, {duration:.0f}s 동안 녹음 -> STT")

    # chat_active=True 신호 전송
    settings_queue.put({"chat_active": True})

    t0 = time.time()
    received: list[dict] = []
    while time.time() - t0 < duration:
        try:
            ev = stt_queue.get(timeout=0.3)
            received.append(ev)
            elapsed = time.time() - t0
            print(f"  [+{elapsed:5.1f}s] ({ev['duration']:.1f}s) {ev['text']}")
        except queue_mod.Empty:
            pass

    print("정지 신호 전송 (chat_active=False)")
    settings_queue.put({"chat_active": False})
    time.sleep(2)

    proc.terminate()
    proc.join(timeout=5)
    print(f"종료. 수신 발화 {len(received)}개")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
