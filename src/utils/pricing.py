"""
모델 API 가격표 + 비용 추정.

UI '비용' 탭에서 현재 선택된 모델(vision/chat/stt) 기준으로 시간당·세션당
대략적인 비용을 계산하기 위한 순수 로직. UI 의존성 없이 단독 테스트 가능.

⚠️ 가격은 자주 바뀐다. 아래 값은 PRICING_AS_OF 기준 공개 단가이며,
   토큰 가정(TOKENS)은 게임 스트리밍 사용 패턴을 대충 잡은 추정치다.
   정확한 청구액이 아니라 "모델 선택 간 상대 비교"가 목적.

출처:
  - Gemini API Pricing  https://ai.google.dev/gemini-api/docs/pricing
  - OpenAI API Pricing  https://developers.openai.com/api/docs/pricing
  - Claude Pricing      https://platform.claude.com/docs/en/about-claude/pricing
"""
from __future__ import annotations

PRICING_AS_OF = "2026-10"

# 모델별 단가 (USD per 1,000,000 tokens). 일부 STT 모델은 분당 고정요금(audio_per_min).
#   input        : 텍스트/이미지 입력 (vision·chat 에 사용)
#   output       : 텍스트 출력
#   audio_input  : 오디오 입력 (gemini STT 에 사용) — 없으면 input 으로 대체
#   audio_per_min: 오디오 1분당 USD (openai transcribe 처럼 분당 과금 모델)
#   token_mult   : 같은 한국어 프롬프트를 이 모델 토크나이저가 세는 배수(Gemini/OpenAI 기준 1.0).
#                  Claude 는 한국어를 약 1.7배로 센다(2026-10 채팅 프롬프트 실측 9,800 vs 5,800).
MODEL_PRICING: dict[str, dict[str, float]] = {
    # ── Gemini ──────────────────────────────────────────────
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0, "audio_input": 1.25},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "audio_input": 1.00},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "audio_input": 0.30},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40, "audio_input": 0.70},
    # ── Claude ──────────────────────────────────────────────
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "token_mult": 1.7},
    # ── OpenAI (vision / chat) ──────────────────────────────
    "gpt-6-luna": {"input": 0.10, "output": 0.50},
    "gpt-5-nano": {"input": 0.05, "output": 0.40},
    "gpt-5-mini": {"input": 0.25, "output": 2.00},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    # ── OpenAI (STT transcribe) ─────────────────────────────
    "gpt-transcribe": {"audio_per_min": 0.0045},
    "gpt-4o-transcribe": {"input": 2.50, "output": 10.0, "audio_per_min": 0.006},
    "gpt-4o-mini-transcribe": {"input": 1.25, "output": 5.0, "audio_per_min": 0.003},
    "whisper-1": {"audio_per_min": 0.006},
}

# 1회 호출/1분당 토큰 가정 (게임 스트리밍 대략치). 표에 그대로 노출해 투명하게 둔다.
TOKENS = {
    "vision_image": 700,    # 캡처 이미지(≈1024px) 1장의 입력 토큰
    "vision_prompt": 700,   # vision 프롬프트(시스템+게임 프로필 등) 입력 토큰
    "vision_output": 250,   # vision 분석 출력 토큰
    "chat_input": 5800,     # 채팅 생성 1회 입력 토큰(규칙+프로필+페르소나+기록, 2026-10 실측)
    "chat_output": 120,     # 채팅 생성 1회 출력 토큰
    "stt_audio_per_min": 1920,   # gemini 오디오 토큰/분 (32 tok/sec)
    "stt_output_per_min": 400,   # 받아쓴 텍스트 출력 토큰/분
}

# 사용량 기본 가정 (UI 에서 조절 가능)
USAGE_DEFAULTS = {
    "hours": 2.0,               # 세션 길이(시간)
    "speech_min_per_hour": 30.0,  # 시간당 실제 발화 분량(분)
    "screen_change_pct": 80.0,    # 캡처 주기 중 화면이 바뀌어 vision 호출되는 비율(%)
    "chat_calls_per_hour": 240.0,  # 시간당 채팅 생성 횟수 (기본 15초 주기에 1번 — UI 는 3600/주기로 덮어씀)
}


