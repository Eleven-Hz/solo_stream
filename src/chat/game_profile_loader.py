"""
게임 프로필 로더.
- game_profiles/{게임명}.yaml 이 있으면 로드
- 없으면: 기본은 '프로필 없음'(빈 프로필 + missing 표시) — 사용자가 build_manual_prompt() 를
  구독 채팅에 붙여넣고 받은 결과를 parse_profile_text()/save_profile() 로 넣는다(비용 0).
  auto_generate=True 일 때만 Gemini (Google Search grounding) 멀티턴으로 자동 생성 후 저장.

생성 파이프라인:
  1단계: 기본 정보 (장르, 개발사, 스토리, 분위기) + 주요 인물
  2단계: 전투 시스템 + 주요 지역
  3단계: 보스/몬스터 + 스킬/기술
  4단계: 커뮤니티 밈 / 채팅 포인트 (chat_tips 전용 심화 검색)
  5단계: 고유명사 한국어 번역 검수 (인물명, 지역명, 보스명, 스킬명)

프로필은 채팅 프롬프트에 매 라운드 통째로 들어가므로(build_profile_context) 항목 수·설명 길이가
곧 런타임 비용이다. 생성 프롬프트는 '많이'가 아니라 '확인된 것만 짧게'를 요구한다.
"""
import json
import logging
import re
import time
from pathlib import Path

import yaml

from src.utils import paths

logger = logging.getLogger("solo_stream.game_profile")

# 일시적(retryable) 오류로 간주할 신호. 이런 오류는 백오프 후 재시도한다.
_RETRYABLE_MARKERS = ("503", "unavailable", "429", "resource_exhausted",
                      "rate limit", "overloaded", "deadline", "500", "internal")
_MAX_RETRIES = 3          # 각 단계 최대 시도 횟수(첫 시도 포함)
_RETRY_BASE_DELAY = 2.0   # 지수 백오프 기본 지연(초): 2 → 4 → 8(상한)
_RETRY_MAX_DELAY = 8.0    # 백오프 지연 상한(초)

# 프로필은 런타임에 자동 생성·저장되므로 쓰기 가능한 사용자 데이터 폴더 기준.
_PROFILE_DIR = paths.user_data_dir() / "game_profiles"
_DEFAULT_PROFILE_MODEL = "gemini-2.5-flash"

# ── few-shot 예시 (CrossCode, 1단계 출력 형태) ──────────────────────────────────
_FEW_SHOT_EXAMPLE = """예시 출력 (CrossCode 기준):
{
  "name": "CrossCode (크로스코드)",
  "description": "Radical Fish Games 개발. 기억을 잃은 주인공 '레아'가 가상현실 MMO '크로스월드'의 진실을 파헤치는 2D 액션 RPG. 고속 전투와 고난도 퍼즐이 특징.",
  "characters": [
    "레아 (Lea): 주인공. 처음엔 '안녕'만 말할 수 있음",
    "세르게이 (Sergey): 레아를 돕는 연구원, 단어를 하나씩 추가해 줌"
  ]
}"""

# 모든 검색 단계 공통 규칙. 개수를 채우려고 지어낸 항목이 봇의 '틀린 사실 단정'(KNOWN_ISSUES 4-6·4-7)
# 으로 이어지므로 '못 채워도 된다'를 명시한다. 설명 길이 제한은 런타임 프롬프트 비용 절감용.
_RULES = """규칙:
- 검색으로 확인된 것만. 확인 안 되는 이름·설정은 지어내지 말고 빼세요 (개수를 못 채워도 됩니다).
- 설명은 30자 안팎 한 줄. 미사여구·중복 설명 금지."""

# ── 단계별 프롬프트 ──────────────────────────────────────────────────────────

_STEP1_PROMPT = """인터넷에서 "{game_name}" 게임의 기본 정보를 검색하세요.
공식 사이트, 스팀 페이지, 나무위키, 위키피디아 등을 참고하세요.

{few_shot}

{rules}
- 인물은 처음 만날 때 알 수 있는 정보만. 후반 반전(정체·배신·죽음)은 쓰지 마세요.

위 예시와 같은 수준으로 아래 JSON만 출력하세요 (마크다운 금지):
{{
  "name": "게임 정식 명칭 (한국어 공식명 있으면 병기, 예: CrossCode (크로스코드))",
  "description": "장르, 개발사, 핵심 스토리, 분위기를 2~3문장으로.",
  "characters": [
    "주인공·동료·주요 NPC 5~8명. 각 항목: '한국어명 (영문 원어명): 역할·주인공과의 관계 한 줄'.",
    "이름 있는 인물이 없는 게임(레이싱·전략 등)이면 빈 배열"
  ]
}}"""

