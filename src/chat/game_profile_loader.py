"""
게임 프로필 로더.
- game_profiles/{게임명}.yaml 이 있으면 로드
- 없으면 Gemini (Google Search grounding) 멀티턴으로 자동 생성 후 저장

생성 파이프라인:
  1단계: 기본 정보 (장르, 개발사, 스토리, 분위기)
  2단계: 전투 시스템 + 주요 지역
  3단계: 보스/몬스터 + 스킬/기술
  4단계: 커뮤니티 밈 / 채팅 포인트 (chat_tips 전용 심화 검색)
  5단계: 고유명사 한국어 번역 검수 (지역명, 보스명, 스킬명)
"""
import json
import logging
import re
import time
from pathlib import Path

import yaml
from google.genai import types

from src.utils import paths

logger = logging.getLogger("solo_stream.game_profile")

# 일시적(retryable) 오류로 간주할 신호. 이런 오류는 백오프 후 재시도한다.
_RETRYABLE_MARKERS = ("503", "unavailable", "429", "resource_exhausted",
                      "rate limit", "overloaded", "deadline", "500", "internal")
_MAX_RETRIES = 5          # 각 단계 최대 시도 횟수(첫 시도 포함)
_RETRY_BASE_DELAY = 4.0   # 지수 백오프 기본 지연(초): 4 → 8 → 16 → 30(상한)
_RETRY_MAX_DELAY = 30.0   # 백오프 지연 상한(초)

# 프로필은 런타임에 자동 생성·저장되므로 쓰기 가능한 사용자 데이터 폴더 기준.
_PROFILE_DIR = paths.user_data_dir() / "game_profiles"
_DEFAULT_PROFILE_MODEL = "gemini-2.5-flash"

# ── few-shot 예시 (CrossCode) ──────────────────────────────────────────────────
_FEW_SHOT_EXAMPLE = """예시 출력 (CrossCode 기준):
{
  "name": "CrossCode (크로스코드)",
  "description": "Radical Fish Games 개발. 기억을 잃은 주인공 '레아'가 가상현실 MMO 'CrossWorlds'의 진실을 파헤치는 2D 액션 RPG. 고속 전투와 고난도 퍼즐이 특징.",
  "systems": [
    "실시간 8방향 전투: 근접(칼)과 원거리(구체 투척) 전환",
    "4속성 시스템: 무색/화염/냉기/전격을 실시간 교체",
    "Circuit 성장 트리: SP 투자로 스탯·스킬 해금",
    "Combat Arts: SP 소모 필살기",
    "Break 시스템: 약점 속성으로 적 무력화"
  ],
  "locations": [
    "초보자 항구 (Rookie Harbor): 시작 허브 도시",
    "베르겐 트레일 (Bergen Trail): 눈 덮인 산악, 냉기 퍼즐",
    "파즈로 템플 (Faj'ro Temple): 화염 테마 던전"
  ],
  "enemies": [
    "고슴됴치 (Hedgehag): 상징적인 기본 몬스터",
    "아폴로 (Apollo): 라이벌 스피어맨 보스",
    "고대 수호자 (Ancient Guardian): 튜토리얼 거대 석상 보스"
  ],
  "skills": [
    "스피어 슬래시 (Sphere Slash): 기본 근접 콤보",
    "가드 스피어 (Guard Sphere): 전방위 방어막",
    "파이로 댄스 (Pyro Dance): 화염 회전 근접기"
  ],
  "chat_tips": [
    "레아의 한정 대사: 'Hi!', 'Lea!' 등 몇 단어만 구사 → 새 단어 습득 시 시청자 축하 문화",
    "아폴로 연설 밈: 장황한 정의 선언 시작되면 '또 시작됐네', 'Justice!' 채팅 폭발",
    "퍼즐 고통 밈: 난이도 높은 퍼즐에서 '뇌절', '지능 테스트' 자주 등장"
  ]
}"""

# ── 단계별 프롬프트 ──────────────────────────────────────────────────────────

_STEP1_PROMPT = """인터넷에서 "{game_name}" 게임의 기본 정보를 검색하세요.
공식 사이트, 스팀 페이지, 나무위키, 위키피디아 등을 참고하세요.

{few_shot}

위 예시와 같은 수준으로 아래 JSON만 출력하세요 (마크다운 금지):
{{
  "name": "게임 정식 명칭 (한국어 공식명 있으면 병기, 예: CrossCode (크로스코드))",
  "description": "장르, 개발사, 핵심 스토리, 분위기를 2~3문장으로. 공식 소개 수준의 구체성.",
  "genre": "장르 키워드 (예: 2D 액션 RPG, 로그라이크, 소울라이크 등)"
}}"""

