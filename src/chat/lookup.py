"""
정확한 정보 봇(페르소나 accurate: true — 고인물킹)의 웹 검색 답.

봇 전원이 한 번의 호출로 채팅을 만드는 라운드에 검색을 끼우면 라운드 전체가 검색 시간(5~15초)만큼
밀린다. 그래서 검색은 따로 돌린다:
  1. 라운드에서 정확한 정보 봇이 스트리머 질문을 맡으면 그 봇은 "잠만요 찾아볼게요"처럼 짧게만 반응하고
  2. 동시에 검색 호출을 띄워
  3. 답이 오면 그 봇 채팅으로 따로 올린다(실제 시청자가 검색해 보고 답하는 타이밍).
찾은 답은 세션 동안 기억(LookupDesk.facts)해 같은 질문은 다시 검색하지 않고, 그 봇 프롬프트에 넣어 다시 쓴다.
게임과 무관한 질문("저녁 뭐 드셨어요?")은 검색 모델이 SKIP → 아무것도 올리지 않는다.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from difflib import SequenceMatcher
from typing import Callable

from src.chat.flow import split_topics

logger = logging.getLogger("solo_stream.lookup")


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (text or "").lower())


def _name_keys(bot: dict) -> list[str]:
    return [str(k).replace(" ", "") for k in [bot.get("name"), *(bot.get("aliases") or [])] if k]


def _clean_question(text: str, bot: dict) -> str:
    """검색할 질문만 남긴다 — 봇 호명("고인물킹님")·앞 접속사("그리고", "근데") 제거."""
    q = text
    for name in sorted([bot.get("name"), *(bot.get("aliases") or [])], key=lambda x: -len(x or "")):
        if name:
            q = re.sub(re.escape(name) + r"\s*(님|씨|아|야)?[,\s]*", "", q)
    q = re.sub(r"^(그리고|근데|아|그럼|그러면)[,\s]+", "", q.strip())
    return q.strip() or text


# 정규화한 질문이 이만큼 비슷하면 같은 질문("다음엔 어디 가요?" ≈ "다음엔 어디라고 하셨죠?")
SAME_QUESTION = 0.7


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= SAME_QUESTION


class LookupDesk:
    """한 세션의 검색 상태 — 진행 중인 검색, 찾은 답(질문 → 답), 다 끝나 채팅 기록에 넣을 답."""

    def __init__(self, keep: int = 8, min_delay: float = 8.0):
        self._facts: OrderedDict[str, tuple[str, str]] = OrderedDict()  # 정규화 질문 → (질문, 답)
        self._keep = keep
        self._busy: set[str] = set()
        self._done: list[dict] = []
        self._lock = threading.Lock()
        # 라운드의 "잠만요" 채팅이 schedule_chats 딜레이(최대 8초) 뒤에 뜨므로 답이 그보다 먼저 뜨지 않게
        self._min_delay = min_delay

    def facts(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._facts.values())

    def pick(self, batch: list[dict], speech: list[dict] | None) -> tuple[dict, str] | None:
        """이번 라운드에 검색할 (봇, 질문). 정확한 정보 봇이 batch 에 있고 새 질문이 있을 때만.

        그 봇 이름이 나온 질문을 우선, 없으면 가장 최근 질문. 이미 찾았거나 찾는 중인 질문은 건너뛴다."""
        bots = [b for b in batch if b.get("accurate")]
        fresh = [u for u in (speech or []) if not u.get("reacted") and (u.get("text") or "").strip()]
        if not bots or not fresh:
            return None
        questions = [t for t in split_topics(fresh) if t["kind"] == "question"]
        if not questions:
            return None
        with self._lock:
            known = list(self._facts) + list(self._busy)
        for bot in bots:
            keys = _name_keys(bot)
            named = [t for t in questions if any(k in t["text"].replace(" ", "") for k in keys)]
            for t in (named or questions[::-1]):
                q = _clean_question(t["text"], bot)
                if not any(_similar(_norm(q), k) for k in known):
                    return bot, q
        return None

    def start(
        self,
        client,
        bot: dict,
        question: str,
        *,
        game: str,
        screen: str,
        profile: str,
        post: Callable[[dict], None],
    ) -> threading.Thread:
        """검색을 백그라운드로 돌리고, 답이 있으면 post({"name","message"}) 로 채팅창에 올린다."""
        key = _norm(question)
        with self._lock:
            self._busy.add(key)

        def run():
            t0 = time.time()
            answer = ""
            try:
                answer = client.lookup_answer(question, game, bot, screen, profile)
            finally:
                with self._lock:
                    self._busy.discard(key)
            logger.info(f"웹 검색 {bot['name']} ({time.time() - t0:.1f}s) \"{question}\" → {answer or '(SKIP/없음)'}")
            if not answer:
                return
            wait = self._min_delay - (time.time() - t0)
            if wait > 0:
                time.sleep(wait)
            chat = {"name": bot["name"], "message": answer}
            with self._lock:
                self._facts[key] = (question, answer)
                while len(self._facts) > self._keep:
                    self._facts.popitem(last=False)
                self._done.append(chat)
            post(chat)

        th = threading.Thread(target=run, daemon=True)
        th.start()
        return th

    def pop_done(self) -> list[dict]:
        """채팅창에 올라간 검색 답 — ai_process 가 채팅 기록에 넣는다."""
        with self._lock:
            done, self._done = self._done, []
        return done
