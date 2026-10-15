"""샘플 영상·음성 리플레이 — 지금 코드·설정 그대로 화면 분석 → 받아쓰기 → 채팅을 라운드 단위로 돌린다.

실행 (프로젝트 루트):
  venv\\Scripts\\python.exe tests\\replay\\run.py [--start 0 --end 300 --interval 15]
      [--vision gemini:gemini-2.5-flash] [--chat openai:gpt-6-luna] [--stt openai:gpt-transcribe]
      [--game crosscode] [--seed 1] [--tag 이름] [--no-instant] [--no-stt-hint]

앱과 같은 시간 흐름을 흉내 낸다:
  - 화면 분석 라운드: interval 마다(앞 라운드가 안 끝났으면 끝난 뒤). 분석 중 도착한 발화까지 포함.
  - 발화 즉시 반응 라운드(stt.instant_reply): 받아쓰기 도착(끝+STT 지연) 후 debounce 가 지나고,
    직전 라운드와 min_gap 이상 떨어져 있고, 다음 화면 분석이 3초 넘게 남았으면 직전 화면으로 채팅만.
산출물: tests/replay/output/<tag>.json (라운드 원자료 — compare_chat.py 입력) + <tag>.txt (시간순 대화록)
비용: 실제 API 호출. 기본 300초·15초 주기 = 20라운드(+발화 라운드), 대략 몇 센트.
"""
from __future__ import annotations

import argparse
import json
import random
import time

