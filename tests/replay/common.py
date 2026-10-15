"""리플레이 평가 공용: 샘플 영상·음성을 앱과 같은 코드 경로로 라운드 단위로 흘린다.

영상 프레임 → vision, 음성 VAD 세그먼트 → STT, 라운드마다 채팅 생성 → main 과 같은 후처리
(발언자 외 드롭 · 복붙 드롭 · 소재 쏠림 제한). 사용량은 src.utils.usage 로 실제 토큰을 센다.
"""
from __future__ import annotations

import copy
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SOLO_STREAM_DEV", "1")

DEFAULT_VIDEO = ROOT / "tests" / "vision" / "video" / "crosscode_video_01.mkv"
DEFAULT_AUDIO = ROOT / "tests" / "stt" / "audio" / "crosscode_audio_01.wav"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def parse_spec(spec: str) -> tuple[str, str]:
    """'openai:gpt-6-luna' → ('openai', 'gpt-6-luna')."""
    provider, _, model = spec.partition(":")
    if not model:
        raise SystemExit(f"provider:model 형식이어야 합니다: {spec}")
    return provider.strip(), model.strip()


def config_with(config: dict, kind: str, spec: str | None) -> dict:
    """config 사본에 kind(vision|chat) 모델을 spec 으로 지정."""
    from src.ai.model_client import model_config_key
    c = copy.deepcopy(config)
    if spec:
        provider, model = parse_spec(spec)
        c["ai"][f"{kind}_provider"] = provider
        c["ai"][model_config_key(provider, kind)] = model
    return c


def api_keys(config: dict) -> dict:
    from src.utils.config_loader import get_api_key, get_claude_api_key, get_openai_api_key
    return {"gemini_api_key": get_api_key(config), "openai_api_key": get_openai_api_key(config),
            "claude_api_key": get_claude_api_key(config)}


class FrameReader:
    def __init__(self, video: Path, width: int = 1024):
        import cv2
        self._cv2 = cv2
        self._cap = cv2.VideoCapture(str(video))
        if not self._cap.isOpened():
            raise SystemExit(f"영상을 열 수 없습니다: {video}")
        self._width = width

    def jpeg_at(self, t: float) -> bytes:
        cv2 = self._cv2
        self._cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, img = self._cap.read()
        if not ok:
            raise SystemExit(f"{t:.0f}초 프레임을 읽지 못했습니다")
        h, w = img.shape[:2]
        img = cv2.resize(img, (self._width, int(h * self._width / w)))
        return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def transcribe_segments(audio: Path, start: float, end: float, lookback: float,
                        config: dict, stt_spec: str | None) -> list[dict]:
    """[start-lookback, end] 구간을 VAD 로 자르고 받아쓴다. [{start, end, text}]"""
    from src.stt.transcriber import create_transcriber
    from tests.integration.run import _load_audio
    from tests.stt.vad_util import SileroVAD

    stt = config.get("stt", {}) or {}
    provider, model = parse_spec(stt_spec) if stt_spec else (stt.get("provider", "openai"), stt.get("model"))
    keys = api_keys(config)
    transcriber = create_transcriber(provider, gemini_api_key=keys["gemini_api_key"],
                                     openai_api_key=keys["openai_api_key"], model=model,
                                     prompt=stt.get("transcribe_prompt"))
    sr = 16000
    pcm = _load_audio(audio, sr)
    lo = max(0.0, start - lookback)
    vad = SileroVAD(threshold=0.5, min_speech_ms=250, min_silence_ms=500, speech_pad_ms=200)
    out = []
    for seg in vad.segments(pcm[int(lo * sr):int(end * sr)]):
        text, _ = transcriber.transcribe(seg.pcm, sr)
        out.append({"start": seg.start_s + lo, "end": seg.end_s + lo, "text": text})
    print(f"STT {provider}/{model}: 세그먼트 {len(out)}개", flush=True)
    return out


