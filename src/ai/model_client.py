"""
유일한 AI API 접점. 다른 모듈은 이 파일의 인터페이스만 사용한다.
google.genai / anthropic SDK는 이 파일에서만 import한다.

vision_provider: "gemini" | "claude"
"""
import base64
import json
import logging
import re
import time

logger = logging.getLogger("solo_stream.ai")

_VISION_PROMPT = """당신은 게임 화면을 분석하는 AI입니다.
현재 게임 화면을 보고 아래 항목을 간결하게 한국어로 요약하세요 (2~3문장):
- 현재 위치 / 장소
- 진행 중인 상황 (전투, 탐험, 대화, 퀘스트 등)
- 눈에 띄는 이벤트나 변화 (HP 위기, 보스 등장, 레벨업 등)

없는 정보는 언급하지 마세요. 짧고 핵심만."""

_CHAT_PROMPT_TEMPLATE = """당신은 게임 스트리밍 방송의 시청자 봇들입니다.
아래 게임 정보와 현재 상황을 바탕으로 각 봇이 채팅을 칩니다.

{game_profile_context}

[현재 게임 상황]
{game_context}

[최근 채팅 기록]
{chat_history}

[봇 목록 - {bot_count}명]
{bots_info}

===규칙===
- 각 봇은 반드시 1개의 메시지만 생성. 2개 이상 금지.
- 메시지는 15자 이내. 실제 채팅처럼 짧게.
- 봇 중 일부(1~2명)는 이번엔 침묵해도 됨. 그러면 해당 봇은 결과에서 제외.
- 게임 정보를 아는 시청자처럼: 게임 용어, 캐릭터명, 스킬명을 자연스럽게 사용.
- 이전 채팅에 직접 반응하거나 다른 봇 닉네임을 언급해도 됨.
- HP/SP 수치는 직접 읽지 말고 "피 빵빵하네", "기력 다 썼다" 처럼 표현.
- 봇마다 말투가 확실히 달라야 함. 같은 내용 다르게 말하기 금지.

반드시 아래 JSON 배열 형식으로만 응답하세요:
[
  {{"name": "봇이름", "message": "채팅내용"}},
  ...
]"""


def _parse_chats(text: str) -> list[dict]:
    """JSON 채팅 배열 파싱 + 봇당 1개 중복 제거."""
    json_match = re.search(r"\[.*\]", text, re.DOTALL)
    if json_match:
        text = json_match.group(0)
    chats = json.loads(text)
    seen = set()
    result = []
    for c in chats:
        if "name" in c and "message" in c and c["name"] not in seen:
            seen.add(c["name"])
            result.append(c)
    return result


class GeminiClient:
    def __init__(self, api_key: str, config: dict):
        from google import genai
        self._genai = genai
        self._client = genai.Client(api_key=api_key)
        self._vision_model = config["ai"]["vision_model"]
        self._chat_model = config["ai"]["chat_model"]

    def _call_with_retry(self, fn, max_retries: int = 3):
        for attempt in range(max_retries):
            try:
                return fn()
            except Exception as e:
                msg = str(e)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    m = re.search(r"retry in (\d+(?:\.\d+)?)s", msg)
                    wait = float(m.group(1)) if m else (2 ** attempt * 2)
                    logger.warning(f"429 quota 초과, {wait:.1f}초 후 재시도 ({attempt+1}/{max_retries})")
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError("최대 재시도 횟수 초과")

    def analyze_screenshot(self, image_bytes: bytes) -> str:
        from google.genai import types
        try:
            def _call():
                return self._client.models.generate_content(
                    model=self._vision_model,
                    contents=[
                        _VISION_PROMPT,
                        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    ],
                )
            response = self._call_with_retry(_call)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Vision 분석 실패: {e}")
            return "게임 화면 분석 중 오류가 발생했습니다."

    def generate_chats(
        self,
        game_context: str,
        bots: list[dict],
        chat_history: list[dict],
        game_profile_context: str = "",
    ) -> list[dict]:
        bots_info = "\n".join(
            f"- {b['name']}: {b['style']} / 감정: {b['emotion']} / 관계: {b['relationship']}"
            for b in bots
        )
        history_text = (
            "\n".join(f"{c['name']}: {c['message']}" for c in chat_history[-10:])
            if chat_history else "(아직 채팅 없음)"
        )
        prompt = _CHAT_PROMPT_TEMPLATE.format(
            game_profile_context=game_profile_context or "(게임 프로필 없음 — 화면만 보고 판단)",
            game_context=game_context,
            chat_history=history_text,
            bots_info=bots_info,
            bot_count=len(bots),
        )
        try:
            def _call():
                return self._client.models.generate_content(
                    model=self._chat_model,
                    contents=prompt,
                )
            response = self._call_with_retry(_call)
            return _parse_chats(response.text.strip())
        except json.JSONDecodeError as e:
            logger.error(f"채팅 JSON 파싱 실패: {e}")
            return []
        except Exception as e:
            logger.error(f"채팅 생성 실패: {e}")
            return []


class ClaudeClient:
    def __init__(self, api_key: str, config: dict):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._vision_model = config["ai"].get("claude_vision_model", "claude-opus-4-7")
        self._chat_model = config["ai"].get("claude_chat_model", "claude-sonnet-4-6")

    def analyze_screenshot(self, image_bytes: bytes) -> str:
        try:
            image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
            response = self._client.messages.create(
                model=self._vision_model,
                max_tokens=512,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": _VISION_PROMPT},
                        ],
                    }
                ],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error(f"Claude Vision 분석 실패: {e}")
            return "게임 화면 분석 중 오류가 발생했습니다."

    def generate_chats(
        self,
        game_context: str,
        bots: list[dict],
        chat_history: list[dict],
        game_profile_context: str = "",
    ) -> list[dict]:
        bots_info = "\n".join(
            f"- {b['name']}: {b['style']} / 감정: {b['emotion']} / 관계: {b['relationship']}"
            for b in bots
        )
        history_text = (
            "\n".join(f"{c['name']}: {c['message']}" for c in chat_history[-10:])
            if chat_history else "(아직 채팅 없음)"
        )
        prompt = _CHAT_PROMPT_TEMPLATE.format(
            game_profile_context=game_profile_context or "(게임 프로필 없음 — 화면만 보고 판단)",
            game_context=game_context,
            chat_history=history_text,
            bots_info=bots_info,
            bot_count=len(bots),
        )
        try:
            response = self._client.messages.create(
                model=self._chat_model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return _parse_chats(response.content[0].text.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Claude 채팅 JSON 파싱 실패: {e}")
            return []
        except Exception as e:
            logger.error(f"Claude 채팅 생성 실패: {e}")
            return []


def create_vision_client(config: dict, gemini_api_key: str, claude_api_key: str | None = None):
    """
    config["ai"]["vision_provider"] 에 따라 클라이언트 반환.
    "gemini" (기본값) 또는 "claude".
    """
    provider = config["ai"].get("vision_provider", "gemini").lower()
    if provider == "claude":
        if not claude_api_key:
            raise ValueError("Claude vision을 사용하려면 CLAUDE_API_KEY가 필요합니다.")
        return ClaudeClient(api_key=claude_api_key, config=config)
    return GeminiClient(api_key=gemini_api_key, config=config)
