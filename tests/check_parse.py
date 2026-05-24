import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai.model_client import _parse_chats

cases = {
    "trailing comma + fence": """```json
[
  {"name": "겜린이", "message": "아니 이 퀘스트 뭐임?"},
  {"name": "백수형", "message": "스킵 ㄱㄱ"},
]
```""",
    "no fence ok": """[
  {"name": "공포팬", "message": "여기 좀 무섭네"},
  {"name": "분석충", "message": "냉기 약점이네"}
]""",
    "with extra explain": """여기 응답입니다:
```json
[
  {"name": "도네요정", "message": "레아 파이팅!"}
]
```
끝.""",
    "duplicate names": """[
  {"name": "겜린이", "message": "첫번째"},
  {"name": "겜린이", "message": "두번째 (중복)"},
  {"name": "백수형", "message": "ㅋㅋ"}
]""",
}

for label, sample in cases.items():
    print(f"--- {label} ---")
    try:
        out = _parse_chats(sample)
        print(f"OK {len(out)}개: {[c['name'] for c in out]}")
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")
    print()
