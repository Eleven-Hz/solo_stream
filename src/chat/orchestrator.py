"""
봇별 타이밍 스케줄링 오케스트레이터.
AI Process에서 받은 채팅 목록을 랜덤 딜레이를 두고 UI 큐에 하나씩 전달한다.
"""
import logging
import random
import time
from multiprocessing import Queue

from src.export.websocket import broadcast

logger = logging.getLogger("solo_stream.orchestrator")

_FREQUENCY_CHANCE = {
    "high": 1.0,
    "medium": 0.7,
    "low": 0.4,
}


def schedule_chats(
    chats: list[dict],
    bots: list[dict],
    ui_queue: Queue,
    delay_min: float = 1.0,
    delay_max: float = 8.0,
    mentioned_names: list[str] | None = None,
    skip_relief: float = 0.0,
    round_id: int | None = None,
):
    """
    chats를 랜덤 딜레이로 ui_queue에 넣는다.
    봇의 frequency에 따라 일부 메시지는 스킵될 수 있다.
    단, mentioned_names(스트리머가 이름을 부른 봇)는 frequency 스킵에서 면제한다 —
    호명에 대한 답변은 반드시 화면에 떠야 하므로. (호명 봇이 batch에 강제 포함되어
    LLM이 답을 생성해도, frequency 스킵에 걸려 UI에서 누락되면 "불러도 답이 없다"가 됨)
    별도 스레드에서 호출된다.

    `delay_min`/`delay_max`/`skip_relief`는 라운드마다 흥분도(hype)에 따라 달라진다
    (main.py의 build_hype_profile). 흥분 라운드는 좁은 delay로 빠르게,
    skip_relief>0으로 저빈도 봇도 덜 잘려 채팅이 우르르 올라온다.
      - skip_relief: frequency 통과 확률에 더하는 보정. 0=평소, 1=스킵 면제.
    `round_id` 가 있으면 마지막 채팅을 띄운 뒤 그 라운드의 '입력 중' 표시를 끄는 메시지를 보낸다.
    """
    try:
        _schedule(chats, bots, ui_queue, delay_min, delay_max, mentioned_names, skip_relief)
    finally:
        if round_id is not None:
            ui_queue.put({"system": True, "type": "typing", "round": round_id, "on": False})


def _schedule(chats, bots, ui_queue, delay_min, delay_max, mentioned_names, skip_relief):
    bot_freq = {b["name"]: b.get("frequency", "medium") for b in bots}
    mentioned = set(mentioned_names or [])
    relief = max(0.0, min(1.0, skip_relief))

    shuffled = chats[:]
    random.shuffle(shuffled)

    for chat in shuffled:
        name = chat.get("name", "")

        if name not in mentioned:
            freq = bot_freq.get(name, "medium")
            chance = _FREQUENCY_CHANCE.get(freq, 0.7)
            # 흥분 라운드는 스킵 확률을 완화: 통과 확률을 relief만큼 1.0 쪽으로 끌어올린다.
            chance = chance + (1.0 - chance) * relief
            if random.random() > chance:
                logger.debug(f"[스킵] {name} (frequency={freq}, relief={relief:.2f})")
                continue

        delay = random.uniform(delay_min, delay_max)
        time.sleep(delay)
        ui_queue.put(chat)
        # 외부 소비자(project_ns 등)에게도 같은 채팅을 내보낸다. 기본 비활성이며
        # 실패는 삼켜서 오버레이 흐름을 막지 않는다 (src/export/websocket.py).
        broadcast(chat)
        logger.debug(f"[채팅 전달] {name}: {chat.get('message', '')}")
