"""
대화 흐름 — 채팅이 '따로 노는 한마디 모음'이 아니라 이어지는 대화처럼 보이게 하는 장치들.

2026-10-15 실사용 로그(36라운드) 분석에서 나온 문제와 대응:
  - 스트리머 말에 5명 중 0~2명만 반응, 인사도 무시 → 라운드 과제(round notes)로 반응 인원을 지정
  - 봇끼리 받아치기 0회 → 코드가 받아칠 봇·대상 채팅을 정해서 프롬프트에 지시
  - 매 라운드 발언자 전원 교체 → 직전 라운드 발언자 일부를 이어서 출석(carry over)
  - 지금 장소·상황 단어(보스·정글)까지 '과열 소재'로 피함 → 현재 화면·발화에 나온 소재는 회피 면제
  - 봇 말버릇 반복("새벽두부 방송도…" 5회) → 봇별 최근 발언에서 반복 단어를 찾아 금지
  - STT 가 게임 고유명사를 틀림 → 프로필에서 고유명사를 뽑아 받아쓰기 힌트로

LLM 은 "30% 확률로" 같은 확률 지시를 지키지 못한다(받아치기 빈도 70~90% 설정에도 0회).
그래서 확률·선정은 코드가 하고, 프롬프트에는 "누가 무엇을" 만 명시한다.
"""
from __future__ import annotations

import random
import re
from collections import Counter, deque

from src.chat.topic_guard import extract_keys

_GREETING_RE = re.compile(r"안녕|하이|ㅎㅇ|반갑|어서\s*오|좋은\s*(아침|저녁|밤)|왔어요|왔습니다")
_BYE_RE = re.compile(r"잘\s*가|다음에\s*봐|ㅂㅂ|바이|수고|들어가|마치겠|끝내겠")
_PLAN_RE = re.compile(r"가보|가봅|해보|해봅|갑시다|합시다|하죠|보시죠|가시죠|볼까|할게|갈게")


def speech_kind(text: str) -> str:
    """발화 성격: greeting / bye / question / plan / statement."""
    t = text or ""
    if _GREETING_RE.search(t):
        return "greeting"
    if _BYE_RE.search(t):
        return "bye"
    if "?" in t or re.search(r"(까요|나요|가요|ㄹ까|을까|할까|뭐죠|뭐지|뭐야|누구|어디|어떤|어떻게|왜|몇)", t):
        return "question"
    if _PLAN_RE.search(t):
        return "plan"
    return "statement"


