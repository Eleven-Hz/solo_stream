"""
STT 제공자 정확도 비교 — Gemini vs OpenAI.

동일 오디오 파일을 VAD로 segment 분할한 뒤, 각 segment를 Gemini / OpenAI 모델로
각각 받아쓰기 해서 텍스트를 나란히 비교한다. (화자분리는 기존 VAD 방식 그대로,
"파일 → STT" 부분만 비교한다.)

결과:
  - 콘솔에 segment별 텍스트/지연 출력
  - tests/stt/result_provider_compare.html (오디오 재생 + 텍스트 나란히 비교)

실행:
  python tests/stt/compare_providers.py
  python tests/stt/compare_providers.py --file tests/stt/audio/crosscode_audio_01.wav --max-segments 8
"""
from __future__ import annotations

import argparse
import base64
import html
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from src.utils.config_loader import load_config, get_api_key
import os

from tests.stt.transcriber import GeminiTranscriber, _pcm_to_wav_bytes
from tests.stt.openai_transcriber import OpenAITranscriber
from tests.stt.vad_util import SileroVAD, SpeechSegment

SR = 16000
_PROMPT = "사용자가 게임을 플레이하며 한국어로 혼잣말/감상을 말합니다. 짧은 감탄사도 그대로 옮겨 적으세요."


def _load_audio(path: Path, target_sr: int) -> np.ndarray:
    import soundfile as sf

    pcm, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1).astype(np.float32)
    if sr != target_sr:
        ratio = target_sr / sr
        out_len = int(round(len(pcm) * ratio))
        xp = np.linspace(0, len(pcm) - 1, num=len(pcm), dtype=np.float64)
        x = np.linspace(0, len(pcm) - 1, num=out_len, dtype=np.float64)
        pcm = np.interp(x, xp, pcm).astype(np.float32)
    return pcm.astype(np.float32)


def _audio_tag(wav_bytes: bytes) -> str:
    b64 = base64.b64encode(wav_bytes).decode("ascii")
    return f'<audio controls preload="none" src="data:audio/wav;base64,{b64}"></audio>'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="tests/stt/audio/sample01.wav")
    parser.add_argument("--max-segments", type=int, default=0, help="0이면 전체")
    parser.add_argument("--gemini-model", default="gemini-2.5-flash")
    parser.add_argument("--openai-model", default="gpt-4o-transcribe")
    parser.add_argument("--also-mini", action="store_true",
                        help="gpt-4o-mini-transcribe 열도 추가")
    args = parser.parse_args()

    path = ROOT / args.file
    if not path.exists():
        print(f"[오류] 입력 파일이 없습니다: {path}")
        return

    print(f"오디오 로드: {path}")
    pcm = _load_audio(path, SR)
    print(f"  {len(pcm)/SR:.1f}s · {SR}Hz · {len(pcm):,} samples")

    # VAD (run.py / config.yaml 기본값과 동일)
    vad = SileroVAD(threshold=0.5, sample_rate=SR,
                    min_speech_ms=250, min_silence_ms=500, speech_pad_ms=100)
    segments: list[SpeechSegment] = vad.segments(pcm)
    if args.max_segments > 0:
        segments = segments[: args.max_segments]
    print(f"발화 segment {len(segments)}개 비교\n")
    if not segments:
        print("발화가 감지되지 않았습니다.")
        return

    app_config = load_config()
    gemini = GeminiTranscriber(
        api_key=get_api_key(app_config),
        model=args.gemini_model, language="ko", prompt=_PROMPT,
    )

    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        print("[오류] .env.local 에 OPENAI_API_KEY 가 없습니다.")
        return

    # 비교 대상 컬럼 정의: (라벨, transcriber)
    columns: list[tuple[str, object]] = [
        (f"Gemini · {args.gemini_model}", gemini),
        (f"OpenAI · {args.openai_model}",
         OpenAITranscriber(api_key=openai_key, model=args.openai_model, language="ko")),
    ]
    if args.also_mini:
        columns.append((
            "OpenAI · gpt-4o-mini-transcribe",
            OpenAITranscriber(api_key=openai_key, model="gpt-4o-mini-transcribe", language="ko"),
        ))

    rows = []
    totals = {label: 0.0 for label, _ in columns}
    for i, seg in enumerate(segments, start=1):
        print(f"[seg {i}/{len(segments)}] {seg.start_s:.2f}s → {seg.end_s:.2f}s ({seg.duration_s:.2f}s)")
        cells = []
        for label, tr in columns:
            text, elapsed = tr.transcribe(seg.pcm, SR)
            totals[label] += elapsed
            print(f"  {label:42s} ({elapsed:5.2f}s): '{text}'")
            cells.append({"label": label, "text": text or "(빈 응답)", "elapsed": elapsed})
        wav = _pcm_to_wav_bytes(seg.pcm, SR)
        rows.append({"num": i, "start": seg.start_s, "end": seg.end_s,
                     "dur": seg.duration_s, "audio": _audio_tag(wav), "cells": cells})
        print()

    print("=" * 60)
    print("평균 응답시간")
    for label, _ in columns:
        print(f"  {label:42s}: {totals[label]/len(segments):.2f}s")
    print("=" * 60)

    _write_html(rows, [label for label, _ in columns], path.name, totals, len(segments))