class ChatRound:
    """main.ai_process 의 채팅 라운드 후처리를 그대로 재현하는 상태 묶음."""

    def __init__(self, config: dict, pool: list[dict]):
        from src.chat.topic_guard import TopicTracker
        chat_cfg = config.get("chat", {}) or {}
        self.pool = pool
        self.history: list[dict] = []
        self.tracker = TopicTracker(window_rounds=int(chat_cfg.get("topic_window_rounds", 6)),
                                    cliche_keywords=chat_cfg.get("cliche_keywords"))
        self.round_max = int(chat_cfg.get("topic_round_max", 2))

    def run(self, client, *, vision: str, speech: list[dict], batch_names: list[str],
            profile_ctx: str, weight: str = "high") -> dict:
        from src import main as app
        from src.chat.topic_guard import limit_round_topics
        mentioned = app._detect_mentioned_bots(speech, self.pool)
        mnames = [b["name"] for b in mentioned]
        batch = mentioned[:5] + [b for b in self.pool if b["name"] in batch_names and b not in mentioned]
        batch = batch[:5]
        hot = self.tracker.hot_keys()
        t0 = time.perf_counter()
        raw = client.generate_chats(game_context=vision, bots=batch, chat_history=self.history,
                                    game_profile_context=profile_ctx, user_speech=speech or None,
                                    default_speech_weight=weight, mentioned_names=mnames,
                                    roster=self.pool, avoid_topics=hot)
        sec = time.perf_counter() - t0
        names = {b["name"] for b in batch}
        chats = [c for c in raw if c.get("name") in names]
        chats = app._drop_repeat_chats(chats, self.history)
        chats, dropped = limit_round_topics(chats, hot_keys=hot, cliche_keywords=self.tracker.cliche_keywords,
                                            per_round_max=self.round_max, exempt_names=set(mnames))
        self.tracker.push_round([c["message"] for c in chats])
        self.history = (self.history + chats)[-10:]
        dbg = client.last_chat_call
        return {"chats": chats, "raw": raw, "dropped": [[c, k] for c, k in dropped], "mentioned": mnames,
                "hot": hot, "batch": [b["name"] for b in batch], "sec": round(sec, 2),
                "in_tokens": dbg.in_tokens, "out_tokens": dbg.out_tokens, "error": dbg.error}


def sample_batch(pool: list[dict], round_idx: int, seed: int, size: int = 5) -> list[str]:
    """라운드별 발언자 — 같은 seed 면 모델이 달라도 같은 봇 구성."""
    rng = random.Random(seed * 1000 + round_idx)
    return [b["name"] for b in rng.sample(pool, min(size, len(pool)))]


def speech_for_round(segments: list[dict], t: float, lookback: float, used: dict[int, int],
                     max_rounds: int) -> tuple[list[dict], list[int]]:
    """main 과 같은 규칙: lookback 안에 끝난 발화, 한 발화는 max_rounds 라운드까지."""
    picked, idx = [], []
    for i, s in enumerate(segments):
        if t - lookback <= s["end"] <= t and len(s["text"].strip()) >= 2 and used.get(i, 0) < max_rounds:
            picked.append({"text": s["text"], "seconds_ago": round(t - s["end"], 1)})
            idx.append(i)
    return picked, idx


def chat_metrics(rounds: list[dict], price: tuple[float, float] | None, interval: float) -> dict:
    """라운드 결과 요약: 메시지 수, HP/SP 언급 비율, 평균 길이, 평균 토큰·지연, 시간당 비용."""
    msgs = [c["message"] for r in rounds for c in r["chats"]]
    n = max(1, len(msgs))
    hp = sum(1 for m in msgs if any(k in m for k in ("HP", "SP", "피 ", "체력")))
    tin = sum(r["in_tokens"] for r in rounds) / max(1, len(rounds))
    tout = sum(r["out_tokens"] for r in rounds) / max(1, len(rounds))
    out = {"messages": len(msgs), "hp_sp_pct": round(100 * hp / n, 1),
           "avg_len": round(sum(len(m) for m in msgs) / n, 1),
           "avg_in_tokens": round(tin), "avg_out_tokens": round(tout),
           "avg_sec": round(sum(r["sec"] for r in rounds) / max(1, len(rounds)), 2),
           "errors": sum(1 for r in rounds if r.get("error"))}
    if price:
        per_call = (tin * price[0] + tout * price[1]) / 1_000_000
        out["usd_per_hour"] = round(per_call * 3600 / interval, 3)
    return out


def write_transcript(path: Path, title: str, segments: list[dict], columns: dict[str, list[dict]]):
    """라운드별 발화 + 각 모델 채팅을 시간순으로 나란히 적은 TXT."""
    lines = [title, ""]
    times = [r["t"] for r in next(iter(columns.values()))]
    for i, t in enumerate(times):
        mm, ss = divmod(int(t), 60)
        heard = [s["text"] for s in segments if t - 15 < s["end"] <= t]
        lines.append(f"── {mm:02d}:{ss:02d}" + (f"  🎤 {' / '.join(heard)}" if heard else ""))
        for name, rounds in columns.items():
            chats = " | ".join(f"{c['name']}: {c['message']}" for c in rounds[i]["chats"]) or "(없음)"
            lines.append(f"   [{name}] {chats}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