def build_round_notes(
    *,
    speech: list[dict] | None,
    bots: list[dict],
    reply: tuple[str, dict] | None = None,
    speech_round: bool = False,
) -> str:
    """이번 라운드의 대화 과제 — 누가 무엇에 반응해야 하는지 명시한다.

    speech 항목: {"text", "seconds_ago", "reacted"(이전 라운드에서 이미 반응했으면 True)}
    reply: (받아칠 봇 이름, 대상 채팅 {"name","message"})
    """
    lines: list[str] = []
    n = len(bots)
    fresh = [u for u in (speech or []) if not u.get("reacted") and (u.get("text") or "").strip()]
    if speech_round:
        lines.append("- 이번 라운드는 스트리머가 **방금 한 말에 바로 반응하는 라운드**다. "
                     "화면은 직전과 같으니 화면 묘사를 반복하지 말고 발화에 반응한다.")
    if fresh:
        need = n if speech_round else max(2, (n + 1) // 2)
        quoted = " / ".join(f'"{u["text"].strip()}"' for u in fresh)
        lines.append(f"- 스트리머가 새로 한 말: {quoted} → 봇 {need}명 이상이 이 말에 직접 반응한다 "
                     "(맞장구·대답·되묻기·놀리기 등 페르소나대로). 나머지는 화면·다른 채팅 얘기를 해도 된다.")
        kinds = {speech_kind(u["text"]) for u in fresh}
        if "greeting" in kinds:
            lines.append("- 스트리머가 인사했다 → 반응하는 봇은 먼저 인사로 받는다 (\"하이~\", \"ㅎㅇㅎㅇ\", \"오셨어요\" 등 각자 말투로).")
        if "bye" in kinds:
            lines.append("- 스트리머가 작별 인사를 했다 → 인사로 배웅한다.")
        if "question" in kinds:
            lines.append("- 질문이 있다 → 최소 1명은 구체적인 내용으로 답한다(회피 금지).")
        if "plan" in kinds:
            lines.append("- 스트리머가 할 일을 말했다 → 그 계획(장소·행동·이름)을 직접 언급하며 반응한다(응원·훈수·놀리기).")
    elif speech:
        lines.append("- 스트리머 발화는 이전 라운드에서 이미 반응했다 → 이어서 말하고 싶은 봇 1명 정도만 반응한다.")
    if reply:
        replier, target = reply
        lines.append(f"- {replier}는 [최근 채팅 기록]의 {target['name']}: \"{target['message']}\" 에 "
                     f"받아친다 — 반박·질문·놀리기·덧붙이기 중 하나로, {target['name']} 닉네임을 불러도 된다. '○○ 말 맞아' 같은 동의만 하지 말고 자기 말을 더한다.")
    if not lines:
        return ""
    return "\n[이번 라운드 대화 과제 — 반드시 지킬 것]\n" + "\n".join(lines) + "\n"


def pick_batch(
    pool: list[dict],
    size: int,
    *,
    must: list[dict] | None = None,
    prev_names: list[str] | None = None,
    carry: int = 1,
    rng: random.Random | None = None,
) -> list[dict]:
    """이번 라운드 발언자. 호명 봇(must) → 직전 라운드 발언자 carry 명 → 나머지 무작위."""
    rng = rng or random
    batch = list((must or [])[:size])
    names = {b["name"] for b in batch}
    prev = [b for b in pool if b["name"] in set(prev_names or []) and b["name"] not in names]
    for b in rng.sample(prev, min(max(0, carry), len(prev), size - len(batch))):
        batch.append(b)
        names.add(b["name"])
    rest = [b for b in pool if b["name"] not in names]
    batch += rng.sample(rest, min(size - len(batch), len(rest)))
    return batch


def pick_reply(
    batch: list[dict],
    last_chats: list[dict],
    *,
    chance: float = 0.7,
    exclude: set[str] | None = None,
    rng: random.Random | None = None,
) -> tuple[str, dict] | None:
    """받아칠 봇과 대상 채팅을 고른다. 봇의 react_to_others/react_chance 를 코드에서 적용한다."""
    rng = rng or random
    if not last_chats or rng.random() >= chance:
        return None
    exclude = exclude or set()
    cands = [b for b in batch if b.get("react_to_others", True) and b["name"] not in exclude]
    rng.shuffle(cands)
    for b in cands:
        if rng.random() >= float(b.get("react_chance", 1.0)):
            continue
        targets = [c for c in last_chats if c.get("name") != b["name"] and c.get("message")]
        if not targets:
            continue
        # 질문·감정이 실린 채팅이 받아치기 좋다
        targets.sort(key=lambda c: (("?" in c["message"]) or ("ㅠ" in c["message"])), reverse=True)
        return b["name"], rng.choice(targets[:2])
    return None


def exempt_current(hot_keys: list[str], game_context: str, speech_texts: list[str]) -> list[str]:
    """지금 화면·발화에 나온 소재는 '과열'이어도 피하지 않는다 — 보스전 중에 '보스'를 피하면 흐름이 끊긴다."""
    now = " ".join([game_context or ""] + list(speech_texts or []))
    return [k for k in hot_keys if k not in now]


class BotHabits:
    """봇별 최근 발언에서 반복되는 단어(말버릇)를 찾는다."""

    def __init__(self, depth: int = 8, min_count: int = 2):
        self._depth = depth
        self._min = min_count
        self._msgs: dict[str, deque] = {}

    def push(self, chats: list[dict]) -> None:
        for c in chats:
            name = c.get("name")
            if name:
                self._msgs.setdefault(name, deque(maxlen=self._depth)).append(
                    extract_keys(c.get("message", ""), []))

    def clear(self) -> None:
        self._msgs.clear()

    def habits(self, name: str, exempt_text: str = "") -> list[str]:
        cnt: Counter = Counter()
        for keys in self._msgs.get(name, []):
            cnt.update(keys)
        return [k for k, n in cnt.most_common() if n >= self._min and k not in exempt_text][:4]

    def annotate(self, bots: list[dict], exempt_text: str = "") -> list[dict]:
        """봇 dict 사본에 avoid_words 를 붙인다(프롬프트 봇 라인에 '말버릇 금지'로 표시)."""
        out = []
        for b in bots:
            words = self.habits(b["name"], exempt_text)
            out.append({**b, "avoid_words": words} if words else b)
        return out

    def drop_habit_chats(self, chats: list[dict], bots: list[dict],
                         exempt_names: set[str] | None = None) -> tuple[list[dict], list[tuple[dict, str]]]:
        """금지한 말버릇을 그대로 쓴 채팅은 버린다(호명 봇 면제)."""
        avoid = {b["name"]: b.get("avoid_words") or [] for b in bots}
        kept, dropped = [], []
        for c in chats:
            hit = next((w for w in avoid.get(c.get("name"), []) if w in (c.get("message") or "")), None)
            if hit and c.get("name") not in (exempt_names or set()):
                dropped.append((c, hit))
            else:
                kept.append(c)
        return kept, dropped


def _head(item: str) -> list[str]:
    """'마름모 광장 (Rhombus Square): 설명' → ['마름모 광장'], 'A (a) / B (b): …' → ['A', 'B']."""
    head = str(item).split(":", 1)[0]
    out = []
    for part in head.split(" / "):
        part = re.sub(r"\s*\([^)]*\)", "", part).strip(" '\"")
        if 2 <= len(part) <= 20:
            out.append(part)
    return out


def stt_hint_terms(profile: dict | None, limit: int = 60) -> list[str]:
    """받아쓰기 힌트용 고유명사 — 프로필의 stt_terms(직접 지정) + 인물·지명·적·기술 이름."""
    if not profile:
        return []
    terms: list[str] = []
    name = str(profile.get("name") or "")
    terms += [t.strip() for t in re.split(r"[()]", name) if t.strip()]
    terms += [str(t).strip() for t in (profile.get("stt_terms") or []) if str(t).strip()]
    for key in ("characters", "locations", "enemies", "skills"):
        for item in profile.get(key) or []:
            terms += _head(item)
    seen, out = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:limit]


def stt_prompt_with_terms(base: str | None, terms: list[str]) -> str:
    base = (base or "").strip()
    if not terms:
        return base
    return (base + "\n" if base else "") + "자주 나오는 게임 고유명사: " + ", ".join(terms)
