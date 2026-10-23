"""
유일한 AI API 접점. 다른 모듈은 이 파일의 인터페이스만 사용한다.
google.genai / anthropic / openai(채팅·vision) SDK는 이 파일에서만 import한다.
(openai 의 STT 경로는 src/stt/transcriber.py 에 따로 있다.)

vision_provider / chat_provider: "gemini" | "claude" | "openai"
모델 이름은 provider 별 키에 둔다 — model_config_key() 참고.
"""
import base64
import json
import logging
import re
import time

from src.utils import usage

logger = logging.getLogger("solo_stream.ai")


def _gemini_tokens(response) -> tuple[int, int]:
    """Gemini 응답의 (입력, 출력) 토큰. 사고 토큰은 출력 요금으로 청구되므로 출력에 더한다."""
    um = getattr(response, "usage_metadata", None)
    if um is None:
        return 0, 0
    out = (getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0)
    return getattr(um, "prompt_token_count", 0) or 0, out


def _usage_tokens(response) -> tuple[int, int]:
    """OpenAI Responses / Anthropic Messages 응답의 (입력, 출력) 토큰 — 둘 다 같은 필드 이름."""
    u = getattr(response, "usage", None)
    if u is None:
        return 0, 0
    return getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0

_VISION_SYSTEM = "당신은 게임 방송 전문 해설 AI입니다. 주어진 게임 정보를 활용해 화면을 정확하고 간결하게 한국어로 분석합니다."

# Vision 분석 실패 시 game_context 자리에 들어가는 표식 문자열.
# 채팅 프롬프트 빌드 시 이 값이면 "화면 정보 없음"으로 치환해 봇이 발화/채팅에 집중하게 한다.
VISION_ERROR_MARKER = "게임 화면 분석 중 오류가 발생했습니다."

# 사용자가 설정에서 편집하는 vision 프롬프트 본문(형식/분량 규칙).
# config["ai"]["vision_prompt"] 가 있으면 그 값으로 대체된다.
# {vision_focus_hint} 자리는 게임 프로필의 '주목할 항목'으로 코드가 채운다(없으면 빈 문자열).
# 게임 정보/이전 위치 섹션은 코드가 본문 앞에 자동으로 붙이므로 여기 넣지 않는다.
_DEFAULT_VISION_BODY = """스크린샷을 분석해 아래 형식으로만 답하세요. 시청자가 화면을 못 보고도 지금 장면을 떠올릴 수 있게 구체적으로 씁니다.

묘사: <화면에 보이는 것을 3~5줄로 서술 — 캐릭터/적/NPC의 생김새·동작, 배경(장소 분위기·색감·날씨·시간대), 눈에 띄는 오브젝트나 이펙트{vision_focus_hint}>
화면 글자: <대사·자막·퀘스트 문구·알림·메뉴 등 화면에 적힌 의미 있는 글자를 짧게 옮김. HP·SP·경험치 같은 HUD 수치는 제외. 없으면 이 줄 생략>
위치: <게임 내 지명 — 게임 정보의 지명을 우선 사용. 화면에 지명 UI가 없으면 이전 위치를 유지. 정말 모르면 "알 수 없음">
상황: <지금 하고 있는 행동과 흐름 — 전투/탐험/대화/퀘스트/이동/메뉴 조작 등. 전투면 상대와 우세·열세, 대화면 누구와 무슨 얘기인지 1~2줄>
이벤트: <주목할 변화 (HP 위기·보스 등장·레벨업·중요 아이템 획득 등) — 없으면 이 줄 생략>

규칙: 항목 레이블("묘사:", "화면 글자:", "위치:", "상황:", "이벤트:") 유지. 화면에 실제로 보이는 것만 쓰고 추측으로 지어내지 않는다. 렉·버그·프레임 드랍 같은 기술 문제는 화면에 명백히 보일 때만 적는다. 추가 설명 금지."""


def _extract_focus_hint(game_profile_context: str) -> str:
    """게임 프로필의 '화면 묘사 시 주목할 항목'을 묘사 힌트 문자열로 추출."""
    if not game_profile_context:
        return ""
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
        return " — 특히: " + " / ".join(focus_items)
    return ""


def _build_vision_prompt(
    game_profile_context: str = "",
    previous_location: str = "",
    body_template: str | None = None,
) -> str:
    profile_section = f"[게임 정보]\n{game_profile_context}\n\n" if game_profile_context else ""
    prev_section = f"[이전 위치] {previous_location}\n\n" if previous_location else ""

    focus_hint = _extract_focus_hint(game_profile_context)

    body = body_template if body_template else _DEFAULT_VISION_BODY
    # 사용자가 본문에서 {vision_focus_hint} 를 지웠어도 안전하게 동작하도록
    # 해당 플레이스홀더만 치환한다(다른 중괄호는 .format 대상이 아님).
    body = body.replace("{vision_focus_hint}", focus_hint)

    return f"{profile_section}{prev_section}{body}"

