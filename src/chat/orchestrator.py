"""
봇별 타이밍 스케줄링 오케스트레이터.
AI Process에서 받은 채팅 목록을 랜덤 딜레이를 두고 UI 큐에 하나씩 전달한다.
"""
import logging
import random
import time
from multiprocessing import Queue

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
):
    """
    chats를 랜덤 딜레이로 ui_queue에 넣는다.
    봇의 frequency에 따라 일부 메시지는 스킵될 수 있다.
    단, mentioned_names(스트리머가 이름을 부른 봇)는 frequency 스킵에서 면제한다 —
    호명에 대한 답변은 반드시 화면에 떠야 하므로. (호명 봇이 batch에 강제 포함되어
    LLM이 답을 생성해도, frequency 스킵에 걸려 UI에서 누락되면 "불러도 답이 없다"가 됨)
    별도 스레드에서 호출된다.
    """
    bot_freq = {b["name"]: b.get("frequency", "medium") for b in bots}
    mentioned = set(mentioned_names or [])

    shuffled = chats[:]
    random.shuffle(shuffled)

    for chat in shuffled:
        name = chat.get("name", "")

        if name not in mentioned:
            freq = bot_freq.get(name, "medium")
            chance = _FREQUENCY_CHANCE.get(freq, 0.7)
            if random.random() > chance:
                logger.debug(f"[스킵] {name} (frequency={freq})")
                continue

        delay = random.uniform(delay_min, delay_max)
        time.sleep(delay)
        ui_queue.put(chat)
        logger.debug(f"[채팅 전달] {name}: {chat.get('message', '')}")
