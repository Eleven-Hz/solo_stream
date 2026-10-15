"""샘플 영상·음성 리플레이 — 지금 코드·설정 그대로 화면 분석 → 받아쓰기 → 채팅을 라운드 단위로 돌린다.

실행 (프로젝트 루트):
  venv\\Scripts\\python.exe tests\\replay\\run.py [--start 0 --end 300 --interval 15]
      [--vision gemini:gemini-2.5-flash] [--chat openai:gpt-6-luna] [--stt openai:gpt-transcribe]
      [--game crosscode] [--seed 1] [--tag 이름]

산출물: tests/replay/output/<tag>.json (라운드 원자료 — compare_chat.py 입력) + <tag>.txt (시간순 대화록)
비용: 실제 API 호출. 기본 300초·15초 주기 = 20라운드(화면 분석 20 + 채팅 20 + 받아쓰기 수십 회), 대략 몇 센트.
"""
from __future__ import annotations

import argparse
import json
import time

from common import (DEFAULT_AUDIO, DEFAULT_VIDEO, OUTPUT_DIR, ChatRound, FrameReader, api_keys, chat_metrics,
                    config_with, sample_batch, speech_for_round, transcribe_segments, write_transcript)


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
    lookback = args.lookback or float((config.get("stt") or {}).get("lookback_seconds", 20.0))
    profile = load_profile(args.game, config=config)
    profile_ctx, vision_ctx = build_profile_context(profile), build_vision_context(profile)
    pool = load_personas(config["chat"]["persona_dir"])
    tag = args.tag or time.strftime("replay_%Y%m%d_%H%M")
    print(f"vision={vspec} chat={cspec} 프로필={profile.get('name')} 봇 {len(pool)}명", flush=True)

    segments = transcribe_segments(Path(args.audio), args.start, args.end, lookback, config, args.stt)
    frames = FrameReader(Path(args.video))
    chat = ChatRound(config, pool)
    used: dict[int, int] = {}
    rounds, last_loc = [], ""
    t, i = args.start + args.interval, 0
    while t <= args.end + 1e-6:
        i += 1
        vision = vision_client.analyze_screenshot(frames.jpeg_at(t), vision_ctx, last_loc)
        for line in vision.splitlines():
            if line.startswith("위치:") and line[3:].strip() not in ("", "알 수 없음"):
                last_loc = line[3:].strip()
        speech, idx = speech_for_round(segments, t, lookback, used, app.MAX_SPEECH_ROUNDS)
        r = chat.run(chat_client, vision=vision, speech=speech, batch_names=sample_batch(pool, i, args.seed),
                     profile_ctx=profile_ctx)
        if not r["error"]:
            for k in idx:
                used[k] = used.get(k, 0) + 1
        rounds.append({"t": t, "vision": vision, "speech": speech, **r})
        print(f"[{int(t)//60:02d}:{int(t)%60:02d}] 채팅 {len(r['chats'])}개 {r['sec']}s"
              f"{' 오류: ' + r['error'][:80] if r['error'] else ''}", flush=True)
        t += args.interval

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = {"vision": vspec, "chat": cspec, "game": args.game, "start": args.start, "end": args.end,
            "interval": args.interval, "lookback": lookback, "seed": args.seed,
            "created": time.strftime("%Y-%m-%d %H:%M")}
    out = OUTPUT_DIR / f"{tag}.json"
    out.write_text(json.dumps({"meta": meta, "segments": segments, "rounds": rounds}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    write_transcript(OUTPUT_DIR / f"{tag}.txt", f"리플레이 {tag} — vision={vspec} chat={cspec}", segments,
                     {cspec: rounds})
    print("요약:", chat_metrics(rounds, None, args.interval))
    print(f"저장: {out} (+ .txt)")


if __name__ == "__main__":
    main()
