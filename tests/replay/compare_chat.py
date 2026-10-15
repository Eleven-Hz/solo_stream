"""채팅 모델 A/B — run.py 가 남긴 리플레이(화면 분석·발화·발언 봇)를 그대로 두고 채팅 모델만 바꿔 비교.

실행 (프로젝트 루트):
  venv\\Scripts\\python.exe tests\\replay\\compare_chat.py tests\\replay\\output\\<tag>.json \\
      openai:gpt-6-luna claude:claude-haiku-5-5 gemini:gemini-2.5-flash-lite [--tag 이름]

산출물: tests/replay/output/<tag>_compare.json (모델별 라운드·요약) + _compare.txt (시간순 나란히 비교)
읽는 법: 숫자(HP/SP 언급 %, 평균 길이, 시간당 비용)는 참고용이고, 판단은 TXT 에서
'스트리머 말(🎤)에 반응했는지'를 직접 읽어서 한다 — 2026-10 비교도 그렇게 결정했다(docs/wiki/models-and-providers.md).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import OUTPUT_DIR, ChatRound, api_keys, chat_metrics, config_with, write_transcript


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("replay", help="run.py 산출 JSON")
    ap.add_argument("models", nargs="+", help="provider:model 여러 개")
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()

    from src.ai.model_client import create_chat_client
    from src.chat.game_profile_loader import build_profile_context, load_profile
    from src.chat.persona_loader import load_personas
    from src.utils import pricing
    from src.utils.config_loader import load_config

    src = json.loads(Path(args.replay).read_text(encoding="utf-8"))
    meta, segments = src["meta"], src["segments"]
    config = load_config()
    keys = api_keys(config)
    pool = load_personas(config["chat"]["persona_dir"])
    profile_ctx = build_profile_context(load_profile(meta["game"], config=config))

    columns, summary = {}, {}
    for spec in args.models:
        client = create_chat_client(config_with(config, "chat", spec), **keys)
        chat = ChatRound(config, pool)
        rounds = []
        for r in src["rounds"]:
            res = chat.run(client, vision=r["vision"], speech=r["speech"], batch_names=r["batch"],
                           profile_ctx=profile_ctx)
            rounds.append({"t": r["t"], **res})
            print(f"{spec} [{int(r['t'])//60:02d}:{int(r['t'])%60:02d}] {len(res['chats'])}개 {res['sec']}s "
                  f"in={res['in_tokens']} out={res['out_tokens']}{' 오류' if res['error'] else ''}", flush=True)
        p = pricing.get_pricing(spec.partition(":")[2])
        summary[spec] = chat_metrics(rounds, (p["input"], p["output"]) if p and "input" in p else None,
                                     meta["interval"])
        columns[spec] = rounds

    tag = args.tag or Path(args.replay).stem + "_compare"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / f"{tag}.json").write_text(json.dumps({"meta": meta, "summary": summary, "rounds": columns},
                                                       ensure_ascii=False, indent=1), encoding="utf-8")
    write_transcript(OUTPUT_DIR / f"{tag}.txt", f"채팅 모델 비교 — {Path(args.replay).name}", segments, columns)
    print("\n모델 | 메시지 | HP/SP% | 평균길이 | 입력/출력 토큰 | 지연 | $/h")
    for spec, m in summary.items():
        print(f"{spec} | {m['messages']} | {m['hp_sp_pct']} | {m['avg_len']} | "
              f"{m['avg_in_tokens']}/{m['avg_out_tokens']} | {m['avg_sec']}s | {m.get('usd_per_hour', '?')}")
    print(f"저장: {OUTPUT_DIR / tag}.json (+ .txt)")


if __name__ == "__main__":
    main()
