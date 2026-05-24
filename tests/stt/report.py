"""
DB에서 STT 세션 데이터를 읽어 HTML 리포트를 생성한다.

단독 실행:
  python tests/stt/report.py                     # 최신 세션
  python tests/stt/report.py --session 3         # 세션 ID 지정
  python tests/stt/report.py --all               # 전체 세션
  python tests/stt/report.py --out result.html   # 출력 경로 지정
"""
import argparse
import base64
import html
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from tests.stt.db import init_db, list_sessions, get_segments, get_results

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; color: #202124; }
h1 { padding: 20px 24px 8px; font-size: 1.3rem; color: #3c4043; }
.session-block { margin: 16px 24px; background: #fff; border-radius: 8px;
                 box-shadow: 0 1px 4px rgba(0,0,0,.15); overflow: hidden; }
.session-header { padding: 12px 16px; background: #0f9d58; color: #fff;
                  cursor: pointer; display: flex; justify-content: space-between; align-items: center; }
.session-header h2 { font-size: 1rem; font-weight: 600; }
.session-meta { font-size: .8rem; opacity: .85; }
.session-body { display: none; }
.session-body.open { display: block; }
.col-headers { display: grid; grid-template-columns: 110px 280px 1fr 80px;
               background: #f8f9fa; border-bottom: 1px solid #e0e0e0; }
.col-headers span { padding: 8px 12px; font-size: .82rem; font-weight: 700;
                    color: #5f6368; border-right: 1px solid #e0e0e0; }
.col-headers span:last-child { border-right: none; }
.seg-row { display: grid; grid-template-columns: 110px 280px 1fr 80px;
             border-bottom: 1px solid #e8eaed; }
.seg-row:last-child { border-bottom: none; }
.seg-row:nth-child(even) { background: #fafafa; }
.cell { padding: 10px 12px; border-right: 1px solid #e8eaed; }
.cell:last-child { border-right: none; }
.cell-time { font-family: 'Consolas', monospace; font-size: .82rem; color: #5f6368; }
.cell-dur { font-size: .72rem; color: #9aa0a6; margin-top: 4px; }
.cell-audio audio { width: 260px; height: 32px; }
.transcript { line-height: 1.55; font-size: .95rem; white-space: pre-wrap; }
.transcript.empty { color: #bdc1c6; font-style: italic; }
.elapsed { font-size: .82rem; color: #9aa0a6; text-align: right; }
.summary { padding: 10px 16px; background: #f1f3f4; font-size: .85rem; color: #3c4043; }
"""

_JS = """
document.querySelectorAll('.session-header').forEach(h => {
  h.addEventListener('click', () => {
    h.nextElementSibling.classList.toggle('open');
    const arrow = h.querySelector('.arrow');
    arrow.textContent = arrow.textContent === '▶' ? '▼' : '▶';
  });
});
const first = document.querySelector('.session-body');
if (first) { first.classList.add('open');
  first.previousElementSibling.querySelector('.arrow').textContent = '▼'; }
"""


def _fmt_time(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}.{int((sec % 1)*10)}"


def _render_segment_row(seg, result) -> str:
    start = seg["start_s"]
    end = seg["end_s"]
    dur = end - start

    time_cell = (
        f'<div class="cell-time">{_fmt_time(start)} → {_fmt_time(end)}</div>'
        f'<div class="cell-dur">#{seg["seg_num"]} · {dur:.2f}s</div>'
    )

    if seg["audio_wav"]:
        b64 = base64.b64encode(seg["audio_wav"]).decode("ascii")
        audio_cell = (
            f'<audio controls preload="none" '
            f'src="data:audio/wav;base64,{b64}"></audio>'
        )
    else:
        audio_cell = '<span class="transcript empty">오디오 없음</span>'

    if result and result["transcript"]:
        txt_html = f'<div class="transcript">{html.escape(result["transcript"])}</div>'
    else:
        txt_html = '<div class="transcript empty">(빈 응답)</div>'

    elapsed = f'{result["elapsed_sec"]:.2f}s' if result else '-'

    return (
        '<div class="seg-row">'
        f'<div class="cell">{time_cell}</div>'
        f'<div class="cell cell-audio">{audio_cell}</div>'
        f'<div class="cell">{txt_html}</div>'
        f'<div class="cell elapsed">{elapsed}</div>'
        '</div>'
    )


def _render_session(session, segments, results_by_seg: dict[int, list]) -> str:
    sid = session["id"]
    source = session["source"] or ""
    desc = session["description"] or ""
    model = session["stt_model"] or ""

    meta_parts = [session["started_at"], model, source]
    if desc:
        meta_parts.append(desc)
    meta = " · ".join(p for p in meta_parts if p)

    rows = []
    elapseds = []
    for seg in segments:
        res_list = results_by_seg.get(seg["id"], [])
        res = res_list[0] if res_list else None
        if res and res["elapsed_sec"]:
            elapseds.append(res["elapsed_sec"])
        rows.append(_render_segment_row(seg, res))

    summary = ""
    if elapseds:
        avg = sum(elapseds) / len(elapseds)
        summary = (
            f'<div class="summary">'
            f'segment {len(segments)}개 · 평균 STT 응답 {avg:.2f}s · '
            f'총 오디오 {session["total_duration_s"]:.1f}s · '
            f'VAD threshold {session["vad_threshold"]} · sr {session["sample_rate"]}'
            f'</div>'
        )

    body_rows = "\n".join(rows) if rows else '<div class="summary">발화 segment가 없습니다.</div>'

    return f"""
<div class="session-block">
  <div class="session-header">
    <h2><span class="arrow">▶</span>&nbsp;세션 #{sid}</h2>
    <span class="session-meta">{html.escape(meta)}</span>
  </div>
  <div class="session-body">
    {summary}
    <div class="col-headers">
      <span>시간</span>
      <span>오디오</span>
      <span>Transcript</span>
      <span>응답</span>
    </div>
    {body_rows}
  </div>
</div>"""


def build_html(session_ids: list[int] | None = None) -> str:
    sessions = list_sessions()
    if session_ids is not None:
        sessions = [s for s in sessions if s["id"] in session_ids]

    blocks = []
    for session in sessions:
        segs = get_segments(session["id"])
        results_by_seg: dict[int, list] = {}
        for s in segs:
            results_by_seg[s["id"]] = get_results(s["id"])
        blocks.append(_render_session(session, segs, results_by_seg))

    content = "\n".join(blocks) if blocks else "<p style='padding:24px'>세션이 없습니다.</p>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>STT 테스트 리포트</title>
<style>{_CSS}</style>
</head>
<body>
<h1>STT (음성 인식) 테스트 리포트</h1>
{content}
<script>{_JS}</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=int, help="특정 세션 ID")
    parser.add_argument("--all", action="store_true", help="전체 세션")
    parser.add_argument("--out", default=str(ROOT / "tests" / "stt" / "result.html"))
    args = parser.parse_args()

    init_db()

    if args.session:
        session_ids = [args.session]
    elif args.all:
        session_ids = None
    else:
        sessions = list_sessions()
        if not sessions:
            print("저장된 세션이 없습니다.")
            return
        session_ids = [sessions[0]["id"]]

    html_content = build_html(session_ids)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_content, encoding="utf-8")
    print(f"리포트 저장: {out_path}")


if __name__ == "__main__":
    main()
