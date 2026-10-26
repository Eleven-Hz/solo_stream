"""검토 요청(상신) — Claude 가 현황판 데이터(data/*.json)를 바로 고치지 않고 proposals/<id>/proposal.json 으로 올린다.

사용자가 현황판 "검토 요청" 탭에서 승인하면 그때 data/ 에 반영하고, 반려하면 사유만 남긴다. 형식: proposals/README.md
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# SOLO_DASH_DIR: 시험이 진짜 data/·proposals/ 대신 복사본 폴더를 쓰게 한다
ROOT = Path(os.environ.get("SOLO_DASH_DIR") or Path(__file__).resolve().parent)
DATA_DIR = ROOT / "data"
PROPOSAL_DIR = ROOT / "proposals"

# 손으로 관리하는 데이터와 한 줄을 가리키는 열(키)
KEYS = {"features": "id", "stages": "order", "workflows": "id"}


def read_data(name: str) -> list[dict]:
    return json.loads((DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _write_data(name: str, rows: list[dict]) -> None:
    (DATA_DIR / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _file(pid: str) -> Path:
    return PROPOSAL_DIR / pid / "proposal.json"


def _save(p: dict) -> None:
    _file(p["id"]).write_text(json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def valid_id(pid: str | None) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", pid or ""))


def _find(dataset: str, rows: list[dict], key) -> int:
    k = KEYS[dataset]
    return next((i for i, r in enumerate(rows) if str(r.get(k)) == str(key)), -1)


def check_change(c: dict, data: dict) -> dict:
    """바뀌는 내용 하나가 지금 데이터와 맞는지. 결과: {ok, current, problem}"""
    ds = c.get("dataset")
    if ds not in KEYS:
        return {"ok": False, "current": None, "problem": f"모르는 데이터: {ds}"}
    rows = data.setdefault(ds, read_data(ds))
    i = _find(ds, rows, c.get("key"))
    row = rows[i] if i >= 0 else None
    op = c.get("op")
    if op == "add":
        return {"ok": False, "current": row, "problem": "이미 있는 줄"} if row else {"ok": True, "current": None}
    if row is None:
        return {"ok": False, "current": None, "problem": "그 줄이 없음"}
    if op == "remove":
        return {"ok": True, "current": row}
    if op != "update":
        return {"ok": False, "current": None, "problem": f"모르는 작업: {op}"}
    # update: from 에 적힌 값이 지금 값과 같아야 한다(요청을 쓴 뒤 다른 승인으로 바뀌었으면 충돌)
    frm = c.get("from") or {}
    moved = [f for f in (c.get("set") or {}) if f in frm and frm[f] != row.get(f)]
    if moved:
        return {"ok": False, "current": row, "problem": "요청 뒤 바뀜: " + ", ".join(moved)}
    return {"ok": True, "current": row}


def load_proposals() -> list[dict]:
    if not PROPOSAL_DIR.exists():
        return []
    data: dict = {}
    out = []
    for d in PROPOSAL_DIR.iterdir():
        if not d.is_dir() or not _file(d.name).exists():
            continue
        try:
            p = json.loads(_file(d.name).read_text(encoding="utf-8"))
        except Exception as e:
            out.append({"id": d.name, "title": d.name, "status": "broken", "problem": f"proposal.json 을 못 읽음: {e}", "changes": []})
            continue
        p["id"] = d.name
        p["status"] = p.get("status") or "pending"
        p["changes"] = [{**c, "check": check_change(c, data) if p["status"] == "pending" else None} for c in p.get("changes") or []]
        p["screenshots"] = [s for s in p.get("screenshots") or [] if (d / s.get("file", "")).is_file()]
        out.append(p)
    return sorted(out, key=lambda p: str(p.get("created") or ""), reverse=True)


def _pending(pid: str) -> dict:
    if not valid_id(pid) or not _file(pid).exists():
        raise ValueError(f"없는 요청: {pid}")
    p = json.loads(_file(pid).read_text(encoding="utf-8"))
    p["id"] = pid
    if (p.get("status") or "pending") != "pending":
        raise ValueError(f"이미 처리한 요청({p['status']})")
    return p


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def approve(pid: str, comment: str = "") -> dict:
    """승인: 바뀌는 내용을 모두 검사한 뒤 하나라도 안 맞으면 아무것도 쓰지 않는다."""
    p = _pending(pid)
    data: dict = {}
    bad = [(c, r) for c in p.get("changes") or [] if not (r := check_change(c, data))["ok"]]
    if bad:
        raise ValueError("충돌 — " + "; ".join(f"{c.get('dataset')}/{c.get('key')}: {r['problem']}" for c, r in bad))
    for c in p.get("changes") or []:
        rows = data[c["dataset"]]
        i = _find(c["dataset"], rows, c["key"])
        if c["op"] == "add":
            rows.append(c["row"])
        elif c["op"] == "remove":
            rows.pop(i)
        else:
            rows[i] = {**rows[i], **c["set"]}
    for name, rows in data.items():
        _write_data(name, rows)
    p["status"] = "approved"
    p["decision"] = {"at": _now(), "comment": comment}
    _save(p)
    return p


def reject(pid: str, comment: str = "") -> dict:
    p = _pending(pid)
    p["status"] = "rejected"
    p["decision"] = {"at": _now(), "comment": comment}
    _save(p)
    return p