_STEP2_PROMPT = """인터넷에서 "{game_name}" 게임의 전투 시스템과 주요 지역을 검색하세요.
게임 위키, 공략 사이트, 나무위키를 참고하세요.

{rules}

아래 JSON만 출력 (마크다운 금지):
{{
  "systems": [
    "핵심 시스템 6~10개. 각 항목: '시스템명 (원어명): 한 줄 설명' 형식.",
    "전투 메카닉, 성장, 자원/특수 게이지 위주. 한국어 공식/팬덤 번역명 병기"
  ],
  "locations": [
    "주요 지역/던전 8~12개, 해금 순서대로. 각 항목: '한국어명 (영문 원어명): 테마·특징 한 줄' 형식."
  ]
}}"""

_STEP3_PROMPT = """인터넷에서 "{game_name}" 게임의 몬스터/보스와 플레이어 스킬을 검색하세요.
게임 위키, 보스 공략 영상, 나무위키를 참고하세요.

{rules}

아래 JSON만 출력 (마크다운 금지):
{{
  "enemies": [
    "자주 마주치는 몬스터·주요 보스 8~12개. 각 항목: '한국어명 (영문 원어명): 특징 한 줄' 형식."
  ],
  "skills": [
    "플레이어가 쓰는 스킬/기술/무기 6~10개. 각 항목: '한국어명 (영문 원어명): 효과 한 줄' 형식."
  ]
}}"""

_STEP3B_VISION_FOCUS_PROMPT = """"{game_name}" 게임의 스크린샷을 AI가 분석할 때 주목해야 할 시각적 요소를 정리하세요.
인터넷 검색 없이 게임 장르와 UI/시스템 특성을 바탕으로 답하세요.

아래 JSON만 출력 (마크다운 금지):
{{
  "vision_focus": [
    "5~8개 항목. 각 항목은 '무엇을 어떻게 읽어야 하는지' 한 줄.",
    "예시: 지역 분위기 (배경 색상·지형 — 설원/사막/정글 등)",
    "예시: 현재 속성 모드 (화면 속성 아이콘 — 무색/열기/냉기/전기/파동)",
    "예시: 화면에 보이는 자원 (철광석/구리/석탄 등 광물 색상으로 구분)",
    "게임 장르에 맞는 UI 요소 (체력바, 미니맵, 인벤토리 상태 등) 포함",
    "전투형이면 적 종류·수·상태, 건설형이면 설비 종류·밀도 등 장르 특화 항목 포함"
  ]
}}"""

_STEP4_CHAT_TIPS_PROMPT = """인터넷에서 "{game_name}" 게임의 커뮤니티 문화와 스트리밍 밈을 검색하세요.
트위치/유튜브 클립, 레딧, 디시인사이드, 나무위키 등을 참고하세요.

이 게임의 스트리밍 채팅창에서 시청자들이 자주 치는 반응, 밈, 용어를 찾아야 합니다.

{rules}
- 'gg'·'ㅋㅋ' 같은 어느 게임에나 있는 반응은 빼고, 이 게임에만 있는 것만.

아래 JSON만 출력 (마크다운 금지):
{{
  "chat_tips": [
    "8~12개. 각 항목: '상황 → 채팅 반응' 한 문장.",
    "다룰 것: 특정 보스·장면 밈, 팬덤 별칭/줄임말, 커뮤니티만 아는 용어"
  ]
}}"""

_STEP5_TRANSLATION_PROMPT = """인터넷에서 "{game_name}" 게임의 한국어 번역을 검수하세요.
한국 공식 번역, 나무위키, 한국어 팬 사이트를 참고하세요.

아래에 현재 수집된 고유명사 목록이 있습니다:
{terms_list}

각 항목의 한국어 번역이 실제 게임 내 공식 한국어 번역 또는 한국 팬덤에서 통용되는 번역과 일치하는지 검수하고,
틀렸거나 더 나은 번역이 있으면 수정하세요.

아래 JSON만 출력 (마크다운 금지):
{{
  "corrections": [
    {{
      "original": "기존 표기",
      "corrected": "검수된 올바른 표기",
      "reason": "수정 이유 (공식 번역, 팬덤 통용 번역 등)"
    }}
  ]
}}

수정할 것이 없으면: {{"corrections": []}}"""


