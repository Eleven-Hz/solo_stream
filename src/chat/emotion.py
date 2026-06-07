"""봇 감정 변화 시스템 (Phase 2).

각 봇의 감정을 단일축 점수(-1.0 부정 ~ +1.0 긍정)로 관리하고, 매 라운드
**3개 신호**를 합산해 갱신한다. 갱신된 점수는 5단계 한국어 라벨로 변환되어
채팅 프롬프트의 `감정:` 항목에 주입된다 (기존 YAML 정적값을 대체).

3신호 — 어느 하나가 비어도 시스템이 굴러가도록 **있는 신호만 더한다**:
  1. 채팅 톤   : 직전 라운드 봇 발언에 섞인 긍정/부정 키워드
  2. Vision 이벤트: game_context의 `이벤트:` 줄 (보스 클리어/사망 등) — 없으면 0
  3. STT 발화  : 스트리머 발화의 감정 키워드 ("아 죽었다" / "와 깼다")

설계 의도: 감정 전이를 Vision 이벤트 감지 하나에 걸지 않는다. Vision이
불안정해 `이벤트:`를 못 뱉어도 채팅 톤·STT 발화만으로 감정이 움직인다.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("solo_stream.emotion")

# ── 5단계 라벨 (단일축) ───────────────────────────────────────────────────────
# score 구간 → 라벨. 경계는 [하한, 상한).
_LABEL_BANDS = [
    (0.55, "흥분"),
    (0.20, "신남"),
    (-0.20, "평온"),
    (-0.55, "실망"),
    (-1.01, "짜증"),
]


def score_to_label(score: float) -> str:
    """감정 점수(-1~+1)를 5단계 한국어 라벨로 변환."""
    for lower, label in _LABEL_BANDS:
        if score >= lower:
            return label
    return "짜증"


# YAML에 적힌 정적 base_emotion 문자열 → 시작 점수.
# 봇 페르소나의 고유 감정을 점수축의 출발점으로 옮긴다 (없으면 0=평온).
_BASE_EMOTION_SCORE = {
    "흥분": 0.6,
    "신남": 0.35,
    "평온": 0.0,
    "긴장": -0.1,
    "실망": -0.35,
    "짜증": -0.5,
    "우울": -0.5,
}


def _base_score(base_emotion: str) -> float:
    return _BASE_EMOTION_SCORE.get((base_emotion or "").strip(), 0.0)


# ── 감정 키워드 사전 ──────────────────────────────────────────────────────────
# 채팅/발화 텍스트에서 찾을 긍정·부정 단서. 부분 문자열로 매칭한다.
_POSITIVE_WORDS = [
    "미쳤", "지렸", "깼다", "클리어", "이겼", "잘한", "잘하", "굿", "개잘",
    "역시", "최고", "오졌", "쩐다", "쩔", "와우", "대박", "성공", "ㄱㅇㄷ",
]
_NEGATIVE_WORDS = [
    "죽었", "death", "아깝", "또 죽", "망했", "졌다", "실패", "에휴", "하...",
    "큰일", "위기", "도망", "ㅠㅠ", "ㅜㅜ", "헉", "위험", "death",
]


def _keyword_valence(text: str) -> float:
    """텍스트의 긍정/부정 키워드 수 차이를 -1~+1 근처 값으로. 키워드 없으면 0."""
    if not text:
        return 0.0
    pos = sum(1 for w in _POSITIVE_WORDS if w in text)
    neg = sum(1 for w in _NEGATIVE_WORDS if w in text)
    if pos == 0 and neg == 0:
        return 0.0
    raw = pos - neg
    # 과도하게 누적되지 않도록 단위당 0.25, [-1,1] 클램프
    return max(-1.0, min(1.0, raw * 0.25))


# ── Vision 이벤트 → 신호 ──────────────────────────────────────────────────────
# main.py가 파싱한 `이벤트:` 줄 텍스트를 받아 valence로 변환. 빈 문자열이면 0.
_EVENT_POSITIVE = ["보스 처치", "보스 클리어", "클리어", "격파", "레벨업", "획득", "달성"]
_EVENT_NEGATIVE = ["사망", "죽음", "HP 위기", "위기", "전멸", "패배", "보스 등장"]


def event_valence(event_text: str) -> float:
    """Vision `이벤트:` 줄을 valence로. 키워드 없으면 0 (= 이벤트 신호 없음)."""
    if not event_text:
        return 0.0
    t = event_text.strip()
    pos = any(w in t for w in _EVENT_POSITIVE)
    neg = any(w in t for w in _EVENT_NEGATIVE)
    if pos and not neg:
        return 0.5
    if neg and not pos:
        return -0.5
    return 0.0


# ── 봇별 감도 (relationship 기반) ─────────────────────────────────────────────
# 같은 신호라도 봇 성향에 따라 반응 폭이 다르다.
# (pos_gain, neg_gain) — 긍정/부정 신호에 곱하는 감도.
_SENSITIVITY_BY_RELATIONSHIP = {
    "안티": (0.6, 1.4),       # 안티: 긍정엔 시큰둥, 부정(실수)엔 격하게
    "트롤러": (0.7, 1.2),
    "찐팬": (1.4, 0.7),       # 찐팬: 긍정에 격하게, 부정엔 감싸줌
    "뉴비팬": (1.3, 0.8),
    "팬": (1.1, 0.9),
}
_DEFAULT_SENSITIVITY = (1.0, 1.0)


def _sensitivity(relationship: str) -> tuple[float, float]:
    return _SENSITIVITY_BY_RELATIONSHIP.get((relationship or "").strip(), _DEFAULT_SENSITIVITY)


class EmotionState:
    """한 봇의 동적 감정 상태. score를 신호로 갱신하고 라벨로 노출한다."""

    __slots__ = ("name", "base", "score", "pos_gain", "neg_gain")

    def __init__(self, name: str, base_emotion: str = "평온", relationship: str = ""):
        self.name = name
        self.base = _base_score(base_emotion)
        self.score = self.base
        self.pos_gain, self.neg_gain = _sensitivity(relationship)

    def apply(self, delta: float, max_step: float = 0.4) -> None:
        """신호 delta를 감도 적용해 점수에 반영하고 [-1,1]로 클램프.

        감도 적용 뒤 한 라운드 변화폭을 max_step으로 제한해, 평온→흥분 같은
        급격한 직행을 막고 최소 2라운드에 걸쳐 전이되게 한다."""
        if delta > 0:
            delta *= self.pos_gain
        elif delta < 0:
            delta *= self.neg_gain
        delta = max(-max_step, min(max_step, delta))
        self.score = max(-1.0, min(1.0, self.score + delta))

    def decay(self, rate: float = 0.25) -> None:
        """매 라운드 base 쪽으로 끌어당겨 평소 감정으로 복귀시킨다.
        rate=0.25면 base와의 거리를 25%씩 좁힌다."""
        self.score += (self.base - self.score) * rate

    @property
    def label(self) -> str:
        return score_to_label(self.score)


# ── 라운드 단위 신호 합산 ─────────────────────────────────────────────────────

def compute_round_delta(
    bot_name: str,
    own_messages: list[str],
    event_text: str,
    user_speech_texts: list[str],
    *,
    chat_weight: float = 1.0,
    event_weight: float = 1.0,
    speech_weight: float = 1.0,
) -> float:
    """한 봇의 이번 라운드 감정 delta = 3신호 가중 합.

    - own_messages: 직전 라운드에서 이 봇(또는 채널 전체)이 친 채팅 텍스트
    - event_text: Vision `이벤트:` 줄 (없으면 "")
    - user_speech_texts: 이번 lookback의 스트리머 발화 텍스트들

    각 신호는 비어 있으면 0을 기여한다 → 한 신호가 죽어도 나머지로 갱신.
    """
    chat_sig = _keyword_valence(" ".join(own_messages)) if own_messages else 0.0
    event_sig = event_valence(event_text)
    speech_sig = _keyword_valence(" ".join(user_speech_texts)) if user_speech_texts else 0.0

    delta = (
        chat_sig * chat_weight
        + event_sig * event_weight
        + speech_sig * speech_weight
    )
    # 한 라운드 변화 폭 제한 (급변 방지)
    return max(-0.6, min(0.6, delta))


def update_emotions(
    states: dict[str, EmotionState],
    bot_pool: list[dict],
    chat_history: list[dict],
    event_text: str,
    user_speech_texts: list[str],
) -> dict[str, str]:
    """봇 풀 전체의 감정 상태를 이번 라운드 신호로 갱신하고 {name: label} 반환.

    - states: 봇별 EmotionState (없으면 base_emotion으로 생성)
    - chat_history: 채널 전체 최근 채팅 (톤 신호원). 봇별 자기 발언 우선,
      없으면 채널 분위기를 공유 신호로 사용.
    """
    # 채널 전체의 최근 발언 (공유 분위기)
    recent_all = [c.get("message", "") for c in chat_history[-8:]]

    labels: dict[str, str] = {}
    for b in bot_pool:
        name = b["name"]
        st = states.get(name)
        if st is None:
            st = EmotionState(name, b.get("emotion", "평온"), b.get("relationship", ""))
            states[name] = st

        # 자기 직전 발언이 있으면 그것 우선, 없으면 채널 분위기
        own = [c.get("message", "") for c in chat_history if c.get("name") == name][-3:]
        chat_src = own if own else recent_all

        st.decay()
        delta = compute_round_delta(name, chat_src, event_text, user_speech_texts)
        st.apply(delta)
        labels[name] = st.label

    return labels
