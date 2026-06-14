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

_VISION_SYSTEM = "당신은 게임 방송 전문 해설 AI입니다. 주어진 게임 정보를 활용해 화면을 정확하고 간결하게 한국어로 분석합니다."

# Vision 분석 실패 시 game_context 자리에 들어가는 표식 문자열.
# 채팅 프롬프트 빌드 시 이 값이면 "화면 정보 없음"으로 치환해 봇이 발화/채팅에 집중하게 한다.
VISION_ERROR_MARKER = "게임 화면 분석 중 오류가 발생했습니다."

_VISION_PROMPT_TEMPLATE = """{game_profile_section}{prev_location_section}스크린샷을 분석해 아래 형식으로만 답하세요.

묘사: <화면에 보이는 것을 2~3줄로 서술{vision_focus_hint}>
위치: <게임 내 지명 — 게임 정보의 지명을 우선 사용. 화면에 지명 UI가 없으면 이전 위치를 유지. 정말 모르면 "알 수 없음">
상황: <지금 하고 있는 행동 — 전투/탐험/대화/퀘스트/이동 등 한 줄>
이벤트: <주목할 변화 (HP 위기·보스 등장·레벨업·중요 아이템 획득 등) — 없으면 이 줄 생략>

규칙: 각 항목은 1~2줄. 항목 레이블("묘사:", "위치:", "상황:", "이벤트:") 유지. 추가 설명 금지."""


def _build_vision_prompt(game_profile_context: str = "", previous_location: str = "") -> str:
    profile_section = f"[게임 정보]\n{game_profile_context}\n\n" if game_profile_context else ""
    prev_section = f"[이전 위치] {previous_location}\n\n" if previous_location else ""

    # vision_focus 항목이 게임 정보에 있으면 묘사 힌트로 추출
    focus_hint = ""
    if game_profile_context:
        lines = game_profile_context.splitlines()
        focus_items = []
        in_focus = False
        for line in lines:
            if "화면 묘사 시 주목할 항목:" in line:
                in_focus = True
                continue
            if in_focus:
                if line.startswith("  - "):
                    focus_items.append(line.strip("- ").strip())
                else:
                    break
        if focus_items:
            focus_hint = " — 특히: " + " / ".join(focus_items)

    return _VISION_PROMPT_TEMPLATE.format(
        game_profile_section=profile_section,
        prev_location_section=prev_section,
        vision_focus_hint=focus_hint,
    )

