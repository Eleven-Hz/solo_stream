"""현황판 시험: venv/Scripts/python scripts/dashboard/test.py

1) 검토 요청: 복사본 폴더(SOLO_DASH_DIR)로 서버를 띄워 승인·충돌·반려·Origin 막기 — 진짜 data/ 는 건드리지 않는다
2) 화면: Edge(없으면 Chrome) headless 로 탭마다(#탭 주소) DOM 을 받아 NaN·빈 머리말이 없는지
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DASH = Path(__file__).resolve().parent
PORT = 8798
BASE = f"http://127.0.0.1:{PORT}"
TABS = ["overview", "checks", "review", "features", "howto", "issues", "content", "dev"]
fails: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("PASS " if cond else "FAIL ") + what)
    if not cond:
        fails.append(what)


def sha(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def post(path: str, body: dict, origin: str | None = None) -> tuple[int, dict | str]:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def browser() -> str | None:
    for p in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"]:
        if Path(p).exists():
            return p
    return None


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="solo-dash-test-"))
    shutil.copytree(DASH / "data", tmp / "data")
    real = sha(DASH / "data/features.json")

    def put(pid: str, p: dict) -> None:
        (tmp / "proposals" / pid).mkdir(parents=True)
        (tmp / "proposals" / pid / "proposal.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")

    # from 은 지금 데이터에서 읽는다 — 실제 승인으로 상태가 바뀌어도 시험이 깨지지 않게
    features = json.loads((tmp / "data/features.json").read_text(encoding="utf-8"))
    hype_status = next(x for x in features if x["id"] == "eventhype")["status"]
    put("2026-10-27-a", {"title": "이벤트 흥분도", "created": "2026-10-27T09:00:00+09:00", "status": "pending",
                         "changes": [{"dataset": "features", "op": "update", "key": "eventhype", "from": {"status": hype_status}, "set": {"status": "완료"}}],
                         "tests": [{"name": "리플레이", "result": "통과"}]})
    put("2026-10-27-b", {"title": "충돌", "created": "2026-10-27T08:00:00+09:00", "status": "pending",
                         "changes": [{"dataset": "features", "op": "update", "key": "capture", "from": {"status": "일부"}, "set": {"status": "계획"}}]})
    put("2026-10-27-c", {"title": "단계 추가", "created": "2026-10-27T07:00:00+09:00", "status": "pending",
                         "changes": [{"dataset": "stages", "op": "add", "key": 9, "row": {"order": 9, "title": "시험", "note": "-"}}]})

    env = {**os.environ, "SOLO_DASH_DIR": str(tmp), "SOLO_DASH_PORT": str(PORT), "PYTHONIOENCODING": "utf-8"}
    srv = subprocess.Popen([sys.executable, str(DASH / "server.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/").read()
                break
            except OSError:
                time.sleep(0.1)
        html = urllib.request.urlopen(BASE + "/").read().decode("utf-8")
        ok("/*SOLO_DATA*/null" not in html and "2026-10-27-a" in html, "페이지에 데이터와 요청이 실림")

        code, r = post("/api/proposals/2026-10-27-a/approve", {"comment": "x"}, origin="https://evil.example")
        ok(code == 403, "다른 Origin 승인 막음")
        code, r = post("/api/proposals/2026-10-27-b/approve", {})
        ok(code == 409 and "충돌" in r.get("error", ""), "from 이 다르면 충돌로 막음")
        code, r = post("/api/proposals/2026-10-27-a/approve", {"comment": "좋음"}, origin=BASE)
        rows = json.loads((tmp / "data/features.json").read_text(encoding="utf-8"))
        ok(code == 200 and next(x for x in rows if x["id"] == "eventhype")["status"] == "완료", "승인하면 features 반영")
        code, r = post("/api/proposals/2026-10-27-a/approve", {})
        ok(code == 409 and "이미 처리" in r.get("error", ""), "두 번 승인 막음")
        code, r = post("/api/proposals/2026-10-27-c/reject", {"comment": "필요 없음"})
        p = json.loads((tmp / "proposals/2026-10-27-c/proposal.json").read_text(encoding="utf-8"))
        stages = json.loads((tmp / "data/stages.json").read_text(encoding="utf-8"))
        ok(code == 200 and p["status"] == "rejected" and p["decision"]["comment"] == "필요 없음" and all(s["order"] != 9 for s in stages),
           "반려하면 사유만 남고 데이터 그대로")
        code, _ = post("/api/proposals/..%2Fdata/approve", {})
        ok(code == 409, "잘못된 id 막음")

        # 내 확인: 사용자가 상태·메모를 바로 바꾼다
        real_checks = sha(DASH / "data/checks.json")
        cid = json.loads((tmp / "data/checks.json").read_text(encoding="utf-8"))[0]["id"]
        code, _ = post(f"/api/checks/{cid}", {"status": "통과"}, origin="https://evil.example")
        ok(code == 403, "확인: 다른 Origin 막음")
        code, r = post(f"/api/checks/{cid}", {"status": "실패", "note": "보스 등장 라운드에 이벤트 줄 없음"}, origin=BASE)
        row = next(x for x in json.loads((tmp / "data/checks.json").read_text(encoding="utf-8")) if x["id"] == cid)
        ok(code == 200 and row["status"] == "실패" and row["note"].startswith("보스") and row["updated"], "확인: 상태·메모·시각 저장")
        code, r = post(f"/api/checks/{cid}", {"status": "완료"})
        ok(code == 409 and "모르는 상태" in r.get("error", ""), "확인: 모르는 상태 막음")
        code, r = post("/api/checks/" + urllib.parse.quote("없는-항목"), {"status": "통과"})
        ok(code == 409 and "없는 확인 항목" in r.get("error", ""), "확인: 없는 항목 막음")
        html = urllib.request.urlopen(BASE + "/").read().decode("utf-8")
        ok("보스 등장 라운드에 이벤트 줄 없음" in html, "확인: 새로 고치면 바뀐 상태가 보임")
        ok(sha(DASH / "data/features.json") == real and sha(DASH / "data/checks.json") == real_checks, "진짜 data/ 는 그대로")

        exe = browser()
        if not exe:
            print("SKIP 화면 시험 — Edge·Chrome 없음")
        else:
            for tab in TABS:
                for width in (1280, 500):   # Edge headless 는 창을 500px 쯤 아래로 못 줄인다
                    dom = subprocess.run([exe, "--headless=new", "--disable-gpu", f"--window-size={width},900", "--virtual-time-budget=3000",
                                          f"--user-data-dir={tmp / 'edge'}", "--dump-dom", f"{BASE}/#{tab}"],
                                         capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
                    dom = dom.split("<script>", 1)[0]   # 스크립트 소스의 -Infinity 는 빼고 그려진 화면만
                    lede = dom.split('id="lede"', 1)[-1].split("</p>", 1)[0]
                    ok("NaN" not in dom and "Infinity" not in dom and "기능" in lede and f'id="{tab}" role="tabpanel">' in dom,
                       f"화면 {tab} {width}px — NaN 없음·머리말·탭 보임")
    finally:
        srv.terminate()
        srv.wait()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'실패 ' + str(len(fails)) + '개' if fails else '모두 통과'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
