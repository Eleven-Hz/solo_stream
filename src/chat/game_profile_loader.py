"""
게임 프로필 로더.
- game_profiles/{게임명}.yaml 이 있으면 로드
- 없으면 Gemini 3 (Google Search grounding) 으로 실제 정보를 검색해서 자동 생성 후 저장
"""
import json
import logging
import re
from pathlib import Path

import yaml
from google.genai import types

logger = logging.getLogger("solo_stream.game_profile")

_PROFILE_DIR = Path(__file__).parent.parent.parent / "game_profiles"

# 프로필 생성 전용 모델: Google Search grounding 지원 모델
_PROFILE_MODEL = "gemini-3-flash-preview"

_GENERATE_PROMPT = """인터넷에서 "{game_name}" 게임 정보를 검색하여 아래 JSON 형식으로 작성하세요.
위키, 공식 사이트, 팬덤 등 신뢰할 수 있는 출처를 참고하세요.
모르는 항목은 빈 배열 []로 남기세요.
반드시 JSON만 출력하고 마크다운 코드블록이나 다른 텍스트는 포함하지 마세요.

{{
  "name": "게임 정식 명칭",
  "description": "게임 소개: 장르, 개발사, 핵심 스토리, 분위기를 2~3문장으로",
  "systems": [
    "실제 게임 시스템 설명. 예: HP/SP 구조, 속성, 전투 방식 등 실제로 존재하는 것만"
  ],
  "locations": [
    "실제 게임 내 주요 지역/던전명과 간단한 설명"
  ],
  "enemies": [
    "실제 등장하는 몬스터/보스명과 특징"
  ],
  "skills": [
    "실제 플레이어 스킬/기술명"
  ],
  "chat_tips": [
    "시청자 봇 채팅에 유용한 이 게임만의 포인트: 유명 밈, 커뮤니티 용어, 죽음 연출, 하이라이트 순간, 팬덤 문화 등"
  ]
}}"""


def _safe_filename(game_name: str) -> str:
    """게임 이름을 파일명으로 변환."""
    name = re.sub(r'[\\/:*?"<>|]', "", game_name)
    name = name.strip().lower().replace(" ", "_")
    return name[:64]


def load_profile(game_name: str, ai_client=None) -> dict:
    """
    게임 프로필 로드. 없으면 ai_client로 자동 생성.
    ai_client가 None이면 default 프로필 반환.
    """
    _PROFILE_DIR.mkdir(exist_ok=True)

    filename = _safe_filename(game_name)
    profile_path = _PROFILE_DIR / f"{filename}.yaml"

    if profile_path.exists():
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = yaml.safe_load(f)
        logger.info(f"게임 프로필 로드: {profile_path.name}")
        return profile

    # 프로필 없음 → 자동 생성
    if ai_client is None:
        logger.warning(f"프로필 없음, AI 클라이언트 없어서 default 사용: {game_name}")
        return _load_default()

    logger.info(f"게임 프로필 없음. Gemini로 자동 생성 중: {game_name}")
    profile = _generate_profile(game_name, ai_client)

    # 저장
    with open(profile_path, "w", encoding="utf-8") as f:
        yaml.dump(profile, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    logger.info(f"게임 프로필 저장 완료: {profile_path}")

    return profile


def _generate_profile(game_name: str, ai_client) -> dict:
    """Gemini 3 + Google Search grounding으로 실제 게임 정보를 검색해 프로필 생성."""
    prompt = _GENERATE_PROMPT.format(game_name=game_name)
    try:
        logger.info(f"Google Search로 '{game_name}' 정보 검색 중...")
        response = ai_client._client.models.generate_content(
            model=_PROFILE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
        text = response.text.strip()
        logger.info(f"검색 완료. 응답 길이: {len(text)}자")

        # JSON 추출 (마크다운 코드블록 제거)
        text = re.sub(r"```(?:json)?", "", text).strip()
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            text = json_match.group(0)

        data = json.loads(text)
        if not data.get("name"):
            data["name"] = game_name
        return data
    except json.JSONDecodeError as e:
        logger.error(f"프로필 JSON 파싱 실패: {e}\n응답: {text[:300]}")
        default = _load_default()
        default["name"] = game_name
        return default
    except Exception as e:
        logger.error(f"프로필 자동 생성 실패: {e}")
        default = _load_default()
        default["name"] = game_name
        return default


def _load_default() -> dict:
    default_path = _PROFILE_DIR / "default.yaml"
    if default_path.exists():
        with open(default_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {"name": "Unknown", "description": "", "systems": [], "locations": [],
            "enemies": [], "skills": [], "chat_tips": []}


def build_profile_context(profile: dict) -> str:
    """프로필을 프롬프트용 텍스트로 변환."""
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