def get_pricing(model: str) -> dict[str, float] | None:
    """모델 단가 반환. 미등록(예: 미공개 프리뷰 모델)이면 None."""
    return MODEL_PRICING.get(model)


def _text_call_cost(pricing: dict[str, float], in_tokens: int, out_tokens: int) -> float:
    """입력/출력 토큰 1회 비용(USD)."""
    return (in_tokens * pricing.get("token_mult", 1.0) * pricing.get("input", 0.0)
            + out_tokens * pricing.get("output", 0.0)) / 1_000_000


def vision_cost_per_call(model: str) -> float | None:
    p = get_pricing(model)
    if p is None:
        return None
    return _text_call_cost(p, TOKENS["vision_image"] + TOKENS["vision_prompt"],
                           TOKENS["vision_output"])


def chat_cost_per_call(model: str) -> float | None:
    p = get_pricing(model)
    if p is None:
        return None
    return _text_call_cost(p, TOKENS["chat_input"], TOKENS["chat_output"])


def stt_cost_per_min(model: str) -> float | None:
    p = get_pricing(model)
    if p is None:
        return None
    if "audio_per_min" in p:
        return p["audio_per_min"]
    # gemini 계열: 오디오 입력 토큰 + 출력 토큰
    audio_rate = p.get("audio_input", p.get("input", 0.0))
    return (TOKENS["stt_audio_per_min"] * audio_rate
            + TOKENS["stt_output_per_min"] * p.get("output", 0.0)) / 1_000_000


def estimate_costs(
    *,
    vision_model: str,
    chat_model: str,
    stt_model: str,
    capture_interval_s: float,
    hours: float,
    speech_min_per_hour: float,
    screen_change_pct: float,
    chat_calls_per_hour: float,
) -> dict:
    """현재 모델 + 사용량 가정으로 시간당/세션당 비용 분해를 계산.

    반환:
      {
        "vision"/"chat"/"stt": {model, known, calls_per_hour|min_per_hour,
                                per_unit, per_hour, per_session},
        "total_per_hour", "total_session", "hours", "unknown": [model, ...],
      }
    """
    interval = capture_interval_s if capture_interval_s and capture_interval_s > 0 else 15.0
    vision_calls_hr = (3600.0 / interval) * (screen_change_pct / 100.0)

    v_unit = vision_cost_per_call(vision_model)
    c_unit = chat_cost_per_call(chat_model)
    s_unit = stt_cost_per_min(stt_model)

    def _block(model, known, rate_key, rate_val, per_hour):
        return {
            "model": model,
            "known": known,
            rate_key: rate_val,
            "per_hour": per_hour,
            "per_session": per_hour * hours,
        }

    vision = _block(vision_model, v_unit is not None, "calls_per_hour", vision_calls_hr,
                    (v_unit or 0.0) * vision_calls_hr)
    vision["per_unit"] = v_unit

    chat = _block(chat_model, c_unit is not None, "calls_per_hour", chat_calls_per_hour,
                  (c_unit or 0.0) * chat_calls_per_hour)
    chat["per_unit"] = c_unit

    stt = _block(stt_model, s_unit is not None, "min_per_hour", speech_min_per_hour,
                 (s_unit or 0.0) * speech_min_per_hour)
    stt["per_unit"] = s_unit

    total_hr = vision["per_hour"] + chat["per_hour"] + stt["per_hour"]
    unknown = [b["model"] for b in (vision, chat, stt) if not b["known"]]

    return {
        "vision": vision,
        "chat": chat,
        "stt": stt,
        "total_per_hour": total_hr,
        "total_session": total_hr * hours,
        "hours": hours,
        "unknown": unknown,
    }


def format_usd(amount: float) -> str:
    """작은 금액은 더 많은 소수자리로 표기."""
    if amount < 0.01:
        return f"${amount:.5f}"
    if amount < 1.0:
        return f"${amount:.4f}"
    return f"${amount:.2f}"
