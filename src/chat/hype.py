"""채팅 타이밍 고도화 — hype(흥분도) + valence(분위기 방향) 2축 (Phase 2).

감정 시스템(`emotion.py`)이 신호를 "봇 감정 라벨"로 흡수한다면, 이 모듈은 같은
입력(스트리머 발화 + 직전 봇 채팅 + Vision `이벤트:` 줄)에서 **두 축**을 뽑아 채팅 타이밍과 물량을 바꾼다.
말없이 플레이해도 보스 등장·사망 같은 큰 장면에서 채팅이 몰리고(`event_hype`), 몇 라운드에
걸쳐 잦아든다(`next_event_glow` — 잔불).

  - hype   (0.0~1.0): 감정의 격렬함. "개웃기네/너무 슬프다" 같은 강조어, 반복
                       ㅋㅋㅋㅋ/ㅠㅠㅠㅠ, 느낌표가 많을수록 ↑.
  - valence(-1~+1)  : 긍/부정 방향. hype가 같아도 "빵 터짐"이냐 "짠함"이냐를 가른다.

hype가 높은 라운드는:
  - 채팅이 **빠르게**(delay 범위 축소) + **많이**(batch 증량 + frequency 스킵 완화) 뜨고,
  - LLM에는 valence에 맞춘 mood_hint("지금 빵 터지는 분위기 — ㅋㅋㅋ 위주")가 주입된다.

설계 의도: valence가 타이밍을 직접 몰지 않게 한다. 슬픔(부정)도 강하면 hype가 높아
ㅠㅠ가 우르르 올라와야 하므로, "빠르게/많이"는 hype 한 축만 본다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── 강조어: hype를 끌어올리는 정도 부사/표현 ────────────────────────────────────
# 방향(긍/부정) 무관하게 "세게 느낀다"는 신호. 부분 문자열 매칭.
_INTENSIFIERS = [
    "너무", "개", "존나", "졸라", "진짜", "겁나", "엄청", "완전", "레전드",
    "미친", "미쳤", "역대급", "대박", "오졌", "지렸", "쩐다", "쩔",
]

# ── 방향 키워드 (valence) — emotion.py와 결이 같되 타이밍용으로 독립 관리 ──────
_POSITIVE_WORDS = [
    "재밌", "꿀잼", "웃기", "웃겨", "지렸", "미쳤", "깼다", "클리어", "이겼",
    "잘한", "굿", "역시", "최고", "오졌", "쩐다", "대박", "성공", "ㄱㅇㄷ", "갓겜",
    "좋았", "좋아", "됐다", "나이스", "오케", "굿굿",
]
_NEGATIVE_WORDS = [
    "슬프", "슬퍼", "짠하", "죽었", "아깝", "망했", "졌다", "실패", "에휴",
    "큰일", "위기", "도망", "무서", "징그", "안돼", "안 돼", "어우",
]

# ── 반복 웃음/울음 패턴 — 길수록 hype + 방향 신호 ─────────────────────────────
_LAUGH_RE = re.compile(r"[ㅋㅎ]{2,}")     # ㅋㅋ, ㅎㅎ, ㅋㅎㅋ…
_CRY_RE = re.compile(r"[ㅜㅠ]{2,}")        # ㅠㅠ, ㅜㅜ…
_BANG_RE = re.compile(r"[!?]{1,}")          # !, !!, ?!

# 발화 valence가 이 값 이상이면 "명확한 방향"으로 보고 봇 채팅 메아리보다 우선한다.
# _scan_text 에서 방향 키워드 1개=±0.5, 웃음/울음=±1 이므로, 0.5는 "긍/부정 단서가
# 발화에 하나라도 분명히 있으면" 그 방향을 확정한다는 뜻.
SPEECH_VAL_DECISIVE = 0.5


@dataclass
class HypeSignal:
    """한 라운드의 종합 흥분 신호."""
    hype: float       # 0.0 ~ 1.0
    valence: float    # -1.0 ~ +1.0
    laugh: int        # 감지된 웃음 토큰 수 (mood_hint 판정 보조)
    cry: int          # 감지된 울음 토큰 수
    event: float = 0.0  # hype 중 화면 이벤트(잔불 포함) 몫 — 로그·임계값 판정용


# ── Vision 이벤트 → hype ──────────────────────────────────────────────────────
# 실제 리플레이의 `이벤트:` 줄은 대부분 사소하다("금속 바늘 1개 획득 알림", "크레딧 731 획득",
# "돌아올 수 없다는 경고" — 2026-10 CrossCode 샘플 기준 라운드의 약 10%에 이벤트 줄, 거의 전부 이런 것).
# 그래서 아무 이벤트나 올리지 않고 **큰 장면만 등급**으로 올린다. 획득·확인 창은 0.
# (hype, valence) — valence 는 emotion.event_valence 와 달리 '보스 등장'·'사망'을 부정으로 보지 않는다:
# 긴장은 들썩임이지 슬픔이 아니라서, 부정으로 두면 mood_hint 가 ㅠㅠ 쪽으로만 나간다.
_BOSS_WORDS = ["보스"]
_BOSS_DOWN = ["처치", "격파", "클리어", "쓰러", "잡았", "승리"]
_DEATH_WORDS = ["사망", "죽었", "죽음", "전멸", "패배", "게임 오버", "게임오버", "GAME OVER", "Game Over"]
_MID_EVENTS: list[tuple[list[str], float, float]] = [
    (["HP 위기", "체력 위기", "체력이 낮", "체력이 거의", "빈사"], 0.5, -0.4),
    (["레벨업", "레벨 업", "레벨이 올"], 0.5, 0.5),
    (["클리어", "승리", "달성"], 0.45, 0.5),
    (["큰 피해", "치명타"], 0.25, 0.0),   # 전투 중 흔함 — 혼자선 임계값 아래, 다른 신호와 합쳐야 발동
]
_NO_EVENT = ("없음", "없다", "없습니다", "-", "해당 없음")


def event_hype(event_text: str) -> tuple[float, float]:
    """Vision `이벤트:` 줄 → (hype, valence). 큰 장면이 아니면 (0, 0)."""
    t = (event_text or "").strip()
    if not t or t.startswith(_NO_EVENT):
        return 0.0, 0.0
    if any(w in t for w in _DEATH_WORDS):
        # 방향 없음: 실제 방송 채팅은 사망에 놀림(ㅋㅋ 또 죽었어)과 안타까움(ㅠㅠ)이 섞인다.
        # 어느 쪽일지는 봇 성향·감정(emotion.py — 안티는 부정에 민감)에 맡긴다.
        return 0.8, 0.0
    if any(w in t for w in _BOSS_WORDS):
        if any(w in t for w in _BOSS_DOWN):
            return 0.85, 0.9
        return 0.7, 0.0          # 보스 등장·보스전 진행 — 방향 없는 들썩임
    for words, h, v in _MID_EVENTS:
        if any(w in t for w in words):
            return h, v
    return 0.0, 0.0


# 잔불: 이벤트 다음 화면 분석 라운드마다 이만큼 곱해 줄어든다. 0.8 → 0.44 → 0.24 → 0.13
# (15초 주기면 터진 뒤 15~30초 동안 이어지다 평소로).
EVENT_GLOW_DECAY = 0.55


def next_event_glow(prev: tuple[float, float], event: tuple[float, float], *,
                    decay: float = EVENT_GLOW_DECAY) -> tuple[float, float]:
    """화면 분석 라운드마다 부른다 — (hype, valence) 중 새 이벤트와 줄어든 이전 잔불 중 hype 가 큰 쪽.

    발화 즉시 반응 라운드는 화면 분석 사이에 끼는 추가 라운드라 부르지 않는다(잔불을 두 번 줄이지 않게).
    hype 0.05 아래는 (0, 0)."""
    faded = (prev[0] * decay, prev[1] * decay)
    glow = event if event[0] >= faded[0] else faded
    return glow if glow[0] >= 0.05 else (0.0, 0.0)


def _scan_text(text: str) -> tuple[float, float, int, int]:
    """텍스트 한 덩어리에서 (hype_raw, valence_raw, laugh_len, cry_len) 추출.

    hype_raw / valence_raw 는 정규화 전 누적값. 합산 후 caller가 정규화한다.
    laugh_len / cry_len 은 반복 토큰의 총 길이(ㅋ 개수 등)로, 길수록 격렬.
    """
    if not text:
        return 0.0, 0.0, 0, 0

    pos = sum(1 for w in _POSITIVE_WORDS if w in text)
    neg = sum(1 for w in _NEGATIVE_WORDS if w in text)
    intens = sum(1 for w in _INTENSIFIERS if w in text)

    laugh_len = sum(len(m.group()) for m in _LAUGH_RE.finditer(text))
    cry_len = sum(len(m.group()) for m in _CRY_RE.finditer(text))
    bang = sum(len(m.group()) for m in _BANG_RE.finditer(text))

    # hype: 강조어 + 방향 키워드 등장 + 반복 토큰 길이 + 느낌표. (방향 무관)
    hype_raw = (
        intens * 0.30
        + (pos + neg) * 0.20
        + min(laugh_len, 8) * 0.10
        + min(cry_len, 8) * 0.10
        + min(bang, 4) * 0.08
    )
    # valence: 긍정 - 부정. 웃음은 +, 울음은 -로도 가산.
    valence_raw = (
        (pos - neg) * 0.5
        + (1 if laugh_len >= 2 else 0)
        - (1 if cry_len >= 2 else 0)
    )
    return hype_raw, valence_raw, laugh_len, cry_len


def compute_hype(
    user_speech_texts: list[str] | None,
    own_chat_texts: list[str] | None,
    *,
    speech_weight: float = 1.0,
    chat_weight: float = 0.5,
    chat_echo_damp: float = 1.0,
    event: tuple[float, float] = (0.0, 0.0),
    event_weight: float = 1.0,
) -> HypeSignal:
    """스트리머 발화 + 직전 봇 채팅 + 화면 이벤트에서 hype/valence 산출.

    - 발화(speech_weight)를 채팅 톤(chat_weight)보다 무겁게 본다: 스트리머의
      실제 리액션이 분위기의 1차 소스이고, 봇 채팅은 보조 신호.
    - `event`: (hype, valence) — `event_hype()` 결과에 잔불을 적용한 값. 발화처럼 감쇠하지
      않는다(화면에 실제로 일어난 일). `event_weight` 0 이면 끔.
    - 어느 하나가 비어도 나머지로 동작.

    `chat_echo_damp` (0~1): 봇 채팅 신호에 추가로 곱하는 감쇠 계수. 직전 라운드가
    흥분 발동이었으면 봇들이 mood_hint 때문에 ㅋㅋ/ㅠㅠ를 대량 생성하는데, 그게
    이번 라운드 채팅 신호로 되먹임되면 부정/긍정 분위기가 봇 메아리만으로 눈덩이처럼
    커진다(자가발전 루프). 직전이 발동이면 caller가 이 값을 낮춰(<1) 메아리를 끊는다.
    발화(STT) 신호는 감쇠하지 않는다 — 스트리머의 실제 리액션은 항상 신뢰.
    """
    s_hype, s_val, s_laugh, s_cry = _scan_text(" ".join(user_speech_texts or []))
    c_hype, c_val, c_laugh, c_cry = _scan_text(" ".join(own_chat_texts or []))

    damp = max(0.0, min(1.0, chat_echo_damp))
    eff_chat_weight = chat_weight * damp

    e_hype = max(0.0, event[0]) * max(0.0, event_weight)
    e_val = event[1] * max(0.0, event_weight)

    hype_raw = s_hype * speech_weight + c_hype * eff_chat_weight + e_hype
    hype = max(0.0, min(1.0, hype_raw))

    # 메아리 감쇠 시 채팅발 반복토큰(laugh/cry)도 같은 비율로 줄여 mood_hint 판정 왜곡 방지.
    c_laugh_eff = round(c_laugh * damp)
    c_cry_eff = round(c_cry * damp)
    laugh = s_laugh + c_laugh_eff
    cry = s_cry + c_cry_eff

    # valence(분위기 방향): 발화가 분위기의 1차 소스다. 발화 valence가 명확하면
    # (|s_val| ≥ SPEECH_VAL_DECISIVE) 그 부호를 확정 방향으로 삼고, 봇 채팅은 같은
    # 방향일 때만 보강한다 — 봇이 친 ㅠㅠ 메아리가 스트리머의 승리("이겼다!") 방향을
    # 부정으로 뒤집어 mood_hint가 ㅠㅠ로 나가는 것을 막는다.
    s_val_eff = s_val * speech_weight
    c_val_eff = c_val * eff_chat_weight
    if abs(s_val) >= SPEECH_VAL_DECISIVE:
        # 발화 방향 확정. 채팅은 동방향 성분만 더하고, 역방향이면 무시.
        if (c_val_eff >= 0) == (s_val_eff >= 0):
            val_raw = s_val_eff + c_val_eff
        else:
            val_raw = s_val_eff
        # 화면 이벤트도 발화와 같은 방향일 때만 더한다
        if (e_val >= 0) == (s_val_eff >= 0):
            val_raw += e_val
        # 반복토큰도 발화 방향과 어긋나는 채팅발 성분은 버린다 (mood_hint 일관성).
        if s_val > 0:
            cry = s_cry
        else:
            laugh = s_laugh
    elif abs(e_val) >= SPEECH_VAL_DECISIVE:
        # 발화가 모호하면 큰 화면 이벤트(보스 처치·사망)가 방향을 정한다 — 봇 ㅋㅋ/ㅠㅠ 메아리가
        # 사망 장면을 폭소로 뒤집지 않게, 채팅은 같은 방향만.
        val_raw = e_val + s_val_eff + (c_val_eff if (c_val_eff >= 0) == (e_val >= 0) else 0.0)
        if e_val > 0:
            cry = s_cry
        else:
            laugh = s_laugh
    else:
        val_raw = s_val_eff + c_val_eff + e_val
    valence = max(-1.0, min(1.0, val_raw))

    return HypeSignal(
        hype=hype,
        valence=valence,
        laugh=laugh,
        cry=cry,
        event=round(min(e_hype, 1.0), 2),
    )


# ── hype → 타이밍/물량 파라미터 ──────────────────────────────────────────────

@dataclass
class HypeProfile:
    """hype 신호를 채팅 타이밍·물량·프롬프트 힌트로 변환한 결과.

    main.py 가 이 값으로 schedule_chats(delay/스킵)·batch 증량·mood_hint 를 적용한다.
    """
    hype: float
    delay_min: float        # 이번 라운드 출력 최소 딜레이
    delay_max: float        # 이번 라운드 출력 최대 딜레이
    extra_batch: int        # batch_size 에 더할 추가 발언자 수
    skip_relief: float      # frequency 스킵 확률에 더할 보정 (0=완화 없음, 1=스킵 면제)
    mood_hint: str          # LLM 프롬프트에 주입할 분위기 지시 (빈 문자열이면 미주입)


def build_hype_profile(
    signal: HypeSignal,
    *,
    base_delay_min: float,
    base_delay_max: float,
    threshold: float = 0.35,
    max_extra_batch: int = 4,
) -> HypeProfile:
    """hype 신호를 라운드 파라미터로. threshold 미만이면 평소 값 그대로(개입 없음).

    hype ∈ [threshold, 1] 을 t ∈ [0, 1] 로 정규화해 선형 보간한다:
      - delay      : 평소 → 거의 동시(0.2~1.5초)로 좁힘
      - extra_batch: 0 → max_extra_batch 명 추가
      - skip_relief: 0 → 0.6 (저빈도 봇도 잘 안 잘림)
    """
    h = signal.hype
    if h < threshold:
        return HypeProfile(
            hype=h,
            delay_min=base_delay_min,
            delay_max=base_delay_max,
            extra_batch=0,
            skip_relief=0.0,
            mood_hint="",
        )

    # threshold~1 구간을 0~1로 정규화
    t = (h - threshold) / max(1e-6, (1.0 - threshold))
    t = max(0.0, min(1.0, t))

    # 흥분할수록 딜레이를 평소값에서 빠른 쪽으로 끌어당김
    fast_min, fast_max = 0.2, 1.5
    delay_min = base_delay_min + (fast_min - base_delay_min) * t
    delay_max = base_delay_max + (fast_max - base_delay_max) * t

    extra_batch = round(max_extra_batch * t)
    skip_relief = 0.6 * t

    return HypeProfile(
        hype=h,
        delay_min=round(delay_min, 2),
        delay_max=round(delay_max, 2),
        extra_batch=extra_batch,
        skip_relief=round(skip_relief, 2),
        mood_hint=_mood_hint(signal),
    )


def _mood_hint(signal: HypeSignal) -> str:
    """valence/laugh/cry 로 LLM 분위기 지시문 생성. hype가 낮으면 caller가 안 부름."""
    is_laughy = signal.laugh >= 2 and signal.laugh >= signal.cry
    if signal.valence > 0.2 or is_laughy:
        return (
            "지금 채팅창이 빵 터진 분위기다. 짧은 폭소 리액션(ㅋㅋㅋㅋ, ㅋㅋㅋㅋㅋ, "
            "개웃기네, ㅁㅊ) 위주로 빠르게 친다. 길게 설명하지 말고 터지는 느낌만."
        )
    if signal.valence < -0.2 or signal.cry >= 2:
        return (
            "지금 채팅창이 짠하고 안타까운 분위기다. 짧은 공감 리액션(ㅠㅠㅠㅠ, "
            "ㅜㅜ, 아 슬프다, 어떡해) 위주로 친다. 길게 설명하지 말고 감정만."
        )
    # 격렬하지만 방향이 모호 — 흥분만 전달
    return (
        "지금 채팅창이 격하게 들썩이는 분위기다. 짧고 강한 리액션 위주로 빠르게 친다. "
        "길게 설명하지 말 것."
    )