def _safe_filename(game_name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "", game_name)
    name = name.strip().lower().replace(" ", "_")
    return name[:64]


class ProfileGenerationAborted(Exception):
    """단계 하나가 503 등 일시적 서버 혼잡으로 재시도까지 소진하고도 실패한 경우.

    이런 상황은 모델 자체가 혼잡한 것이라 나머지 단계도 같은 이유로 실패할 가능성이 크다.
    빈 껍데기 프로필을 저장하지 않도록 생성 전체를 중단시키는 신호로 쓴다."""

    def __init__(self, step: str, cause: Exception):
        self.step = step
        self.cause = cause
        super().__init__(f"{step}: {cause}")


def _is_retryable(err: Exception) -> bool:
    """503/429 등 일시적 오류면 True (재시도 대상)."""
    msg = str(err).lower()
    return any(m in msg for m in _RETRYABLE_MARKERS)


def _search(ai_client, prompt: str, model: str) -> str:
    """Google Search grounding으로 단일 검색 실행.

    503/UNAVAILABLE 같은 일시적 오류는 지수 백오프로 최대 _MAX_RETRIES 회 재시도한다.
    모든 재시도가 실패하면 마지막 예외를 그대로 raise 한다(호출부가 메시지 수집).
    """
    from google.genai import types
    last_err: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = ai_client._client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                ),
            )
            um = getattr(response, "usage_metadata", None)
            if um is not None:
                from src.utils import usage
                usage.record("profile", model, getattr(um, "prompt_token_count", 0) or 0,
                             (getattr(um, "candidates_token_count", 0) or 0)
                             + (getattr(um, "thoughts_token_count", 0) or 0))
            text = (response.text or "").strip()
            if not text:
                # 503 등으로 본문이 비어 돌아오는 케이스도 일시적 오류로 취급해 재시도
                raise RuntimeError("빈 응답(text 없음)")
            return text
        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES and _is_retryable(e):
                delay = min(_RETRY_BASE_DELAY * (2 ** (attempt - 1)), _RETRY_MAX_DELAY)
                logger.warning(
                    f"    일시적 오류 — {delay:.0f}s 후 재시도 ({attempt}/{_MAX_RETRIES}): {e}"
                )
                time.sleep(delay)
                continue
            raise
    # 도달하지 않지만 방어적으로
    raise last_err if last_err else RuntimeError("검색 실패")


def _parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    return json.loads(text)


