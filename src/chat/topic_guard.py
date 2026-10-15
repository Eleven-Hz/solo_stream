"""
소재(클리셰) 쏠림 억제 (KNOWN_ISSUES 4-1 + 4-2).

로그 분석 결과 "같은 말 반복"의 정체는 문장 복붙이 아니라 **한 라운드에서 여러 봇이
같은 소재로 몰리는 것**("렉" 5.9% 등)이었다. 두 단계로 막는다.

  1) 프롬프트 힌트: 최근 N라운드에 자주 나온 소재를 뽑아 "이번엔 피하라"고 알려준다.
  2) 후처리: 한 라운드 안에서 같은 소재를 쓰는 봇 수를 제한(기본 2명, 최근 과열 소재는 1명).

형태소 분석기 없이 동작하도록 단순 토큰화(한글/영숫자 덩어리 + 조사 떼기)를 쓴다.
"렉"처럼 한 글자짜리 클리셰는 토큰 길이 조건(2자 이상)에 걸리므로 별도 목록
(CLICHE_KEYWORDS, config chat.cliche_keywords)으로 부분 문자열 매칭한다.
"""
import re
from collections import Counter, deque

# 기본 클리셰 키워드(부분 문자열 매칭). config chat.cliche_keywords 로 덮어쓸 수 있다.
# 한 글자짜리를 넣으면 다른 단어 속에서도 잡히므로("술" → "기술") 신중히 고른다.
DEFAULT_CLICHE_KEYWORDS = ["렉", "버벅", "프레임", "끊기", "끊김", "서버"]

# 흔한 추임새/지시어/서술어 — 이것들이 겹치는 건 소재 쏠림이 아니다.
_STOPWORDS = {
    "진짜", "정말", "그냥", "이거", "저거", "그거", "이게", "저게", "그게", "이건", "저건", "그건",
    "여기", "저기", "거기", "지금", "이제", "근데", "그런데", "너무", "완전", "아니", "아니네",
    "오늘", "어제", "다음", "여긴", "거긴", "저긴", "우리", "저희", "다들", "이번", "역시", "약간", "조금", "많이", "계속", "벌써", "아직",
    "뭐야", "뭐지", "뭔가", "무슨", "어떻게", "왜케", "갑자기", "그래", "그래도", "맞아", "맞죠",
    "있네", "없네", "있다", "없다", "있음", "없음", "하는", "하네", "했네", "해요", "하세요",
    "같은데", "같아요", "같음", "같다", "같네", "보니", "보는", "보면", "봐요", "봤는데",
    "스트리머", "스트리머님", "형님", "방송", "채팅", "시청자", "ㅇㅇ",
    "개웃", "웃기네", "대박", "미쳤다", "미친", "ㄹㅇ", "레전드",
}

# 긴 것부터 떼어야 "에서"가 "서"보다 먼저 맞는다.
_JOSA = sorted(
    ["에서는", "에서", "으로", "이랑", "까지", "부터", "처럼", "보다", "한테", "에게",
     "는", "은", "이", "가", "을", "를", "도", "만", "에", "로", "랑", "의"],
    key=len, reverse=True,
)

_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")


def _strip_josa(tok: str) -> str:
    for j in _JOSA:
        if tok.endswith(j) and len(tok) - len(j) >= 2:
            return tok[: -len(j)]
    return tok


def extract_keys(message: str, cliche_keywords: list[str] | None = None) -> set[str]:
    """메시지에서 소재 키 집합을 뽑는다: 클리셰 키워드(부분 문자열) + 2자 이상 내용어."""
    text = message or ""
    keys: set[str] = set()
    compact = "".join(text.split())
    for kw in cliche_keywords if cliche_keywords is not None else DEFAULT_CLICHE_KEYWORDS:
        if kw and kw in compact:
            keys.add(kw)
    for tok in _TOKEN_RE.findall(text):
        tok = _strip_josa(tok.lower())
        if len(tok) >= 2 and tok not in _STOPWORDS:
            keys.add(tok)
    return keys


class TopicTracker:
    """최근 N라운드의 발화를 보관하고 과열 소재(hot keys)를 계산한다."""

    def __init__(
        self,
        window_rounds: int = 6,
        hot_min_count: int = 3,
        cliche_keywords: list[str] | None = None,
    ):
        self._rounds: deque[list[set[str]]] = deque(maxlen=max(1, window_rounds))
        self._hot_min = max(2, hot_min_count)
        self.cliche_keywords = (
            list(cliche_keywords) if cliche_keywords is not None else list(DEFAULT_CLICHE_KEYWORDS)
        )

    def push_round(self, messages: list[str]) -> None:
        self._rounds.append([extract_keys(m, self.cliche_keywords) for m in messages])

    def clear(self) -> None:
        self._rounds.clear()

    def hot_keys(self, top: int = 5) -> list[str]:
        """최근 윈도우에서 서로 다른 발화 hot_min_count 개 이상에 나온 소재(빈도순)."""
        cnt: Counter = Counter()
        for rnd in self._rounds:
            for keys in rnd:
                cnt.update(keys)
        return [k for k, n in cnt.most_common() if n >= self._hot_min][:top]


def limit_round_topics(
    chats: list[dict],
    *,
    hot_keys: list[str] | None = None,
    cliche_keywords: list[str] | None = None,
    per_round_max: int = 2,
    hot_max: int = 1,
    exempt_names: set[str] | None = None,
) -> tuple[list[dict], list[tuple[dict, str]]]:
    """한 라운드 안에서 같은 소재를 쓰는 봇 수를 제한한다.

    - 일반 소재: 최대 per_round_max 명
    - 최근 과열 소재(hot_keys): 최대 hot_max 명
    - exempt_names(호명된 봇)는 제한에서 면제하지만 카운트에는 포함한다.
    LLM 이 낸 순서대로 먼저 나온 봇을 남긴다. (남은 채팅, [(드롭된 채팅, 이유 소재)]) 반환.
    """
    hot = set(hot_keys or [])
    exempt = exempt_names or set()
    used: Counter = Counter()
    kept: list[dict] = []
    dropped: list[tuple[dict, str]] = []
    for c in chats:
        keys = extract_keys(c.get("message", ""), cliche_keywords)
        over = next(
            (k for k in sorted(keys) if used[k] >= (hot_max if k in hot else per_round_max)),
            None,
        )
        if over is not None and c.get("name") not in exempt:
            dropped.append((c, over))
            continue
        used.update(keys)
        kept.append(c)
    return kept, dropped
