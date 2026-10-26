"""solo_stream 개발 현황판 — 저장소에서 숫자를 뽑아 HTML 한 장(out/dashboard.html)으로 만든다.

실행: 루트의 open-dashboard.bat(서버 server.py 를 띄워 승인·반려까지 된다)
      또는 venv/Scripts/python scripts/dashboard/build.py (파일만, 보기 전용)
검토 요청(proposals/)도 같이 싣는다 — 승인 전 요청은 data/ 를 바꾸지 않는다(proposals.py)

코드에서 읽는 것: 버전, 지금 기본 모델(config.yaml), 기본 봇(personas/default_bots.yaml), 게임 프로필,
                 알려진 문제 표(docs/KNOWN_ISSUES.md), 평가 러너, src 코드 줄 수, 커밋.
손으로 적는 것(data/): features.json 기능 상태(docs/확장_로드맵.md 와 맞출 것), stages.json 확장 단계 이름,
                      workflows.json 개발 순서.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from proposals import load_proposals, read_data  # noqa: E402


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8").stdout


def load_yaml(p: Path) -> dict:
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def version() -> str:
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)', (REPO / "src/__init__.py").read_text(encoding="utf-8"))
    return m.group(1) if m else "?"


# 지금 기본 모델 — provider 마다 모델 이름 키가 다르다(config.yaml ai 주석)
MODEL_KEY = {"gemini": "{}_model", "openai": "openai_{}_model", "claude": "claude_{}_model"}


def models() -> list[dict]:
    cfg = load_yaml(REPO / "config.yaml")
    ai, stt = cfg.get("ai") or {}, cfg.get("stt") or {}
    rows = []
    for kind, label in (("vision", "화면 분석"), ("chat", "채팅")):
        prov = ai.get(f"{kind}_provider", "gemini")
        rows.append({"use": label, "provider": prov, "model": ai.get(MODEL_KEY.get(prov, "{}_model").format(kind), "")})
    rows.append({"use": "게임 프로필", "provider": "gemini", "model": ai.get("profile_model", "")})
    rows.append({"use": "음성 인식", "provider": stt.get("provider", ""), "model": stt.get("model", "")
                 + (f" (없으면 {stt['fallback_model']})" if stt.get("fallback_model") else "")})
    return rows


def bots() -> list[dict]:
    out = []
    for b in load_yaml(REPO / "personas/default_bots.yaml").get("bots") or []:
        out.append({"name": b.get("name", ""), "relationship": b.get("relationship", ""), "style": b.get("style", ""),
                    "frequency": b.get("frequency", "medium"), "speech": b.get("user_speech_weight", "기본"),
                    "accurate": bool(b.get("accurate")), "disabled": bool(b.get("disabled"))})
    return out


def profiles() -> list[dict]:
    out = []
    for p in sorted((REPO / "game_profiles").glob("*.yaml")):
        d = load_yaml(p)
        out.append({"file": p.name, "name": d.get("name", p.stem), "characters": len(d.get("characters") or []),
                    "systems": len(d.get("systems") or []), "focus": len(d.get("vision_focus") or []),
                    "terms": len(d.get("stt_terms") or [])})
    return out


# KNOWN_ISSUES.md '상태 한눈에 보기' 표: | # | 이슈 | 상태 |
ISSUE_STATE = {"✅": "해결", "⚠": "부분 대응", "🔬": "분석 완료", "🔴": "미해결", "👍": "허용·긍정"}


def issues() -> list[dict]:
    out, on = [], False
    for line in (REPO / "docs/KNOWN_ISSUES.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("| # |"):
            on = True
            continue
        if on and not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not on or len(cells) < 3 or set(cells[0]) <= {"-"}:
            continue
        mark = next((k for k in ISSUE_STATE if cells[2].startswith(k)), "")
        out.append({"no": cells[0], "issue": cells[1].replace("`", ""), "state": ISSUE_STATE.get(mark, "?"),
                    "note": cells[2][len(mark):].strip().replace("`", "")})
    return out


def code() -> list[dict]:
    out = []
    for p in sorted((REPO / "src").rglob("*.py")):
        rel = p.relative_to(REPO / "src").as_posix()
        area = rel.split("/")[0] if "/" in rel else "main"
        out.append({"file": rel, "area": area, "lines": len(p.read_text(encoding="utf-8").splitlines())})
    return out


def tests() -> list[dict]:
    out = []
    for p in sorted((REPO / "tests").glob("*/*.py")):
        if p.name == "__init__.py" or p.parent.name == "__pycache__":
            continue
        if p.name == "run.py" or p.parent.name == "ui" and p.name.startswith(("smoke", "test")):
            out.append({"file": p.relative_to(REPO).as_posix(), "kind": p.parent.name})
    return out


def commits() -> list[dict]:
    out = []
    for line in git("log", "--no-merges", "--format=%h%x09%ad%x09%s", "--date=short").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        h, date, subject = parts
        m = re.match(r"(\w+)(?:\(([^)]*)\))?:", subject)
        out.append({"hash": h, "date": date, "subject": subject, "type": m.group(1) if m else "기타",
                    "scope": (m.group(2) or "") if m else ""})
    return out


def build() -> str:
    data = {
        "built": datetime.now(timezone.utc).isoformat(), "head": git("rev-parse", "--short", "HEAD").strip(),
        "version": version(),
        "features": read_data("features"), "stages": read_data("stages"), "workflows": read_data("workflows"),
        "models": models(), "bots": bots(), "profiles": profiles(), "issues": issues(),
        "code": code(), "tests": tests(), "commits": commits(), "proposals": load_proposals(),
    }
    page = (HERE / "page.html").read_text(encoding="utf-8")
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return page.replace("/*SOLO_DATA*/null", blob, 1)


if __name__ == "__main__":
    html = build()
    if "--stdout" in sys.argv:   # server.py 가 요청마다 부른다
        sys.stdout.buffer.write(html.encode("utf-8"))
    else:
        out = HERE / "out" / "dashboard.html"
        out.parent.mkdir(exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print("현황판:", out)
