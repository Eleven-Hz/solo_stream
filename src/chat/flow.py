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
# 받아쓰기는 물음표를 자주 빠뜨려서 어미·의문사로도 질문을 잡는다. 단 의문사만 보면
# "어떻게 구워줬던 걸로 기억하는데"처럼 서술문까지 질문이 돼 봇이 답을 지어내므로,
# 의문사는 문장이 서술 어미로 끝나지 않을 때만 인정한다.
_Q_END_RE = re.compile(r"(까요|나요|가요|ㄹ까|을까|할까|건가|던가|더라|뭐죠|뭐지|뭐야|냐|니)$")
_Q_WORD_RE = re.compile(r"뭐|무슨|누구|어디|어떤|어떻게|왜|몇")
# 끝의 "뭐"는 "딱히 뭐", "인데 뭐" 같은 군말
_STATEMENT_END_RE = re.compile(r"(데|다|네|군|고|서|게|뭐)$")


def _is_question(text: str) -> bool:
    for sent in re.split(r"(?<=[.?!])\s+|\s*/\s*", text):
        s = sent.strip()
        if "?" in s and len(s.strip("?!.~ ")) > 1:  # "어?" 같은 감탄은 제외
            return True
        s = s.rstrip(".!~… ")
        if not s:
            continue
        if _Q_END_RE.search(s):
            return True
        if _Q_WORD_RE.search(s) and not _STATEMENT_END_RE.search(s):
            return True
    return False


def speech_kind(text: str) -> str:
    """발화 성격: greeting / bye / question / plan / statement."""
    t = text or ""
    if _GREETING_RE.search(t):
        return "greeting"
    if _BYE_RE.search(t):
        return "bye"
    if _is_question(t):
        return "question"
    if _PLAN_RE.search(t):
        return "plan"
    return "statement"


_SENT_SPLIT_RE = re.compile(r"(?<=[.?!])\s+|\s*/\s*")
MAX_TOPICS = 3
_KIND_SCORE = {"question": 3, "greeting": 3, "bye": 3, "plan": 2, "statement": 1}


def split_topics(speech: list[dict]) -> list[dict]:
    """새 발화를 주제 단위로 나눈다 — [{"text", "kind", "order"}] 시간순.

    받아쓰기 한 덩어리 안에서도 질문·계획·인사 문장마다 따로 나누고, 그 뒤 서술은 같은 덩어리 안에서
    앞 주제에 붙인다("시트론 집이었나? 루크 집이었던 것 같은데."는 한 주제). "어허." 같은 군말은 뺀다."""
    units: list[list[str]] = []
    for u in speech:
        cur: list[str] | None = None
        for s in (x.strip() for x in _SENT_SPLIT_RE.split(u.get("text") or "")):
            if not s:
                continue
            if cur is None or speech_kind(s) != "statement":
                cur = [s]
                units.append(cur)
            else:
                cur.append(s)
    topics = []
    for parts in units:
        text = " ".join(parts)
        kind = speech_kind(text)
        if kind == "statement" and len(re.sub(r"[\s.?!~…,]", "", text)) <= 3:
            continue
        topics.append({"text": text, "kind": kind, "order": len(topics)})
    return topics