_STEP2_PROMPT = """인터넷에서 "{game_name}" 게임의 전투 시스템과 주요 지역을 검색하세요.
게임 위키, 공략 사이트, 나무위키를 참고하세요.

아래 JSON만 출력 (마크다운 금지):
{{
  "systems": [
    "실제 존재하는 시스템 10개 이상. 각 항목: '시스템명: 한 줄 설명' 형식.",
    "HP/MP/스태미나 구조, 전투 메카닉, 성장 시스템, 특수 게이지 등 포함",
    "한국어 공식/팬덤 번역명 있으면 반드시 병기. 예: Circuit (회로 성장 트리)"
  ],
  "locations": [
    "실제 존재하는 주요 지역/던전 10개 이상. 각 항목: '한국어명 (영문 원어명): 한 줄 설명' 형식.",
    "각 지역의 테마, 특징, 해금 시점 포함"
  ]
}}"""

_STEP3_PROMPT = """인터넷에서 "{game_name}" 게임의 몬스터/보스와 플레이어 스킬을 검색하세요.
게임 위키, 보스 공략 영상, 나무위키를 참고하세요.

아래 JSON만 출력 (마크다운 금지):
{{
  "enemies": [
    "실제 등장하는 몬스터/보스 10개 이상. 각 항목: '한국어명 (영문 원어명): 특징 한 줄' 형식.",
    "일반 몬스터, 미니보스, 메인 보스, 숨겨진 보스 등 구분해서 포함"
  ],
  "skills": [
    "플레이어가 실제 사용 가능한 스킬/기술/무기 10개 이상. 각 항목: '한국어명 (영문 원어명): 효과 한 줄' 형식.",
    "기본기, 특수기, 궁극기, 패시브 등 포함"
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

아래 JSON만 출력 (마크다운 금지):
{{
  "chat_tips": [
    "15개 이상의 항목. 각 항목: 구체적인 상황과 채팅 반응을 한 문장으로.",
    "죽음 연출 반응 (예: 'gg', 'F', '또 죽었다')",
    "보스 등장 반응 (예: 특정 보스 등장 시 채팅 밈)",
    "하이라이트 순간 반응",
    "커뮤니티만 아는 게임 내 밈 용어",
    "팬덤에서 사용하는 별칭/줄임말",
    "이 게임만의 독특한 채팅 문화"
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


def _is_retryable(err: Exception) -> bool:
    """503/429 등 일시적 오류면 True (재시도 대상)."""
    msg = str(err).lower()
    return any(m in msg for m in _RETRYABLE_MARKERS)


def _search(ai_client, prompt: str, model: str) -> str:
    """Google Search grounding으로 단일 검색 실행.

    503/UNAVAILABLE 같은 일시적 오류는 지수 백오프로 최대 _MAX_RETRIES 회 재시도한다.
    모든 재시도가 실패하면 마지막 예외를 그대로 raise 한다(호출부가 메시지 수집).
    """
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


def load_profile(game_name: str, ai_client=None, config: dict | None = None) -> dict:
    """
    게임 프로필 로드. 없으면 ai_client로 자동 생성.
    ai_client가 None이면 default 프로필 반환.
    """
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    filename = _safe_filename(game_name)
    profile_path = _PROFILE_DIR / f"{filename}.yaml"

    if profile_path.exists():
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = yaml.safe_load(f)
        logger.info(f"게임 프로필 로드: {profile_path.name}")
        return profile

    if ai_client is None:
        logger.warning(f"프로필 없음, AI 클라이언트 없어서 빈 프로필 사용: {game_name}")
        return _empty_profile(game_name)

    model = (config or {}).get("ai", {}).get("profile_model", _DEFAULT_PROFILE_MODEL)
    logger.info(f"게임 프로필 없음. 멀티턴 검색으로 자동 생성 중: {game_name} (모델: {model})")
    profile = _generate_profile(game_name, ai_client, model)

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
        "systems": [],
        "locations": [],
        "enemies": [],
        "skills": [],
        "chat_tips": [],
    }
    # 단계 실패(재시도 소진 포함)를 모아 호출부가 UI로 표시할 수 있게 한다.
    errors: list[str] = []

    def _record_error(step: str, e: Exception):
        retryable = " (재시도 후에도 실패)" if _is_retryable(e) else ""
        logger.error(f"  {step} 실패{retryable}: {e}")
        errors.append(f"{step}: {e}")

    # ── 1단계: 기본 정보 ────────────────────────────────────────────────────
    logger.info(f"[1/5] 기본 정보 검색 중...")
    try:
        prompt = _STEP1_PROMPT.format(game_name=game_name, few_shot=_FEW_SHOT_EXAMPLE)
        text = _search(ai_client, prompt, model)
        data = _parse_json(text)
        profile["name"] = data.get("name", game_name)
        profile["description"] = data.get("description", "")
        logger.info(f"  → 이름: {profile['name']}")
    except Exception as e:
        _record_error("1단계(기본 정보)", e)

    # ── 2단계: 전투 시스템 + 지역 ───────────────────────────────────────────
    logger.info(f"[2/5] 전투 시스템 + 지역 검색 중...")
    try:
        prompt = _STEP2_PROMPT.format(game_name=game_name)
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
        prompt = _STEP3_PROMPT.format(game_name=game_name)
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
        prompt = _STEP4_CHAT_TIPS_PROMPT.format(game_name=game_name)
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
    for section in ("locations", "enemies", "skills"):
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
    for section in ("locations", "enemies", "skills"):
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