def _write_html(rows, labels, filename, totals, n_seg) -> None:
    ncol = len(labels) + 1  # 오디오 + provider들
    head_cells = "".join(f"<th>{html.escape(l)}</th>" for l in labels)
    body = []
    for r in rows:
        cells = "".join(
            f'<td><div class="txt">{html.escape(c["text"])}</div>'
            f'<div class="meta">{c["elapsed"]:.2f}s</div></td>'
            for c in r["cells"]
        )
        body.append(
            f'<tr><td class="audio"><div class="meta">#{r["num"]} · '
            f'{r["start"]:.1f}–{r["end"]:.1f}s ({r["dur"]:.1f}s)</div>{r["audio"]}</td>{cells}</tr>'
        )
    avg = "".join(f"<th>{totals[l]/n_seg:.2f}s</th>" for l in labels)

    out = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>STT 제공자 비교 — Gemini vs OpenAI</title>
<style>
body{{font-family:'Segoe UI',sans-serif;background:#f0f2f5;color:#202124;padding:24px;}}
h1{{font-size:1.3rem;}} p{{color:#5f6368;font-size:.9rem;}}
table{{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.15);border-radius:8px;overflow:hidden;}}
th,td{{border:1px solid #e8eaed;padding:10px 12px;vertical-align:top;text-align:left;}}
th{{background:#f8f9fa;font-size:.85rem;color:#3c4043;}}
td.audio{{width:220px;background:#fafafa;}}
.txt{{line-height:1.5;font-size:.95rem;}} .meta{{font-size:.75rem;color:#5f6368;margin-bottom:6px;}}
audio{{width:200px;height:34px;}}
tr:nth-child(even) td{{background:#fcfcfc;}} td.audio{{background:#f4f6f8 !important;}}
tfoot th{{background:#e8f0fe;}}
</style></head><body>
<h1>STT 제공자 정확도 비교 — Gemini vs OpenAI</h1>
<p>입력: {html.escape(filename)} · segment {n_seg}개 · 동일 VAD 구간을 각 모델로 받아쓰기</p>
<table>
<thead><tr><th>오디오</th>{head_cells}</tr></thead>
<tbody>{''.join(body)}</tbody>
<tfoot><tr><th>평균 응답시간</th>{avg}</tr></tfoot>
</table>
</body></html>"""
    out_path = ROOT / "tests" / "stt" / "result_provider_compare.html"
    out_path.write_text(out, encoding="utf-8")
    print(f"\n리포트: {out_path}")


if __name__ == "__main__":
    main()
