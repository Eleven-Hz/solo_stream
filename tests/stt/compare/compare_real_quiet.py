"""
result.html(작게 들리는 실제 mic 세션)의 저장 오디오로 정규화 효과를 비교한다.

DB에 저장된 mic 세션 segment(진짜 작은 음성)를 꺼내:
  - raw(원본) vs normalize(피크 정규화) 오디오를 둘 다 들을 수 있게 HTML에 임베드
  - 각각 Gemini 로 transcribe 해 텍스트 비교
결과: tests/stt/output/result_real_compare.html

실행:
  python tests/stt/compare/compare_real_quiet.py [--session N]
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np
import soundfile as sf

from src.utils.config_loader import load_config, get_api_key
from tests.stt.db import init_db, list_sessions, get_segments, get_results
from tests.stt.transcriber import GeminiTranscriber, _normalize_pcm, _pcm_to_wav_bytes

SR = 16000
TARGET_LEVEL = 0.1
PERCENTILE = 95.0
MAX_GAIN = 20.0


def _latest_mic_session() -> int | None:
    for s in list_sessions():
        if (s["source"] or "").startswith("mic"):
            return s["id"]
    return None


def _audio_tag(wav_bytes: bytes) -> str:
    b64 = base64.b64encode(wav_bytes).decode("ascii")
    return f'<audio controls preload="none" src="data:audio/wav;base64,{b64}"></audio>'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=int, default=None)
    args = parser.parse_args()

    init_db()
    sid = args.session or _latest_mic_session()
    if sid is None:
        print("mic 세션을 찾을 수 없습니다.")
        return

    app_config = load_config()
    tr = GeminiTranscriber(
        api_key=get_api_key(app_config),
        model="gemini-2.5-flash",
        language="ko",
        prompt="사용자가 게임을 플레이하며 한국어로 혼잣말/감상을 말합니다. 짧은 감탄사도 그대로 옮겨 적으세요.",
    )

    rows = []
    print(f"세션 #{sid} 의 저장 오디오로 비교\n")
    for seg in get_segments(sid):
        if not seg["audio_wav"]:
            continue
        raw, _sr = sf.read(io.BytesIO(seg["audio_wav"]), dtype="float32", always_2d=False)
        if raw.ndim > 1:
            raw = raw.mean(axis=1).astype(np.float32)
        norm = _normalize_pcm(raw, target_level=TARGET_LEVEL, percentile=PERCENTILE, max_gain=MAX_GAIN)

        raw_peak = float(np.abs(raw).max())
        norm_peak = float(np.abs(norm).max())
        gain = norm_peak / raw_peak if raw_peak > 0 else 1.0

        raw_txt, raw_el = tr.transcribe(raw, SR)
        norm_txt, norm_el = tr.transcribe(norm, SR)

        print(f"seg{seg['seg_num']}: peak {raw_peak:.4f} → {norm_peak:.4f} (x{gain:.1f})")
        print(f"  raw       ({raw_el:.2f}s): '{raw_txt}'")
        print(f"  normalize ({norm_el:.2f}s): '{norm_txt}'\n")

        norm_wav = _pcm_to_wav_bytes(norm, SR)
        rows.append({
            "num": seg["seg_num"],
            "raw_peak": raw_peak, "norm_peak": norm_peak, "gain": gain,
            "raw_audio": _audio_tag(seg["audio_wav"]),
            "norm_audio": _audio_tag(norm_wav),
            "raw_txt": raw_txt or "(빈 응답)",
            "norm_txt": norm_txt or "(빈 응답)",
        })

    # HTML
    body = []
    for r in rows:
        body.append(f"""
    <div class="seg">
      <h3>segment #{r['num']} — peak {r['raw_peak']:.4f} → {r['norm_peak']:.4f} (×{r['gain']:.1f})</h3>
      <div class="grid">
        <div class="col"><div class="label">원본(raw) — 작게 들림</div>{r['raw_audio']}
          <div class="txt">{html.escape(r['raw_txt'])}</div></div>
        <div class="col norm"><div class="label">정규화(normalize) — 키운 소리</div>{r['norm_audio']}
          <div class="txt">{html.escape(r['norm_txt'])}</div></div>
      </div>
    </div>""")

    out = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>정규화 전/후 비교 (실제 작은 음성)</title>
<style>
body{{font-family:'Segoe UI',sans-serif;background:#f0f2f5;color:#202124;padding:24px;}}
h1{{font-size:1.3rem;}} h3{{margin:18px 0 8px;font-size:1rem;color:#3c4043;}}
.seg{{background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.15);padding:16px;margin-bottom:16px;}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;}}
.col{{background:#fafafa;border:1px solid #e8eaed;border-radius:6px;padding:12px;}}
.col.norm{{background:#e8f5e9;border-color:#a5d6a7;}}
.label{{font-size:.8rem;font-weight:700;color:#5f6368;margin-bottom:8px;}}
.col.norm .label{{color:#2e7d32;}}
audio{{width:100%;height:34px;}} .txt{{margin-top:10px;line-height:1.5;font-size:.95rem;}}
</style></head><body>
<h1>정규화 전/후 비교 — result.html 의 실제 작은 음성</h1>
<p style="color:#5f6368;font-size:.9rem;">왼쪽 원본을 들으면 작고, 오른쪽 정규화는 키운 소리입니다. transcript 도 함께 비교하세요.</p>
{''.join(body)}
</body></html>"""

    out_path = ROOT / "tests" / "stt" / "output" / "result_real_compare.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out, encoding="utf-8")
    print(f"리포트: {out_path}")


if __name__ == "__main__":
    main()