_CHAT_PROMPT_TEMPLATE = """당신은 게임 스트리밍 방송의 시청자 봇들입니다.
아래 게임 정보와 현재 상황을 바탕으로 각 봇이 채팅을 칩니다.

{game_profile_context}
{mood_section}{user_speech_section}
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
- HP/SP 수치는 직접 읽지 말고 "피 빵빵하네", "기력 다 썼다" 처럼 표현.
- 봇마다 말투가 확실히 달라야 함. 같은 내용 다르게 말하기 금지.
- 봇별 [자기 기억] 범위만큼만 자기 과거 발언을 참고한다. 범위 밖 발언은 기억 못 함.
- 봇별 [타인 반응] 설정이 false이면 다른 봇 발언에 직접 반응하거나 닉네임 언급 금지.
- 봇별 [타인 반응 빈도]가 있으면 그 확률로만 타인 발언에 반응한다 (예: 0.3 → 30% 확률).
- 봇이 다른 봇에게 받아칠 때는 그 봇의 닉네임을 직접 부르며 말할 수 있다 ([타인 반응]이 true인 경우에 한함).
- 봇별 [호명됨] true인 봇은 스트리머가 자기 이름을 부른 것이다. 반드시 응답하며, 일반 게임 채팅이 아니라 **스트리머가 한 말 그 자체에 직접 반응**한다 — 인사("안녕")를 받으면 인사로 받아치고, 질문을 받으면 답하고, 의견을 받으면 그에 대해 말한다. 페르소나 말투는 유지하되 발화 내용을 무시하고 엉뚱한 채팅을 치지 않는다.
- **[호명됨] true인 봇은 자기 직전 발언(내 최근 발언)을 그대로 반복하지 않는다.** HP/SP·화면 상황 코멘트로 때우지 말고, 호명한 발화의 내용에 맞춰 새로운 말을 한다. (예: "패드가 편한가요?"라고 물으면 "HP 여유롭네요"가 아니라 "패드 추천드려요" 같이 질문에 답)
- 스트리머 발화가 질문이면(물음표가 없어도 "어디", "뭐", "어떤", "할까" 등 묻는 말투 포함) 호명된 봇이 없더라도 봇 중 최소 1명은 그 질문에 **구체적인 내용으로 직접 답한다**. "글쎄", "음..." 같은 회피 금지.
  · 게임 내 질문(다음 지역/공략/어디로)이면 고인물·분석 성향 봇이 게임 정보를 근거로 실제 답을 제시한다.
  · 게임 추천·취향 질문이면(예: "이거랑 비슷한 게임 뭐 있어?") 시청자답게 실제 게임 제목 등 구체적 답을 1개 이상 댄다.
- 봇별 [스트리머 발화 반응]에 따라 스트리머가 한 말에 대한 반응 강도를 다르게 한다.
  · off    — 스트리머 발화를 무시. 화면 상황만 보고 채팅.
  · low    — 들었다는 정도. 가벼운 동조/리액션만 (예: "ㅇㅈ", "ㄹㅇ"). 직접 답하지 않음.
  · medium — 한 번 정도 받아치거나 동조. 봇 말투로 변형해서.
  · high   — 적극 받아치고 맞장구. 단, 스트리머 말을 그대로 옮기지 말고 봇 말투로.

반드시 아래 JSON 배열 형식으로만 응답하세요.
- 마크다운 코드펜스(```)를 사용하지 말 것.
- 마지막 항목 뒤에 trailing comma를 넣지 말 것.
- JSON 외의 설명 문장은 절대 추가하지 말 것.

[
  {{"name": "봇이름", "message": "채팅내용"}},
  ...
]"""


def _build_bots_info(
    bots: list[dict],
    chat_history: list[dict],
    default_speech_weight: str = "medium",
    mentioned_names: list[str] | None = None,
) -> str:
    from src.chat.persona_loader import normalize_user_speech_weight

    mentioned_set = set(mentioned_names or [])
    lines = []
    for b in bots:
        name = b["name"]
        memory_depth = b.get("memory_depth", 0)
        react = b.get("react_to_others", True)
        react_chance = b.get("react_chance", 1.0)
        speech_weight = normalize_user_speech_weight(
            b.get("user_speech_weight"), default=default_speech_weight,
        )

        line = f"- {name}: {b['style']} / 감정: {b['emotion']} / 관계: {b['relationship']}"
        line += f" / [자기 기억] 최근 자기 발언 {memory_depth}개"

        if not react:
            line += " / [타인 반응] false"
        else:
            pct = int(react_chance * 100)
            line += f" / [타인 반응] true / [타인 반응 빈도] {pct}%"

        line += f" / [스트리머 발화 반응] {speech_weight}"

        if name in mentioned_set:
            line += " / [호명됨] true"

        if memory_depth > 0:
            own = [c["message"] for c in chat_history if c.get("name") == name]
            recent_own = own[-memory_depth:] if own else []
            if recent_own:
                quoted = ", ".join(f'"{m}"' for m in recent_own)
                line += f" / 내 최근 발언: {quoted}"

        lines.append(line)
    return "\n".join(lines)


def _game_context_for_prompt(game_context: str) -> str:
    """Vision 분석이 실패했으면 프롬프트에 오류 문장을 그대로 넣지 않고
    '화면 정보 없음'으로 치환한다. 봇이 오류 메시지를 채팅 소재로 삼거나
    그쪽에 끌려가 스트리머 발화를 무시하는 것을 막는다."""
    if not game_context or game_context.strip() == VISION_ERROR_MARKER:
        return "(화면 정보 없음 — 최근 채팅과 스트리머 발화를 보고 반응)"
    return game_context


