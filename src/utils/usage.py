"""
실제 API 사용량(토큰·오디오 길이) 집계.

각 API 호출 지점(model_client / transcriber / game_profile_loader)이 응답에 담긴 실제
토큰 수로 `record(...)` 를 부르고, 프로세스(AI / STT)마다 `UsageMeter` 하나가 이를 모아
`ui_queue` 로 스냅샷을 보낸다. UI 는 출처별 스냅샷을 합쳐 '사용량' 탭에 보여준다.

금액 = API 가 보고한 토큰(또는 오디오 초) × pricing.MODEL_PRICING 공개 단가.
무료 한도·할인·검색 그라운딩 요금은 반영하지 않으므로 청구서와 정확히 같지는 않다.
SDK 를 import 하지 않는 순수 모듈 — 어느 프로세스에서든 가볍게 쓸 수 있다.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from src.utils import pricing

logger = logging.getLogger("solo_stream.usage")

# 항목(kind) 표시 이름 — UI 표와 로그에서 같은 순서로 쓴다.
KIND_LABEL = {"vision": "화면 분석", "chat": "채팅 생성", "lookup": "웹 검색", "stt": "음성 인식", "profile": "게임 프로필"}

_sink: Callable[..., None] | None = None


def set_sink(fn: Callable[..., None] | None) -> None:
    """이 프로세스의 record() 가 보낼 곳(보통 UsageMeter.add). None 이면 기록 안 함."""
    global _sink
    _sink = fn


def record(kind: str, model: str, in_tokens: int = 0, out_tokens: int = 0, audio_sec: float = 0.0) -> None:
    """API 호출 1회의 실제 사용량. 집계 실패가 본 호출을 깨뜨리지 않게 예외를 삼킨다."""
    if _sink is None:
        return
    try:
        _sink(kind, model, int(in_tokens or 0), int(out_tokens or 0), float(audio_sec or 0.0))
    except Exception:
        logger.exception("사용량 기록 실패")


def actual_cost(kind: str, model: str, in_tokens: int, out_tokens: int, audio_sec: float) -> float | None:
    """실제 토큰/오디오 길이로 금액(USD). 단가 미등록 모델이면 None.

    estimate 와 달리 token_mult 를 곱하지 않는다 — 여기 토큰은 그 모델이 직접 센 값이다.
    """
    p = pricing.get_pricing(model)
    if p is None:
        return None
    if audio_sec and "audio_per_min" in p:
        return p["audio_per_min"] * audio_sec / 60.0
    in_rate = p.get("audio_input", p.get("input", 0.0)) if kind == "stt" else p.get("input", 0.0)
    return (in_tokens * in_rate + out_tokens * p.get("output", 0.0)) / 1_000_000


class UsageMeter:
    """한 프로세스의 세션 누적 사용량. 스레드 안전(STT 는 파이프라인 스레드에서 기록)."""

    def __init__(self, on_change: Callable[[dict], None] | None = None):
        self.started = time.time()
        self._rows: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()
        self._on_change = on_change

    def add(self, kind: str, model: str, in_tokens: int = 0, out_tokens: int = 0, audio_sec: float = 0.0):
        cost = actual_cost(kind, model, in_tokens, out_tokens, audio_sec)
        with self._lock:
            r = self._rows.setdefault((kind, model), {
                "kind": kind, "model": model, "calls": 0, "in_tokens": 0, "out_tokens": 0,
                "audio_sec": 0.0, "cost": 0.0, "priced": True,
            })
            r["calls"] += 1
            r["in_tokens"] += in_tokens
            r["out_tokens"] += out_tokens
            r["audio_sec"] += audio_sec
            if cost is None:
                r["priced"] = False
            else:
                r["cost"] += cost
            snap = self._snapshot_locked()
        if self._on_change is not None:
            self._on_change(snap)

    def _snapshot_locked(self) -> dict:
        return {"started": self.started, "rows": [dict(r) for r in self._rows.values()]}

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot_locked()

    def total_cost(self) -> float:
        with self._lock:
            return sum(r["cost"] for r in self._rows.values())


def merge_snapshots(snaps: list[dict]) -> dict:
    """여러 프로세스 스냅샷을 하나로. started 는 가장 이른 값, 행은 (kind, model) 로 합산."""
    rows: dict[tuple[str, str], dict] = {}
    started = min((s["started"] for s in snaps), default=time.time())
    for s in snaps:
        for r in s.get("rows", []):
            key = (r["kind"], r["model"])
            if key not in rows:
                rows[key] = dict(r)
                continue
            m = rows[key]
            for f in ("calls", "in_tokens", "out_tokens", "audio_sec", "cost"):
                m[f] += r[f]
            m["priced"] = m["priced"] and r["priced"]
    order = list(KIND_LABEL)
    ordered = sorted(rows.values(), key=lambda r: (order.index(r["kind"]) if r["kind"] in order else 99, r["model"]))
    total = sum(r["cost"] for r in ordered)
    elapsed = max(1.0, time.time() - started)
    return {
        "started": started,
        "elapsed_s": elapsed,
        "rows": ordered,
        "total_cost": total,
        "per_hour": total * 3600.0 / elapsed,
        "unpriced": sorted({r["model"] for r in ordered if not r["priced"]}),
    }