_CHAT_PROMPT_TEMPLATE = """당신은 게임 스트리밍 방송의 시청자 봇들입니다.
아래 게임 정보와 현재 상황을 바탕으로 각 봇이 채팅을 칩니다.

{game_profile_context}
{mood_section}{user_speech_section}
[현재 게임 상황]
{game_context}

[최근 채팅 기록]
{chat_history}

{roster_section}[봇 목록 - {bot_count}명]
{bots_info}
{topic_section}{round_notes}
===규칙===
- 각 봇은 반드시 1개의 메시지만 생성. 2개 이상 금지.
- 메시지는 15자 이내. 실제 채팅처럼 짧게.
- 봇 중 일부(1~2명)는 이번엔 침묵해도 됨. 그러면 해당 봇은 결과에서 제외.
- 게임 정보를 아는 시청자처럼: 게임 용어, 캐릭터명, 스킬명을 자연스럽게 사용.
- HP/SP 수치는 직접 읽지 말고 "피 빵빵하네", "기력 다 썼다" 처럼 표현.
- 봇마다 말투가 확실히 달라야 함. 같은 내용 다르게 말하기 금지.
- **소재를 분산한다.** 한 라운드에서 같은 소재(같은 단어·같은 화제)는 최대 2명까지만(스트리머 발화에 반응하는 건 예외). 나머지 봇은 화면의 다른 요소, 스트리머 발화, 다른 봇의 말, 자기 페르소나 관심사 등 서로 다른 소재로 말한다.
- 렉·버그·끊김·프레임 같은 기술 문제는 [현재 게임 상황]에 명시됐을 때만 언급한다. 화면 정보가 없거나 채팅이 뜸하다고 렉을 추측하지 않는다.
- **[최근 채팅 기록]이나 "내 최근 발언"에 이미 나온 문장을 그대로(또는 거의 똑같이) 다시 치지 않는다.** 같은 봇이 직전에 한 말을 복붙하지 말고, 상황이 그대로면 차라리 침묵(결과에서 제외)한다. 새로 할 말이 없으면 그 봇은 빼라.
- 봇별 [자기 기억] 범위만큼만 자기 과거 발언을 참고한다. 범위 밖 발언은 기억 못 함.
- 봇별 [타인 반응] 설정이 false이면 다른 봇 발언에 직접 반응하거나 닉네임 언급 금지.
- 다른 봇에게 받아치는 봇은 [이번 라운드 대화 과제]에 지정돼 있다. 그 외 봇은 [타인 반응 빈도]가 높을 때만 가끔 받아친다.
- 봇별 "말버릇 금지" 단어는 그 봇이 최근 계속 쓴 말이다. 이번엔 그 단어를 쓰지 말고 다른 얘기를 한다.
- 봇이 다른 봇에게 받아칠 때는 그 봇의 닉네임을 직접 부르며 말할 수 있다 ([타인 반응]이 true인 경우에 한함).
- 봇별 [호명됨] true인 봇은 스트리머가 자기 이름을 부른 것이다. 반드시 응답하며, 일반 게임 채팅이 아니라 **스트리머가 한 말 그 자체에 직접 반응**한다 — 인사("안녕")를 받으면 인사로 받아치고, 질문을 받으면 답하고, 의견을 받으면 그에 대해 말한다. 페르소나 말투는 유지하되 발화 내용을 무시하고 엉뚱한 채팅을 치지 않는다.
- **[호명됨] true인 봇은 자기 직전 발언(내 최근 발언)을 그대로 반복하지 않는다.** HP/SP·화면 상황 코멘트로 때우지 말고, 호명한 발화의 내용에 맞춰 새로운 말을 한다. (예: "패드가 편한가요?"라고 물으면 "HP 여유롭네요"가 아니라 "패드 추천드려요" 같이 질문에 답)
- 스트리머 발화가 질문이면(물음표가 없어도 "어디", "뭐", "어떤", "할까" 등 묻는 말투 포함) 호명된 봇이 없더라도 봇 중 최소 1명은 그 질문에 **구체적인 내용으로 직접 답한다**. "글쎄", "음..." 같은 회피 금지.
  · 게임 내 질문(다음 지역/공략/어디로)이면 고인물·분석 성향 봇이 게임 정보를 근거로 실제 답을 제시한다.
  · 게임 추천·취향 질문이면(예: "이거랑 비슷한 게임 뭐 있어?") 시청자답게 실제 게임 제목 등 구체적 답을 1개 이상 댄다.
- 봇은 시청자답게 게임 사실을 틀리거나 아는 척해도 된다. 단 **[정확한 정보] true인 봇은 예외** — 게임 사실은 [게임 정보]·[현재 게임 상황]의 화면 글자·스트리머 발화에 근거한 것만 정확히 말한다(지명·인물·스킬 이름은 [게임 정보] 표기 그대로). 근거가 없으면 지어내지 말고 "그건 확실치 않네요"처럼 말하거나 근거 있는 다른 얘기를 한다. 스트리머의 짐작이나 다른 봇의 틀린 말에 맞장구치지 않고, 틀렸으면 바로잡아도 된다.
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

        known = [str(x) for x in (b.get("known_streamers") or []) if x]
        if known:
            line += f" / 아는 다른 스트리머: {', '.join(known)} (다른 방송 얘기할 때 이 이름을 씀)"

        if b.get("accurate"):
            line += (" / [정확한 정보] true — [게임 정보]·화면 글자에 적힌 사실만 말함. 거기 없는 사실"
                     "(누구 집·진행 순서·보상 등)은 추측해 단정하지 말고 \"그건 확실치 않네요\"")

        facts = b.get("known_facts") or []
        if facts:
            line += " / 이 봇이 검색해 둔 정보: " + "; ".join(f'"{q}" → {a}' for q, a in facts)

        avoid = [str(w) for w in (b.get("avoid_words") or []) if w]
        if avoid:
            line += f" / 말버릇 금지: {', '.join(avoid)}"

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
    # STT 받아쓰기는 오인식이 섞인다("기술" → "술"). 뜬금없는 단어 하나에 봇들이
    # 우르르 끌려가지 않도록 경고를 붙인다 (KNOWN_ISSUES 4-3).
    lines.append(
        "(음성 자동 받아쓰기라 오인식이 섞일 수 있음. 화면 상황·앞뒤 말과 안 맞는 뜬금없는 "
        "단어는 잘못 들린 것일 수 있으니 그 단어 하나에 여러 봇이 꽂히지 말 것. "
        "뜻이 애매하면 1명 정도만 \"뭐라고요?\"처럼 되묻거나 화면 상황 위주로 반응)"
    )
    return "\n".join(lines) + "\n"


def _build_roster_section(roster: list[dict] | None, speaking: list[dict]) -> str:
    """[채팅방 시청자] 섹션 — 이번 라운드 발언자 외 활성 봇까지 이름을 알려준다.
    봇이 다른 시청자를 닉네임으로 부르거나 언급할 수 있게 한다 (KNOWN_ISSUES 4-4)."""
    if not roster:
        return ""
    speaking_names = {b["name"] for b in speaking}
    others = [b["name"] for b in roster if b.get("name") and b["name"] not in speaking_names]
    if not others:
        return ""
    return (
        "[채팅방의 다른 시청자 — 이번 라운드엔 채팅 안 침]\n"
        + ", ".join(others)
        + "\n— 봇 목록의 봇들이 아는 단골이라 닉네임으로 부르거나 언급할 수는 있다. "
        "단, 이 시청자들의 채팅은 절대 생성하지 말 것 (결과 JSON 에는 [봇 목록]의 봇만).\n\n"
    )


def _build_topic_section(avoid_topics: list[str] | None) -> str:
    """최근 라운드에 과하게 나온 소재를 알려 쏠림을 피하게 한다 (KNOWN_ISSUES 4-1/4-2)."""
    topics = [t for t in (avoid_topics or []) if t]
    if not topics:
        return ""
    return (
        "\n[최근 채팅에 너무 자주 나온 소재]\n"
        + ", ".join(topics)
        + "\n— 이번 라운드엔 이 소재를 피하고, 꼭 필요하면 1명만 언급한다.\n"
    )


def _build_mood_section(mood_hint: str | None) -> str:
    """프롬프트의 [채팅창 분위기] 섹션. 흥분 라운드(hype 높음)에만 채워진다.
    hype.build_hype_profile 이 만든 mood_hint 를 받아 LLM 에 톤을 지시한다."""
    hint = (mood_hint or "").strip()
    if not hint:
        return ""
    return f"[채팅창 분위기]\n{hint}\n\n"


def _build_chat_prompt(
    game_context: str,
    bots: list[dict],
    chat_history: list[dict],
    game_profile_context: str = "",
    user_speech: list[dict] | None = None,
    default_speech_weight: str = "medium",
    mentioned_names: list[str] | None = None,
    mood_hint: str | None = None,
    roster: list[dict] | None = None,
    avoid_topics: list[str] | None = None,
    round_notes: str | None = None,
) -> str:
    """모든 provider 가 같은 채팅 프롬프트를 쓰도록 한 곳에서 만든다.

    round_notes: 이번 라운드 대화 과제(누가 무엇에 반응할지) — src/chat/flow.build_round_notes."""
    bots_info = _build_bots_info(bots, chat_history, default_speech_weight, mentioned_names)
    history_text = (
        "\n".join(f"{c['name']}: {c['message']}" for c in chat_history[-10:])
        if chat_history else "(아직 채팅 없음)"
    )
    return _CHAT_PROMPT_TEMPLATE.format(
        game_profile_context=game_profile_context or "(게임 프로필 없음 — 화면만 보고 판단)",
        mood_section=_build_mood_section(mood_hint),
        game_context=_game_context_for_prompt(game_context),
        user_speech_section=_build_user_speech_section(user_speech),
        chat_history=history_text,
        bots_info=bots_info,
        bot_count=len(bots),
        roster_section=_build_roster_section(roster, bots),
        topic_section=_build_topic_section(avoid_topics),
        round_notes=round_notes or "",
    )


def _is_retryable_server_error(msg: str) -> bool:
    """503/UNAVAILABLE/500/overloaded 등 서버 측 일시 혼잡 오류면 True.

    429(quota)는 별도 'retry in Ns' 힌트 경로로 처리하므로 여기서 제외한다."""
    low = msg.lower()
    return any(m in low for m in ("503", "unavailable", "500", "overloaded", "internal error"))


def is_transient_error(err: "Exception | str | None") -> bool:
    """503/UNAVAILABLE/429 등 서버 혼잡성 일시적 오류면 True.

    UI에서 '서버 혼잡' 경고 아이콘을 띄울지 판단하는 데 쓴다."""
    if not err:
        return False
    msg = str(err).lower()
    return any(m in msg for m in ("503", "unavailable", "429", "resource_exhausted", "overloaded"))


class ChatCallDebug:
    """generate_chats 가 마지막 호출 정보를 보관해 호출자가 라운드 로그에 합쳐쓸 수 있게 함."""
    __slots__ = ("provider", "model", "prompt", "raw_response", "elapsed_s", "error", "in_tokens", "out_tokens")

    def __init__(self):
        self.provider: str = ""
        self.in_tokens: int = 0
        self.out_tokens: int = 0
        self.model: str = ""
        self.prompt: str = ""
        self.raw_response: str = ""
        self.elapsed_s: float = 0.0
        # 마지막 호출이 실패했으면 예외 문자열, 성공이면 "". UI 503 경고 판단용.
        self.error: str = ""


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


# 정확한 정보 봇(고인물)의 웹 검색 답 — src/chat/lookup.py 가 라운드와 따로 돌린다.
_LOOKUP_PROMPT = """게임 "{game}" 방송 채팅창의 고인물 시청자 "{bot}"로서 스트리머 질문에 답한다.