def _build_user_speech_section(user_speech: list[dict] | None) -> str:
    """프롬프트의 [최근 사용자 발화] 섹션. user_speech 항목은
    {"text": str, "seconds_ago": float} 형식. 없으면 빈 문자열 반환."""
    if not user_speech:
        return ""
    lines = ["", "[최근 스트리머 발화 (음성)]"]
    for u in user_speech:
        sec = u.get("seconds_ago")
        text = u.get("text", "").strip()
        if not text:
            continue
        prefix = f"({sec:.1f}s 전) " if sec is not None else ""
        lines.append(f'- {prefix}"{text}"')
    if len(lines) == 2:  # 헤더만 있고 발화 없음
        return ""
    return "\n".join(lines) + "\n"


def _build_mood_section(mood_hint: str | None) -> str:
    """프롬프트의 [채팅창 분위기] 섹션. 흥분 라운드(hype 높음)에만 채워진다.
    hype.build_hype_profile 이 만든 mood_hint 를 받아 LLM 에 톤을 지시한다."""
    hint = (mood_hint or "").strip()
    if not hint:
        return ""
    return f"[채팅창 분위기]\n{hint}\n\n"


class ChatCallDebug:
    """generate_chats 가 마지막 호출 정보를 보관해 호출자가 라운드 로그에 합쳐쓸 수 있게 함."""
    __slots__ = ("provider", "model", "prompt", "raw_response", "elapsed_s")

    def __init__(self):
        self.provider: str = ""
        self.model: str = ""
        self.prompt: str = ""
        self.raw_response: str = ""
        self.elapsed_s: float = 0.0