def assign_topics(
    topics: list[dict],
    bots: list[dict],
    need: int,
    *,
    rng: random.Random | None = None,
) -> list[tuple[dict, list[str]]]:
    """주제별로 반응할 봇을 정한다 — [(주제, [봇 이름])] 시간순.

    질문·인사 > 계획 > 서술 순, 같으면 가장 최근 말을 우선해 MAX_TOPICS 개까지 고른다.
    이름이 불린 봇은 그 말을 맡고, 고른 주제마다 최소 1명, 남은 인원(need 까지)은 점수 높은 주제에 더 준다.
    봇은 [스트리머 발화 반응]이 높은 순(off 제외)으로 배정해 반응 약한 봇이 질문을 떠안지 않게 한다."""
    from src.chat.persona_loader import normalize_user_speech_weight

    rng = rng or random
    if not topics or not bots:
        return []
    last = topics[-1]["order"]

    def score(t: dict) -> int:
        return _KIND_SCORE.get(t["kind"], 1) + (1 if t["order"] == last else 0)

    limit = min(MAX_TOPICS, len(bots), max(1, need))
    chosen = sorted(sorted(topics, key=lambda t: (score(t), t["order"]), reverse=True)[:limit],
                    key=lambda t: t["order"])

    order = {"high": 0, "medium": 1, "low": 2}
    pool = [b for b in bots if normalize_user_speech_weight(b.get("user_speech_weight"), default="medium") != "off"]
    rng.shuffle(pool)
    pool.sort(key=lambda b: order.get(normalize_user_speech_weight(b.get("user_speech_weight"), default="medium"), 1))

    picks: dict[int, list[str]] = {t["order"]: [] for t in chosen}
    for b in list(pool):
        keys = [str(k).replace(" ", "") for k in [b["name"], *(b.get("aliases") or [])] if k]
        for t in chosen:
            if any(k in t["text"].replace(" ", "") for k in keys):
                picks[t["order"]].append(b["name"])
                pool.remove(b)
                break
    by_score = sorted(chosen, key=lambda t: (score(t), t["order"]), reverse=True)
    for t in by_score:
        if not picks[t["order"]] and pool:
            picks[t["order"]].append(pool.pop(0)["name"])
    while pool and sum(len(v) for v in picks.values()) < need:
        t = min(by_score, key=lambda t: len(picks[t["order"]]) / score(t))
        picks[t["order"]].append(pool.pop(0)["name"])
    return [(t, picks[t["order"]]) for t in chosen if picks[t["order"]]]


def build_round_notes(
    *,
    speech: list[dict] | None,
    bots: list[dict],
    reply: tuple[str, dict] | None = None,
    speech_round: bool = False,
    rng: random.Random | None = None,
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
        topics = split_topics(fresh)
        assigned = assign_topics(topics, bots, need, rng=rng) if len(topics) > 1 else []
        if assigned:
            # 여러 말을 한꺼번에 던지면 LLM 은 봇마다 다 섞어 답하거나 마지막 말에만 몰린다
            # → 어느 봇이 어느 말을 맡을지 코드가 정한다
            lines.append("- 스트리머가 새로 한 말이 여러 개다 → 봇마다 아래에서 맡은 말에만 반응한다 "
                         "(맞장구·대답·되묻기·놀리기 등 페르소나대로). 한 채팅에 여러 말을 한꺼번에 답하지 않는다.")
            for t, names in assigned:
                label = " (질문)" if t["kind"] == "question" else ""
                lines.append(f'  · "{t["text"]}"{label} → {", ".join(names)}')
            lines.append("  나머지 봇은 화면·다른 채팅 얘기를 하거나 위 말 중 하나에 가볍게 반응해도 된다.")
        else:
            quoted = " / ".join(f'"{u["text"].strip()}"' for u in fresh)
            lines.append(f"- 스트리머가 새로 한 말: {quoted} → 봇 {need}명 이상이 이 말에 직접 반응한다 "
                         "(맞장구·대답·되묻기·놀리기 등 페르소나대로). 나머지는 화면·다른 채팅 얘기를 해도 된다.")
        kinds = {speech_kind(u["text"]) for u in fresh}
        if "greeting" in kinds:
            lines.append("- 스트리머가 인사했다 → 반응하는 봇은 먼저 인사로 받는다 (\"하이~\", \"ㅎㅇㅎㅇ\", \"오셨어요\" 등 각자 말투로).")
        if "bye" in kinds:
            lines.append("- 스트리머가 작별 인사를 했다 → 인사로 배웅한다.")
        if "question" in kinds:
            who = "질문을 맡은 봇은" if assigned else "최소 1명은"
            lines.append(f"- 질문이 있다 → {who} 구체적인 내용으로 답한다(회피 금지).")
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