def load_profile(game_name: str, ai_client=None, config: dict | None = None,
                 auto_generate: bool = False) -> dict:
    """
    게임 프로필 로드. 파일이 없으면:
      - auto_generate=True 이고 ai_client 가 있으면 Gemini 검색으로 자동 생성(API 비용)
      - 아니면 빈 프로필에 _missing 표시 → UI 가 '프로필 만들기' 안내를 띄운다
    """
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    filename = _safe_filename(game_name)
    profile_path = _PROFILE_DIR / f"{filename}.yaml"

    if profile_path.exists():
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = yaml.safe_load(f)
        logger.info(f"게임 프로필 로드: {profile_path.name}")
        return profile

    if ai_client is None or not auto_generate:
        logger.warning(f"프로필 없음 — 빈 프로필로 진행(설정에서 만들기 안내): {game_name}")
        result = _empty_profile(game_name)
        result["_missing"] = True
        return result

    model = (config or {}).get("ai", {}).get("profile_model", _DEFAULT_PROFILE_MODEL)
    logger.info(f"게임 프로필 없음. 멀티턴 검색으로 자동 생성 중: {game_name} (모델: {model})")
    try:
        profile = _generate_profile(game_name, ai_client, model)
    except ProfileGenerationAborted as e:
        # 모델 혼잡(503 등)으로 중단됨. 빈 껍데기를 저장하면 다음 실행에 그게 로드돼
        # 재생성 기회를 영영 잃으므로 저장하지 않고, UI가 알림을 띄울 수 있게 표시만 한다.
        logger.error(
            f"게임 프로필 생성 중단 ({e.step}) — 모델({model}) 혼잡(503). "
            f"저장하지 않음, 잠시 후 다시 시도 필요."
        )
        result = _empty_profile(game_name)
        result["_generation_aborted"] = str(e)
        return result

    # 생성 중 발생한 단계 오류는 UI 표시용이라 디스크에는 저장하지 않는다.
    # (오류가 박제되면 다음 실행 때 파일이 그대로 로드돼 재생성 기회를 놓친다)
    errors = profile.get("_generation_errors")
    to_save = {k: v for k, v in profile.items() if k != "_generation_errors"}
    with open(profile_path, "w", encoding="utf-8") as f:
        yaml.dump(to_save, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    logger.info(f"게임 프로필 저장 완료: {profile_path}")
    if errors:
        logger.warning(f"프로필 생성 중 {len(errors)}개 단계 실패 — 일부 정보 누락 가능")

    return profile


def _generate_profile(game_name: str, ai_client, model: str) -> dict:
    profile: dict = {
        "name": game_name,
        "description": "",
        "vision_focus": [],
        "characters": [],
        "systems": [],
        "locations": [],
        "enemies": [],
        "skills": [],
        "chat_tips": [],
    }
    # 단계 실패(재시도 소진 포함)를 모아 호출부가 UI로 표시할 수 있게 한다.
    errors: list[str] = []

    def _record_error(step: str, e: Exception):
        # 503 등 일시적 서버 혼잡으로 재시도까지 소진하고도 실패한 경우:
        # 모델 자체가 혼잡한 것이라 나머지 단계도 같은 이유로 실패할 공산이 크다.
        # 빈 껍데기 프로필을 만들지 않도록 생성 전체를 즉시 중단한다.
        if _is_retryable(e):
            logger.error(f"  {step} 실패 (재시도 후에도 실패) — 모델 혼잡으로 생성 중단: {e}")
            raise ProfileGenerationAborted(step, e)
        logger.error(f"  {step} 실패: {e}")
        errors.append(f"{step}: {e}")

    # ── 1단계: 기본 정보 ────────────────────────────────────────────────────
    logger.info(f"[1/5] 기본 정보 + 인물 검색 중...")
    try:
        prompt = _STEP1_PROMPT.format(game_name=game_name, few_shot=_FEW_SHOT_EXAMPLE, rules=_RULES)
        text = _search(ai_client, prompt, model)
        data = _parse_json(text)
        profile["name"] = data.get("name", game_name)
        profile["description"] = data.get("description", "")
        profile["characters"] = _clean_list(data.get("characters", []))
        logger.info(f"  → 이름: {profile['name']}, 인물 {len(profile['characters'])}명")
    except Exception as e:
        _record_error("1단계(기본 정보)", e)

    # ── 2단계: 전투 시스템 + 지역 ───────────────────────────────────────────
    logger.info(f"[2/5] 전투 시스템 + 지역 검색 중...")
    try:
        prompt = _STEP2_PROMPT.format(game_name=game_name, rules=_RULES)
        text = _search(ai_client, prompt, model)
        data = _parse_json(text)
        profile["systems"] = _clean_list(data.get("systems", []))
        profile["locations"] = _clean_list(data.get("locations", []))
        logger.info(f"  → 시스템 {len(profile['systems'])}개, 지역 {len(profile['locations'])}개")
    except Exception as e:
        _record_error("2단계(전투/지역)", e)

    # ── 3단계: 몬스터/보스 + 스킬 ──────────────────────────────────────────
    logger.info(f"[3/5] 몬스터/보스 + 스킬 검색 중...")
    try:
        prompt = _STEP3_PROMPT.format(game_name=game_name, rules=_RULES)
        text = _search(ai_client, prompt, model)
        data = _parse_json(text)
        profile["enemies"] = _clean_list(data.get("enemies", []))
        profile["skills"] = _clean_list(data.get("skills", []))
        logger.info(f"  → 적 {len(profile['enemies'])}개, 스킬 {len(profile['skills'])}개")
    except Exception as e:
        _record_error("3단계(몬스터/스킬)", e)

    # ── 3b단계: vision_focus 생성 ────────────────────────────────────────────
    logger.info(f"[3b/5] Vision 묘사 포커스 생성 중...")
    try:
        prompt = _STEP3B_VISION_FOCUS_PROMPT.format(game_name=game_name)
        text = _search(ai_client, prompt, model)
        data = _parse_json(text)
        profile["vision_focus"] = _clean_list(data.get("vision_focus", []))
        logger.info(f"  → vision_focus {len(profile['vision_focus'])}개")
    except Exception as e:
        _record_error("3b단계(Vision 포커스)", e)

    # ── 4단계: chat_tips 심화 검색 ──────────────────────────────────────────
    logger.info(f"[4/5] 커뮤니티 밈/채팅 포인트 심화 검색 중...")
    try:
        prompt = _STEP4_CHAT_TIPS_PROMPT.format(game_name=game_name, rules=_RULES)
        text = _search(ai_client, prompt, model)
        data = _parse_json(text)
        profile["chat_tips"] = _clean_list(data.get("chat_tips", []))
        logger.info(f"  → chat_tips {len(profile['chat_tips'])}개")
    except Exception as e:
        _record_error("4단계(채팅 포인트)", e)

    # ── 5단계: 한국어 번역 검수 ─────────────────────────────────────────────
    logger.info(f"[5/5] 한국어 번역 검수 중...")
    try:
        profile = _verify_translations(game_name, profile, ai_client, model)
    except Exception as e:
        _record_error("5단계(번역 검수)", e)

    if errors:
        profile["_generation_errors"] = errors

    return profile


def _verify_translations(game_name: str, profile: dict, ai_client, model: str) -> dict:
    """수집된 고유명사를 한국어로 검수하고 잘못된 번역을 교정."""
    all_terms: list[str] = []
    for section in ("characters", "locations", "enemies", "skills"):
        all_terms.extend(profile.get(section, []))

    if not all_terms:
        return profile

    terms_text = "\n".join(f"- {t}" for t in all_terms)
    prompt = _STEP5_TRANSLATION_PROMPT.format(
        game_name=game_name,
        terms_list=terms_text,
    )

    text = _search(ai_client, prompt, model)
    data = _parse_json(text)
    corrections: list[dict] = data.get("corrections", [])

    if not corrections:
        logger.info("  → 번역 수정 없음")
        return profile

    # 교정 맵 생성
    correction_map = {c["original"]: c["corrected"] for c in corrections if c.get("original") and c.get("corrected")}
    for reason_item in corrections:
        logger.info(f"  번역 수정: {reason_item.get('original')} → {reason_item.get('corrected')} ({reason_item.get('reason', '')})")

    def _apply_correction(item: str) -> str:
        # 완전 일치 우선
        if item in correction_map:
            return correction_map[item]
        # 항목 앞부분에 original이 포함된 경우 (설명이 뒤에 붙은 경우) 교체
        for original, corrected in correction_map.items():
            if original and item.startswith(original):
                return corrected + item[len(original):]
            # 항목 중간에 포함된 경우 (예: "지역명: 설명" 형식)
            if original and original in item:
                return item.replace(original, corrected, 1)
        return item

    # 각 섹션에 교정 적용
    for section in ("characters", "locations", "enemies", "skills"):
        profile[section] = [_apply_correction(item) for item in profile.get(section, [])]

    return profile


def _clean_list(items: list) -> list[str]:
    """JSON 배열에서 실제 내용 항목만 추출 (설명용 메타 문자열 제거)."""
    result = []
    for item in items:
        if not isinstance(item, str):
            continue
        # "예: ..." 또는 너무 짧거나 메타 설명인 항목 제거
        stripped = item.strip()
        if len(stripped) < 4:
            continue
        if stripped.startswith("예:") or stripped.startswith("각 항목"):
            continue
        result.append(stripped)
    return result


def _empty_profile(game_name: str = "Unknown") -> dict:
    """프로필 파일도 없고 AI 클라이언트도 없을 때 쓰는 최소 빈 프로필.

    번들 ``default.yaml`` 의존을 없앴다(별도 기본 프로필 파일 불필요 — 프로필은
    게임명별로 런타임 자동 생성되며, 생성 불가 시 이 빈 골격으로 충분).
    """
    return {
        "name": game_name or "Unknown",
        "description": "알 수 없는 게임입니다. 화면을 보고 상황에 맞게 반응하세요.",
        "vision_focus": [],
        "characters": [],
        "systems": [],
        "locations": [],
        "enemies": [],
        "skills": [],
        "chat_tips": [
            "게임 화면을 보고 자연스럽게 반응하세요",
            "전투 중이면 응원하거나 긴장감을 표현하세요",
            "탐험 중이면 풍경이나 다음 목적지에 대해 이야기하세요",
        ],
    }


def build_profile_context(profile: dict) -> str:
    """채팅 생성용 전체 프로필 컨텍스트."""
    lines = [f"[게임: {profile.get('name', '?')}]"]
    if profile.get("description"):
        lines.append(profile["description"])
    if profile.get("characters"):
        lines.append("\n주요 인물:")
        lines.extend(f"  - {s}" for s in profile["characters"])
    if profile.get("systems"):
        lines.append("\n주요 시스템:")
        lines.extend(f"  - {s}" for s in profile["systems"])
    if profile.get("locations"):
        lines.append("\n주요 장소:")
        lines.extend(f"  - {s}" for s in profile["locations"])
    if profile.get("enemies"):
        lines.append("\n주요 몬스터:")
        lines.extend(f"  - {s}" for s in profile["enemies"])
    if profile.get("skills"):
        lines.append("\n주요 스킬:")
        lines.extend(f"  - {s}" for s in profile["skills"])
    if profile.get("chat_tips"):
        lines.append("\n채팅 포인트:")
        lines.extend(f"  - {s}" for s in profile["chat_tips"])
    return "\n".join(lines)


def build_vision_context(profile: dict) -> str:
    """Vision 화면 분석 전용 컨텍스트. 지명·몬스터·스킬만 포함해 토큰 최소화."""
    lines = [f"게임: {profile.get('name', '?')}"]
    if profile.get("vision_focus"):
        lines.append("화면 묘사 시 주목할 항목:")
        for f in profile["vision_focus"]:
            lines.append(f"  - {f}")
    if profile.get("characters"):
        lines.append("등장인물 (대화창 이름과 대조):")
        for c in profile["characters"]:
            lines.append(f"  - {c.split(':')[0].strip()}")
        # 주인공을 '파란 아바타' 같은 외형으로 부르면 채팅 봇이 다른 인물과 헷갈린다
        hero = next((c for c in profile["characters"] if "주인공" in c), None)
        if hero:
            name = hero.split(":")[0].split("(")[0].strip()
            lines.append(f"플레이어 캐릭터: {name} — 묘사·상황에서 조작 중인 주인공은 외형('파란 옷 캐릭터' 등) 대신 '{name}'(이)라고 쓴다")
    if profile.get("locations"):
        lines.append("지역 목록 (화면의 지명과 대조):")
        for loc in profile["locations"]:
            short = loc.split(":")[0].strip()
            lines.append(f"  - {short}")
    if profile.get("enemies"):
        lines.append("등장 몬스터/보스:")
        for e in profile["enemies"]:
            short = e.split(":")[0].strip()
            lines.append(f"  - {short}")
    if profile.get("skills"):
        lines.append("주요 스킬:")
        for s in profile["skills"]:
            short = s.split(":")[0].strip()
            lines.append(f"  - {short}")
    return "\n".join(lines)


# ── 수동 생성(구독 채팅에 붙여넣기) ─────────────────────────────────────────────
# API 자동 생성(6단계 검색) 대신, 사용자가 이 프롬프트 하나를 ChatGPT/Claude/Gemini 앱에
# 붙여넣고 받은 YAML 을 앱에 다시 붙여넣는다. 구독 채팅의 웹 검색·리서치가 단계별 API
# 검색보다 깊고, API 비용이 들지 않는다. 결과 형식은 자동 생성과 같은 프로필 스키마.

PROFILE_SECTIONS = ("vision_focus", "characters", "systems", "locations", "enemies", "skills", "chat_tips")

_MANUAL_PROMPT = """게임 "{game_name}"의 정보를 웹에서 검색해, 이 게임을 실제로 아는 한국 시청자들이 방송 채팅에서 쓸 수 있는 '게임 프로필'을 만들어 주세요.
(웹 검색 기능을 켜고 공식 사이트·스팀·나무위키·위키피디아·게임 위키·공략 사이트·커뮤니티(레딧, 디시 등)를 참고)

반드시 지킬 것
- 실제로 게임에 있는 것만. 확인이 안 되는 이름·밈은 지어내지 말고 빼세요 (개수를 못 채워도 됩니다).
- 고유명사는 '한국어명 (영문 원어명)' 형식. 한국어 공식 번역이 있으면 그것을, 없으면 한국 팬덤에서 통용되는 표기를 쓰세요.
- 각 항목은 한 줄: '이름: 30자 안팎 설명'. 이 프로필은 매 채팅마다 AI에게 통째로 전달되니 짧을수록 좋습니다.
- 인물은 처음 만날 때 알 수 있는 정보만. 후반 반전(정체·배신·죽음)은 쓰지 마세요.
- 출처 링크·각주·인용 표시는 넣지 마세요.
- 결과는 아래 YAML 하나만, ```yaml 코드 블록 하나에 담아 주세요. 앞뒤 설명은 쓰지 마세요.

```yaml
name: 게임 정식 명칭 (한국어 공식명 있으면 병기. 예: CrossCode (크로스코드))
description: 장르, 개발사, 핵심 스토리, 분위기를 2~3문장으로.
vision_focus:   # 5~8개. AI가 게임 스크린샷을 볼 때 주목할 시각 요소와 읽는 법. HP 같은 HUD 숫자 읽기는 넣지 말 것
  - 지역 분위기 (배경 색상·지형 — 설원/사막/던전 등)
characters:     # 5~8명. 주인공·동료·주요 NPC. 이름 있는 인물이 없는 게임이면 빈 목록
  - 한국어명 (영문명): 역할·주인공과의 관계 한 줄
systems:        # 6~10개. 전투 메카닉, 성장, 자원/게이지, 특수 시스템
  - 시스템명 (원어명): 한 줄 설명
locations:      # 8~12개. 주요 지역/던전, 해금 순서대로
  - 한국어명 (영문명): 테마·특징 한 줄
enemies:        # 8~12개. 자주 마주치는 몬스터와 주요 보스
  - 한국어명 (영문명): 특징 한 줄
skills:         # 6~10개. 플레이어가 쓰는 스킬/기술/무기/아이템
  - 한국어명 (영문명): 효과 한 줄
chat_tips:      # 8~12개. 이 게임에만 있는 방송 채팅 반응·밈·별칭을 '상황 → 채팅 반응' 한 문장으로 ('gg' 같은 흔한 반응 제외)
  - 보스 첫 등장 → '또 시작됐네' 같은 밈 채팅이 쏟아짐
```

참고용 예시(CrossCode 일부):
```yaml
name: CrossCode (크로스코드)
description: Radical Fish Games 개발. 기억을 잃은 주인공 '레아'가 가상현실 MMO 'CrossWorlds'의 진실을 파헤치는 2D 액션 RPG. 고속 전투와 고난도 퍼즐이 특징.
characters:
  - 레아 (Lea): 주인공. 처음엔 '안녕'만 말할 수 있음
locations:
  - 초보자 항구 (Rookie Harbor): 시작 허브 도시
enemies:
  - 고슴도치 (Hedgehag): 상징적인 기본 몬스터
chat_tips:
  - 아폴로가 장황한 정의 선언을 시작하면 '또 시작됐네', 'Justice!' 채팅 폭발
```"""


def build_manual_prompt(game_name: str) -> str:
    """구독 채팅에 붙여넣을 프로필 생성 프롬프트(한 번에 전체)."""
    return _MANUAL_PROMPT.format(game_name=game_name.strip() or "Unknown")


def _profile_item(item) -> str | None:
    """리스트 항목 하나를 '이름: 설명' 문자열로. 채팅이 dict 로 주는 경우도 받는다."""
    if isinstance(item, str):
        return item.strip() or None
    if isinstance(item, dict):
        if len(item) == 1:  # {"이름": "설명"} — YAML 에서 '이름: 설명' 이 dict 로 읽힌 경우
            (k, v), = item.items()
            return f"{k}: {v}".strip() if v not in (None, "") else str(k).strip()
        name = item.get("name") or item.get("이름") or item.get("title")
        desc = item.get("description") or item.get("desc") or item.get("설명") or ""
        if name:
            return f"{name}: {desc}".strip().rstrip(":") if desc else str(name).strip()
    return None


_KNOWN_KEYS = ("name", "description") + PROFILE_SECTIONS


def _strip_citations(text: str) -> str:
    """검색 켠 채팅이 붙이는 출처 표시 제거: '([site](url))' 는 통째로, '[글자](url)' 는 글자만."""
    text = re.sub(r"\s*\(\[[^\]]*\]\([^)]*\)\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\(https?://[^)]*\)", r"\1", text)
    return re.sub(r"\s*【[^】]*】", "", text)


def _parse_loose(text: str) -> dict:
    """정식 YAML 이 아닐 때의 줄 단위 읽기. 채팅 답은 항목 안에 '예: …' 처럼 콜론이 또 들어가
    YAML 이 깨지는 경우가 흔해서, 'key:' 줄과 '- 항목' 줄만 보고 항목은 원문 그대로 둔다."""
    data: dict = {}
    key = None
    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_]+)\s*:\s*(.*)$", line)
        if m and m.group(1) in _KNOWN_KEYS:
            key, value = m.group(1), m.group(2).strip()
            if value.startswith("#") or value in ("|", ">", "|-", ">-"):
                value = ""  # 주석만 있거나 블록 스칼라 시작 — 내용은 다음 줄들
            data[key] = value.strip("'\"") if value else ([] if key in PROFILE_SECTIONS else "")
            continue
        if key is None:
            continue
        item = re.match(r"^\s*[-•*]\s+(.*)$", line)
        if item and key in PROFILE_SECTIONS:
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(item.group(1).strip().strip("'\""))
        elif isinstance(data.get(key), list) and data[key]:
            data[key][-1] += " " + line.strip()  # 줄바꿈된 항목 이어붙이기
        elif isinstance(data.get(key), str):
            data[key] = (data[key] + " " + line.strip()).strip()  # 여러 줄 설명
    return data