[게임 정보 — 이 방송의 게임 프로필. 이름 표기는 이걸 따른다]
{profile}

[지금 화면]
{screen}

[스트리머 질문] "{question}"

절차:
1. 질문을 영어로 바꿔 웹 검색한다(영문 위키·공략이 가장 자세함 — 예: "<영문 게임명> wiki <영문 지명/인물명>").
2. 검색 결과에 **직접 적힌** 사실로만 답한다. 결과끼리 다르거나 직접 적힌 답이 없으면 SKIP.

규칙:
- 이 게임과 관련 없는 질문(일상·잡담·취향·방송 얘기)이면 SKIP 한 단어만 출력.
- 답은 채팅 한 줄(40자 이내)만 출력. 말투: {style}. 인사·출처·링크·따옴표·부연 설명 금지.
- 지명·인물·아이템 이름은 [게임 정보] 표기 그대로, 거기 없으면 한국어판 표기(모르면 원어).
- 스포일러(인물의 정체·배신·죽음·결말) 금지 — 그런 답이면 "스포라 말 아낄게요" 정도로."""


def _build_lookup_prompt(question: str, game: str, bot: dict, screen: str, profile: str = "") -> str:
    return _LOOKUP_PROMPT.format(game=game or "?", bot=bot.get("name", "?"), style=bot.get("style", "간결하게"),
                                 profile=profile or "(없음)", screen=_game_context_for_prompt(screen),
                                 question=question)


def _lookup_text(raw: str) -> str:
    """검색 답에서 채팅 한 줄만 남긴다. SKIP·빈 답이면 "".
    Gemini 는 답 앞에 영어 메모("The search results confirm…")를 붙이기도 해서 한글이 든 첫 줄을 쓴다."""
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    if any(ln.upper().startswith("SKIP") for ln in lines):
        return ""
    korean = [ln for ln in lines if re.search(r"[가-힣]", ln)]
    text = korean[0] if korean else ""
    # 붙어 나온 출처 링크 — "([위키](https://…))", "[위키](https://…)", 맨 URL
    text = re.sub(r"\s*\(?\[[^\]]*\]\([^)]*\)\)?", "", text)
    text = re.sub(r"\s*\(?https?://\S+", "", text).strip()
    while text.endswith(")") and text.count(")") > text.count("("):  # URL 속 괄호가 남긴 꼬리
        text = text[:-1].rstrip()
    text = text.strip("\"'“”‘’` ")
    return "" if not text or text.upper().startswith("SKIP") else text


class GeminiClient:
    def __init__(self, api_key: str, config: dict):
        from google import genai
        self._genai = genai
        self._client = genai.Client(api_key=api_key)
        self._vision_model = config["ai"]["vision_model"]
        self._chat_model = config["ai"]["chat_model"]
        self._vision_prompt = (config["ai"].get("vision_prompt") or "").strip() or None
        # 마지막 chat 호출의 prompt/원본응답/소요시간 — ai_process 가 라운드 블록에 합쳐 찍는 용도
        self.last_chat_call = ChatCallDebug()

    def _call_with_retry(self, fn, max_retries: int = 3):
        for attempt in range(max_retries):
            try:
                return fn()
            except Exception as e:
                msg = str(e)
                last = attempt == max_retries - 1
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    # quota 초과: 서버가 'retry in Ns' 힌트를 주면 그대로 따른다.
                    if last:
                        raise
                    m = re.search(r"retry in (\d+(?:\.\d+)?)s", msg)
                    wait = float(m.group(1)) if m else (2 ** attempt * 2)
                    logger.warning(f"429 quota 초과, {wait:.1f}초 후 재시도 ({attempt+1}/{max_retries})")
                    time.sleep(wait)
                elif _is_retryable_server_error(msg):
                    # 503/UNAVAILABLE/500/overloaded 등 일시적 서버 혼잡:
                    # 채팅은 라운드 주기에 묶여 있어 길게 끌면 라운드가 밀린다.
                    # 짧은 백오프(1s→2s)로 빠르게 1~2회만 재시도.
                    if last:
                        raise
                    wait = min(2 ** attempt, 2)
                    logger.warning(f"서버 혼잡(일시 오류), {wait}초 후 재시도 ({attempt+1}/{max_retries}): {msg[:120]}")
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError("최대 재시도 횟수 초과")

    def analyze_screenshot(self, image_bytes: bytes, game_profile_context: str = "", previous_location: str = "") -> str:
        from google.genai import types
        prompt = _build_vision_prompt(game_profile_context, previous_location, self._vision_prompt)
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
            usage.record("vision", self._vision_model, *_gemini_tokens(response))
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
        roster: list[dict] | None = None,
        avoid_topics: list[str] | None = None,
        round_notes: str | None = None,
    ) -> list[dict]:
        prompt = _build_chat_prompt(
            game_context, bots, chat_history, game_profile_context, user_speech,
            default_speech_weight, mentioned_names, mood_hint, roster, avoid_topics, round_notes,
        )
        raw = ""
        parsed: list[dict] = []
        err = ""
        tokens = (0, 0)
        t0 = time.perf_counter()
        try:
            def _call():
                return self._client.models.generate_content(
                    model=self._chat_model,
                    contents=prompt,
                )
            response = self._call_with_retry(_call)
            tokens = _gemini_tokens(response)
            usage.record("chat", self._chat_model, *tokens)
            raw = (response.text or "").strip()
            parsed = _parse_chats(raw)
        except json.JSONDecodeError as e:
            logger.error(f"채팅 JSON 파싱 실패: {e}")
            err = str(e)
        except Exception as e:
            logger.error(f"채팅 생성 실패: {e}")
            err = str(e)
        finally:
            self.last_chat_call.provider = "gemini"
            self.last_chat_call.model = self._chat_model
            self.last_chat_call.prompt = prompt
            self.last_chat_call.raw_response = raw
            self.last_chat_call.elapsed_s = time.perf_counter() - t0
            self.last_chat_call.error = err
            self.last_chat_call.in_tokens, self.last_chat_call.out_tokens = tokens
        return parsed

    def lookup_answer(self, question: str, game: str, bot: dict, screen: str = "", profile: str = "") -> str:
        """Google 검색 그라운딩으로 질문에 답한 채팅 한 줄. 게임 무관·못 찾음·실패면 ""."""
        from google.genai import types
        prompt = _build_lookup_prompt(question, game, bot, screen, profile)
        try:
            response = self._call_with_retry(lambda: self._client.models.generate_content(
                model=self._chat_model,
                contents=prompt,
                config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
            ))
            usage.record("lookup", self._chat_model, *_gemini_tokens(response))
            return _lookup_text(response.text or "")
        except Exception as e:
            logger.error(f"웹 검색 답 실패: {e}")
            return ""


class ClaudeClient:
    def __init__(self, api_key: str, config: dict):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._vision_model = config["ai"].get("claude_vision_model", "claude-opus-4-7")
        self._chat_model = config["ai"].get("claude_chat_model", "claude-sonnet-4-6")
        self._vision_prompt = (config["ai"].get("vision_prompt") or "").strip() or None
        self.last_chat_call = ChatCallDebug()

    @staticmethod
    def _fast_kwargs(model: str) -> dict:
        """확장 사고를 끄고(지연·비용↓), 5세대 모델은 effort 도 low 로 둔다.
        채팅·vision 은 짧은 정형 출력이라 사고가 품질을 거의 올리지 않는다."""
        kw: dict = {"thinking": {"type": "disabled"}}
        if re.match(r"claude-[a-z]+-5(-\d+)?$", model):  # claude-haiku-5-5, claude-sonnet-5 …
            kw["extra_body"] = {"output_config": {"effort": "low"}}
        return kw

    def analyze_screenshot(self, image_bytes: bytes, game_profile_context: str = "", previous_location: str = "") -> str:
        prompt = _build_vision_prompt(game_profile_context, previous_location, self._vision_prompt)
        try:
            image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
            response = self._client.messages.create(
                model=self._vision_model,
                max_tokens=1024,
                **self._fast_kwargs(self._vision_model),
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
            usage.record("vision", self._vision_model, *_usage_tokens(response))
            return "".join(b.text for b in response.content if b.type == "text").strip()
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
        roster: list[dict] | None = None,
        avoid_topics: list[str] | None = None,
        round_notes: str | None = None,
    ) -> list[dict]:
        prompt = _build_chat_prompt(
            game_context, bots, chat_history, game_profile_context, user_speech,
            default_speech_weight, mentioned_names, mood_hint, roster, avoid_topics, round_notes,
        )
        raw = ""
        parsed: list[dict] = []
        err = ""
        tokens = (0, 0)
        t0 = time.perf_counter()
        try:
            response = self._client.messages.create(
                model=self._chat_model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
                **self._fast_kwargs(self._chat_model),
            )
            tokens = _usage_tokens(response)
            usage.record("chat", self._chat_model, *tokens)
            raw = "".join(b.text for b in response.content if b.type == "text").strip()
            parsed = _parse_chats(raw)
        except json.JSONDecodeError as e:
            logger.error(f"Claude 채팅 JSON 파싱 실패: {e}")
            err = str(e)
        except Exception as e:
            logger.error(f"Claude 채팅 생성 실패: {e}")
            err = str(e)
        finally:
            self.last_chat_call.provider = "claude"
            self.last_chat_call.model = self._chat_model
            self.last_chat_call.prompt = prompt
            self.last_chat_call.raw_response = raw
            self.last_chat_call.elapsed_s = time.perf_counter() - t0
            self.last_chat_call.error = err
            self.last_chat_call.in_tokens, self.last_chat_call.out_tokens = tokens
        return parsed


class OpenAIClient:
    """OpenAI Responses API 로 vision/채팅. 추론(reasoning)은 가능한 한 끈다.

    모델마다 허용하는 reasoning.effort 가 달라(gpt-6-luna 는 "none", gpt-5-nano 는
    "minimal" 부터, gpt-4.1·4o 계열은 reasoning 자체가 없음) 거절되면 다음 단계로 내려가고
    모델별로 기억한다.
    """

    _EFFORT_STEPS = ("none", "minimal", None)
    # 모델별로 통과한 단계 — 클라이언트를 다시 만들어도(설정 변경) 재탐색하지 않게 클래스에 둔다.
    _effort_step: dict[str, int] = {}

    def __init__(self, api_key: str, config: dict):
        import openai
        self._openai = openai
        # SDK 자체 재시도(429/5xx 백오프)를 쓰되, 라운드가 밀리지 않게 짧게.
        self._client = openai.OpenAI(api_key=api_key, max_retries=2, timeout=30.0)
        ai = config["ai"]
        self._vision_model = ai.get(model_config_key("openai", "vision")) or DEFAULT_MODELS["openai"]["vision"]
        self._chat_model = ai.get(model_config_key("openai", "chat")) or DEFAULT_MODELS["openai"]["chat"]
        self._vision_prompt = (ai.get("vision_prompt") or "").strip() or None
        self.last_chat_call = ChatCallDebug()

    def _create(self, model: str, **kw):
        step = self._effort_step.get(model, 0)
        while True:
            effort = self._EFFORT_STEPS[step]
            extra = {"reasoning": {"effort": effort}} if effort else {}
            try:
                return self._client.responses.create(model=model, **extra, **kw)
            except self._openai.BadRequestError as e:
                if "reasoning" not in str(e).lower() or step + 1 >= len(self._EFFORT_STEPS):
                    raise
                step += 1
                self._effort_step[model] = step
                logger.info(f"{model}: reasoning.effort={effort} 미지원 → {self._EFFORT_STEPS[step] or '(미지정)'}")

    def analyze_screenshot(self, image_bytes: bytes, game_profile_context: str = "", previous_location: str = "") -> str:
        prompt = _build_vision_prompt(game_profile_context, previous_location, self._vision_prompt)
        try:
            image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
            response = self._create(
                self._vision_model,
                instructions=_VISION_SYSTEM,
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{image_b64}"},
                        {"type": "input_text", "text": prompt},
                    ],
                }],
                max_output_tokens=1024,
            )
            usage.record("vision", self._vision_model, *_usage_tokens(response))
            return (response.output_text or "").strip() or VISION_ERROR_MARKER
        except Exception as e:
            logger.error(f"OpenAI Vision 분석 실패: {e}")
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
        roster: list[dict] | None = None,
        avoid_topics: list[str] | None = None,
        round_notes: str | None = None,
    ) -> list[dict]:
        prompt = _build_chat_prompt(
            game_context, bots, chat_history, game_profile_context, user_speech,
            default_speech_weight, mentioned_names, mood_hint, roster, avoid_topics, round_notes,
        )
        raw = ""
        parsed: list[dict] = []
        err = ""
        tokens = (0, 0)
        t0 = time.perf_counter()
        try:
            response = self._create(self._chat_model, input=prompt, max_output_tokens=1024)
            tokens = _usage_tokens(response)
            usage.record("chat", self._chat_model, *tokens)
            raw = (response.output_text or "").strip()
            parsed = _parse_chats(raw)
        except json.JSONDecodeError as e:
            logger.error(f"OpenAI 채팅 JSON 파싱 실패: {e}")
            err = str(e)
        except Exception as e:
            logger.error(f"OpenAI 채팅 생성 실패: {e}")
            err = str(e)
        finally:
            self.last_chat_call.provider = "openai"
            self.last_chat_call.model = self._chat_model
            self.last_chat_call.prompt = prompt
            self.last_chat_call.raw_response = raw
            self.last_chat_call.elapsed_s = time.perf_counter() - t0
            self.last_chat_call.error = err
            self.last_chat_call.in_tokens, self.last_chat_call.out_tokens = tokens
        return parsed

    def lookup_answer(self, question: str, game: str, bot: dict, screen: str = "", profile: str = "") -> str:
        """web_search 도구로 질문에 답한 채팅 한 줄. 게임 무관·못 찾음·실패면 ""."""
        prompt = _build_lookup_prompt(question, game, bot, screen, profile)
        try:
            response = self._create(self._chat_model, input=prompt, tools=[{"type": "web_search"}],
                                    max_output_tokens=1024)
            usage.record("lookup", self._chat_model, *_usage_tokens(response))
            return _lookup_text(response.output_text or "")
        except Exception as e:
            logger.error(f"OpenAI 웹 검색 답 실패: {e}")
            return ""


PROVIDERS = ("gemini", "claude", "openai")

# provider 별 기본 모델 (config 에 값이 없을 때).
DEFAULT_MODELS = {
    "gemini": {"vision": "gemini-2.5-flash", "chat": "gemini-2.5-flash-lite"},
    "claude": {"vision": "claude-haiku-5-5", "chat": "claude-haiku-5-5"},
    "openai": {"vision": "gpt-6-luna", "chat": "gpt-6-luna"},
}


def model_config_key(provider: str, kind: str) -> str:
    """config["ai"] 에서 provider·용도(vision|chat)별 모델 이름이 들어 있는 키.
    gemini 는 예전 키(vision_model/chat_model)를 그대로 쓴다."""
    base = f"{kind}_model"
    return base if provider == "gemini" else f"{provider}_{base}"


def selected_model(config: dict, kind: str) -> tuple[str, str]:
    """현재 설정된 (provider, model). kind = "vision" | "chat"."""
    ai = config.get("ai") or {}
    provider = str(ai.get(f"{kind}_provider", "gemini")).lower()
    if provider not in PROVIDERS:
        provider = "gemini"
    return provider, ai.get(model_config_key(provider, kind)) or DEFAULT_MODELS[provider][kind]


# 고른 provider 의 키가 없을 때 대신 쓸 순서. 비용·품질이 무난한 순.
_FALLBACK_ORDER = ("openai", "gemini", "claude")


def resolve_provider(config: dict, kind: str, keys: dict[str, str | None]) -> tuple[str, str, str | None]:
    """실제로 쓸 (provider, model, 대체되기 전 provider|None).

    고른 provider 의 키가 없으면 가지고 있는 키 중 하나(_FALLBACK_ORDER)의 기본 모델로 대체한다.
    키가 하나도 없으면 ValueError — 이 경우 main 은 AI 프로세스를 띄우지 않는다."""
    provider, model = selected_model(config, kind)
    if keys.get(provider):
        return provider, model, None
    for alt in _FALLBACK_ORDER:
        if keys.get(alt):
            alt_model = (config.get("ai") or {}).get(model_config_key(alt, kind)) or DEFAULT_MODELS[alt][kind]
            return alt, alt_model, provider
    raise ValueError("API 키가 하나도 없습니다")


def _create_client(kind: str, config: dict, gemini_api_key: str | None,
                   claude_api_key: str | None, openai_api_key: str | None):
    keys = {"gemini": gemini_api_key, "claude": claude_api_key, "openai": openai_api_key}
    provider, model, fell_from = resolve_provider(config, kind, keys)
    if fell_from:
        # 키 없이 고른 provider 는 AI 프로세스가 죽지 않도록 있는 키로 대체한다.
        logger.warning(f"{kind}: {fell_from} 키가 없어 {provider}/{model} 로 대체합니다")
        config = {**config, "ai": {**(config.get("ai") or {}), model_config_key(provider, kind): model}}
    if provider == "claude":
        return ClaudeClient(api_key=claude_api_key, config=config)
    if provider == "openai":
        return OpenAIClient(api_key=openai_api_key, config=config)
    return GeminiClient(api_key=gemini_api_key, config=config)


def create_vision_client(config: dict, gemini_api_key: str | None, claude_api_key: str | None = None,
                         openai_api_key: str | None = None):
    """config["ai"]["vision_provider"] 에 따라 클라이언트 반환. 키가 없으면 있는 키로 대체."""
    return _create_client("vision", config, gemini_api_key, claude_api_key, openai_api_key)


def create_chat_client(config: dict, gemini_api_key: str | None, claude_api_key: str | None = None,
                       openai_api_key: str | None = None):
    """config["ai"]["chat_provider"] 에 따라 채팅 전용 클라이언트 반환. 키가 없으면 있는 키로 대체."""
    return _create_client("chat", config, gemini_api_key, claude_api_key, openai_api_key)


def create_lookup_client(config: dict, gemini_api_key: str | None, claude_api_key: str | None = None,
                         openai_api_key: str | None = None):
    """정확한 정보 봇의 웹 검색용 클라이언트. 채팅 provider 와 상관없이 키가 있는 openai(openai_chat_model)
    → gemini(chat_model) 순 — gemini flash-lite 는 검색 답이 부정확해서 openai 우선. 둘 다 없으면 None(검색 끔)."""
    keys = {"gemini": gemini_api_key, "claude": claude_api_key, "openai": openai_api_key}
    for p in ("openai", "gemini"):
        if keys.get(p):
            cfg = {**config, "ai": {**(config.get("ai") or {}), "chat_provider": p}}
            return _create_client("chat", cfg, gemini_api_key, claude_api_key, openai_api_key)
    return None


def check_api_key(provider: str, api_key: str) -> tuple[bool, str]:
    """키가 실제로 동작하는지 가벼운 목록 조회로 확인한다(과금 없음). (성공 여부, 안내 문구).

    설정 창의 '연결 확인' 버튼용. SDK import 를 이 파일에 가두기 위해 여기 둔다.
    OpenAI 키 확인은 STT 쪽 진입점(src/stt/transcriber.check_openai_key)에 있다.
    """
    if not api_key:
        return False, "키가 없습니다"
    try:
        if provider == "gemini":
            from google import genai
            client = genai.Client(api_key=api_key)
            next(iter(client.models.list(config={"page_size": 1})), None)
        elif provider == "claude":
            import anthropic
            anthropic.Anthropic(api_key=api_key).models.list(limit=1)
        else:
            return False, f"알 수 없는 provider: {provider}"
        return True, "연결됨"
    except Exception as e:
        msg = str(e)
        if "API_KEY_INVALID" in msg or "401" in msg or "authentication" in msg.lower():
            return False, "키가 올바르지 않습니다"
        return False, msg[:120]