def _parse_chats(text: str) -> list[dict]:
    """JSON 채팅 배열 파싱 + 봇당 1개 중복 제거.

    LLM 응답은 종종 다음과 같은 형태로 와서 보수적 cleanup이 필요:
    - 마크다운 코드펜스(```json ... ```)로 감싸짐
    - 배열 마지막 항목에 trailing comma
    - 항목 사이에 설명 문장 섞임 (앞뒤 텍스트는 정규식으로 잘라냄)
    """
    # 1) 가장 바깥 [ ... ] 배열만 추출 (코드펜스/앞뒤 설명 제거)
    json_match = re.search(r"\[.*\]", text, re.DOTALL)
    if json_match:
        text = json_match.group(0)
    # 2) trailing comma 제거:  }  ,  ]   /   ]  ,  ]   (개행 사이도 처리)
    cleaned = re.sub(r",(\s*[\]}])", r"\1", text)
    try:
        chats = json.loads(cleaned)
    except json.JSONDecodeError:
        # 3) fallback: 항목별로 직접 추출
        chats = []
        for m in re.finditer(
            r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"message"\s*:\s*"([^"]*)"\s*\}',
            cleaned,
        ):
            chats.append({"name": m.group(1), "message": m.group(2)})
        if not chats:
            raise
    seen = set()
    result = []
    for c in chats:
        if isinstance(c, dict) and "name" in c and "message" in c and c["name"] not in seen:
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
        # 마지막 chat 호출의 prompt/원본응답/소요시간 — ai_process 가 라운드 블록에 합쳐 찍는 용도
        self.last_chat_call = ChatCallDebug()

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

    def analyze_screenshot(self, image_bytes: bytes, game_profile_context: str = "", previous_location: str = "") -> str:
        from google.genai import types
        prompt = _build_vision_prompt(game_profile_context, previous_location)
        try:
            def _call():
                return self._client.models.generate_content(
                    model=self._vision_model,
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                        prompt,
                    ],
                    config=types.GenerateContentConfig(
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
            response = self._call_with_retry(_call)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Vision 분석 실패: {e}")
            return VISION_ERROR_MARKER

    def generate_chats(
        self,
        game_context: str,
        bots: list[dict],
        chat_history: list[dict],
        game_profile_context: str = "",
        user_speech: list[dict] | None = None,
        default_speech_weight: str = "medium",
        mentioned_names: list[str] | None = None,
        mood_hint: str | None = None,
    ) -> list[dict]:
        bots_info = _build_bots_info(bots, chat_history, default_speech_weight, mentioned_names)
        history_text = (
            "\n".join(f"{c['name']}: {c['message']}" for c in chat_history[-10:])
            if chat_history else "(아직 채팅 없음)"
        )
        prompt = _CHAT_PROMPT_TEMPLATE.format(
            game_profile_context=game_profile_context or "(게임 프로필 없음 — 화면만 보고 판단)",
            mood_section=_build_mood_section(mood_hint),
            game_context=_game_context_for_prompt(game_context),
            user_speech_section=_build_user_speech_section(user_speech),
            chat_history=history_text,
            bots_info=bots_info,
            bot_count=len(bots),
        )
        raw = ""
        parsed: list[dict] = []
        t0 = time.perf_counter()
        try:
            def _call():
                return self._client.models.generate_content(
                    model=self._chat_model,
                    contents=prompt,
                )
            response = self._call_with_retry(_call)
            raw = (response.text or "").strip()
            parsed = _parse_chats(raw)
        except json.JSONDecodeError as e:
            logger.error(f"채팅 JSON 파싱 실패: {e}")
        except Exception as e:
            logger.error(f"채팅 생성 실패: {e}")
        finally:
            self.last_chat_call.provider = "gemini"
            self.last_chat_call.model = self._chat_model
            self.last_chat_call.prompt = prompt
            self.last_chat_call.raw_response = raw
            self.last_chat_call.elapsed_s = time.perf_counter() - t0
        return parsed


class ClaudeClient:
    def __init__(self, api_key: str, config: dict):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._vision_model = config["ai"].get("claude_vision_model", "claude-opus-4-7")
        self._chat_model = config["ai"].get("claude_chat_model", "claude-sonnet-4-6")
        self.last_chat_call = ChatCallDebug()

    def analyze_screenshot(self, image_bytes: bytes, game_profile_context: str = "", previous_location: str = "") -> str:
        prompt = _build_vision_prompt(game_profile_context, previous_location)
        try:
            image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
            response = self._client.messages.create(
                model=self._vision_model,
                max_tokens=768,
                system=_VISION_SYSTEM,
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
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error(f"Claude Vision 분석 실패: {e}")
            return VISION_ERROR_MARKER

    def generate_chats(
        self,
        game_context: str,
        bots: list[dict],
        chat_history: list[dict],
        game_profile_context: str = "",
        user_speech: list[dict] | None = None,
        default_speech_weight: str = "medium",
        mentioned_names: list[str] | None = None,
        mood_hint: str | None = None,
    ) -> list[dict]:
        bots_info = _build_bots_info(bots, chat_history, default_speech_weight, mentioned_names)
        history_text = (
            "\n".join(f"{c['name']}: {c['message']}" for c in chat_history[-10:])
            if chat_history else "(아직 채팅 없음)"
        )
        prompt = _CHAT_PROMPT_TEMPLATE.format(
            game_profile_context=game_profile_context or "(게임 프로필 없음 — 화면만 보고 판단)",
            mood_section=_build_mood_section(mood_hint),
            game_context=_game_context_for_prompt(game_context),
            user_speech_section=_build_user_speech_section(user_speech),
            chat_history=history_text,
            bots_info=bots_info,
            bot_count=len(bots),
        )
        raw = ""
        parsed: list[dict] = []
        t0 = time.perf_counter()
        try:
            response = self._client.messages.create(
                model=self._chat_model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            parsed = _parse_chats(raw)
        except json.JSONDecodeError as e:
            logger.error(f"Claude 채팅 JSON 파싱 실패: {e}")
        except Exception as e:
            logger.error(f"Claude 채팅 생성 실패: {e}")
        finally:
            self.last_chat_call.provider = "claude"
            self.last_chat_call.model = self._chat_model
            self.last_chat_call.prompt = prompt
            self.last_chat_call.raw_response = raw
            self.last_chat_call.elapsed_s = time.perf_counter() - t0
        return parsed


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


def create_chat_client(config: dict, gemini_api_key: str, claude_api_key: str | None = None):
    """
    config["ai"]["chat_provider"] 에 따라 채팅 전용 클라이언트 반환.
    "gemini" (기본값) 또는 "claude".
    """
    provider = config["ai"].get("chat_provider", "gemini").lower()
    if provider == "claude":
        if not claude_api_key:
            raise ValueError("Claude chat을 사용하려면 CLAUDE_API_KEY가 필요합니다.")
        return ClaudeClient(api_key=claude_api_key, config=config)
    return GeminiClient(api_key=gemini_api_key, config=config)