from common import (DEFAULT_AUDIO, DEFAULT_VIDEO, OUTPUT_DIR, STT_LATENCY, ChatRound, FrameReader, api_keys,
                    chat_metrics, config_with, speech_for_round, transcribe_segments, write_transcript)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=300.0)
    ap.add_argument("--interval", type=float, default=15.0, help="라운드 간격(초) — 앱의 분석 주기")
    ap.add_argument("--vision", help="provider:model (기본: 설정값)")
    ap.add_argument("--chat", help="provider:model (기본: 설정값)")
    ap.add_argument("--stt", help="provider:model (기본: 설정값)")
    ap.add_argument("--game", default="crosscode", help="게임 프로필 이름")
    ap.add_argument("--video", default=str(DEFAULT_VIDEO))
    ap.add_argument("--audio", default=str(DEFAULT_AUDIO))
    ap.add_argument("--lookback", type=float, default=None, help="발화 반영 시간(초, 기본: 설정값)")
    ap.add_argument("--seed", type=int, default=1, help="라운드별 발언 봇 구성 seed")
    ap.add_argument("--no-instant", action="store_true", help="발화 즉시 반응 라운드 끄기")
    ap.add_argument("--no-stt-hint", action="store_true", help="받아쓰기에 게임 고유명사 힌트 안 붙이기")
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()

    from pathlib import Path
    from src import main as app
    from src.ai.model_client import create_chat_client, create_vision_client, selected_model
    from src.chat.game_profile_loader import build_profile_context, build_vision_context, load_profile
    from src.chat.persona_loader import load_personas
    from src.utils.config_loader import load_config

    config = load_config()
    vcfg, ccfg = config_with(config, "vision", args.vision), config_with(config, "chat", args.chat)
    keys = api_keys(config)
    vision_client = create_vision_client(vcfg, **keys)
    chat_client = create_chat_client(ccfg, **keys)
    vspec, cspec = "%s:%s" % selected_model(vcfg, "vision"), "%s:%s" % selected_model(ccfg, "chat")
    stt_cfg = config.get("stt") or {}
    lookback = args.lookback or float(stt_cfg.get("lookback_seconds", 20.0))
    instant = not args.no_instant and bool(stt_cfg.get("instant_reply", True))
    debounce = float(stt_cfg.get("instant_debounce_seconds", 1.5))
    min_gap = float(stt_cfg.get("instant_min_gap_seconds", 5.0))
    max_wait = float(stt_cfg.get("instant_max_wait_seconds", 4.0))
    profile = load_profile(args.game, config=config)
    profile_ctx, vision_ctx = build_profile_context(profile), build_vision_context(profile)
    pool = load_personas(config["chat"]["persona_dir"])
    tag = args.tag or time.strftime("replay_%Y%m%d_%H%M")
    print(f"vision={vspec} chat={cspec} 프로필={profile.get('name')} 봇 {len(pool)}명 "
          f"즉시반응={'on' if instant else 'off'} STT힌트={'off' if args.no_stt_hint else 'on'}", flush=True)

    segments = transcribe_segments(Path(args.audio), args.start, args.end, lookback, config, args.stt,
                                   profile=None if args.no_stt_hint else profile)
    frames = FrameReader(Path(args.video))
    chat = ChatRound(config, pool)
    used: dict[int, int] = {}
    rounds: list[dict] = []
    state = {"loc": "", "vision": "", "vision_t": -1e9, "start": -1e9, "end": args.start}

    def chat_round(t: float, vision: str, vision_sec: float, speech_round: bool):
        speech, idx = speech_for_round(segments, t + vision_sec, lookback, used, app.MAX_SPEECH_ROUNDS)
        state["start"] = t
        r = chat.run(chat_client, vision=vision, speech=speech, profile_ctx=profile_ctx,
                     rng=random.Random(args.seed * 1000 + len(rounds)), speech_round=speech_round)
        if not r["error"]:
            for k in idx:
                used[k] = used.get(k, 0) + 1
        done = t + vision_sec + r["sec"]
        # 발화 → 채팅 생성 완료까지 걸린 시간 (이번 라운드에 처음 반영된 발화)
        lat = [round(done - segments[k]["end"], 1) for k, u in zip(idx, speech) if not u["reacted"]]
        rounds.append({"t": round(t, 2), "vision": vision, "vision_sec": round(vision_sec, 2),
                       "speech": speech, "speech_latency": lat, **r})
        state["end"] = done
        mm, ss = divmod(int(t), 60)
        print(f"[{mm:02d}:{ss:02d}]{' ⚡' if speech_round else '  '} 채팅 {len(r['chats'])}개 {r['sec']}s"
              f"{' 오류: ' + r['error'][:80] if r['error'] else ''}", flush=True)

    def speech_round_due(tau: float) -> bool:
        fresh = [s for i, s in enumerate(segments)
                 if used.get(i, 0) == 0 and len(s["text"].strip()) >= 2
                 and tau - lookback <= s["end"] and s["end"] + STT_LATENCY <= tau]
        if not fresh:
            return False
        recv = [s["end"] + STT_LATENCY for s in fresh]
        if tau - max(recv) < debounce and tau - min(recv) < max_wait:
            return False
        return tau - state["start"] >= min_gap and tau - state["vision_t"] < args.interval - 3.0

    vt = args.start + args.interval
    while vt <= args.end + 1e-6:
        # 다음 화면 분석 전까지 발화 즉시 반응 라운드
        tau = state["end"]
        while instant and state["vision"] and tau < vt:
            if speech_round_due(tau):
                chat_round(tau, state["vision"], 0.0, True)
                tau = state["end"]
            else:
                tau += 0.25
        t = max(vt, state["end"])  # 앞 라운드가 안 끝났으면 끝난 뒤 시작
        t0 = time.perf_counter()
        vision = vision_client.analyze_screenshot(frames.jpeg_at(vt), vision_ctx, state["loc"])
        vision_sec = time.perf_counter() - t0
        for line in vision.splitlines():
            if line.startswith("위치:") and line[3:].strip() not in ("", "알 수 없음"):
                state["loc"] = line[3:].strip()
        state["vision"], state["vision_t"] = vision, t
        chat_round(t, vision, vision_sec, False)
        vt += args.interval

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = {"vision": vspec, "chat": cspec, "game": args.game, "start": args.start, "end": args.end,
            "interval": args.interval, "lookback": lookback, "seed": args.seed, "instant": instant,
            "stt_hint": not args.no_stt_hint, "created": time.strftime("%Y-%m-%d %H:%M")}
    out = OUTPUT_DIR / f"{tag}.json"
    out.write_text(json.dumps({"meta": meta, "segments": segments, "rounds": rounds}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    write_transcript(OUTPUT_DIR / f"{tag}.txt", f"리플레이 {tag} — vision={vspec} chat={cspec}", segments,
                     {cspec: rounds})
    lats = [x for r in rounds for x in r["speech_latency"]]
    summary = chat_metrics(rounds, None, args.interval, [b["name"] for b in pool])
    if lats:
        summary["speech_latency_avg"] = round(sum(lats) / len(lats), 1)
    print("요약:", summary)
    print(f"저장: {out} (+ .txt)")


if __name__ == "__main__":
    main()