def _extract_structured(text: str):
    """붙여넣은 답에서 프로필을 찾아 읽는다. 코드 블록 → 전체 → { } 순으로 정식 YAML/JSON,
    다 안 되면 줄 단위 읽기(_parse_loose)."""
    text = _strip_citations(text)
    blocks = re.findall(r"```(?:ya?ml|json)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    candidates = blocks + [text]
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        candidates.append(m.group(0))
    for c in candidates:
        try:
            data = yaml.safe_load(c)
        except Exception:
            continue
        if isinstance(data, dict) and any(k in data for k in _KNOWN_KEYS):
            return data
    for c in blocks + [text]:
        data = _parse_loose(c)
        if any(data.get(k) for k in _KNOWN_KEYS):
            return data
    raise ValueError("프로필 형식(name:, locations: 같은 항목)을 찾지 못했어요")


def parse_profile_text(text: str, fallback_name: str = "") -> tuple[dict, list[str]]:
    """구독 채팅 결과(붙여넣기/파일)를 프로필 dict 로. (프로필, 경고 목록). 못 읽으면 ValueError."""
    if not (text or "").strip():
        raise ValueError("붙여넣은 내용이 없어요")
    data = _extract_structured(text)
    profile: dict = {
        "name": str(data.get("name") or fallback_name or "Unknown").strip(),
        "description": str(data.get("description") or "").strip(),
    }
    for section in PROFILE_SECTIONS:
        raw = data.get(section) or []
        if isinstance(raw, str):
            raw = [line.lstrip("-• ").strip() for line in raw.splitlines()]
        items = [s for s in (_profile_item(x) for x in raw) if s]
        profile[section] = _clean_list(items)
    warnings: list[str] = []
    if not data.get("name"):
        warnings.append("이름(name)이 없어 게임 이름으로 채웠어요")
    if not profile["description"]:
        warnings.append("설명(description)이 비어 있어요")
    empty = [s for s in PROFILE_SECTIONS if not profile[s] and s != "characters"]
    if empty:
        warnings.append(f"비어 있는 항목: {', '.join(empty)}")
    return profile, warnings


def profile_path_for(game_name: str) -> Path:
    return _PROFILE_DIR / f"{_safe_filename(game_name)}.yaml"


def save_profile(game_name: str, profile: dict) -> Path:
    """프로필을 게임 이름 규칙의 파일로 저장(덮어씀). load_profile 이 다음부터 이 파일을 읽는다."""
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    path = profile_path_for(game_name)
    to_save = {k: v for k, v in profile.items() if not k.startswith("_")}
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(to_save, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    logger.info(f"게임 프로필 저장(수동): {path}")
    return path
