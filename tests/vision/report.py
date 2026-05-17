"""
DB에서 세션 데이터를 읽어 HTML 리포트를 생성한다.

단독 실행:
  python tests/vision/report.py                     # 최신 세션
  python tests/vision/report.py --session 3         # 세션 ID 지정
  python tests/vision/report.py --all               # 전체 세션
  python tests/vision/report.py --out result.html   # 출력 경로 지정
"""
import argparse
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from tests.vision.db import init_db, list_sessions, get_session, get_rounds, get_results

_FIELD_COLORS = {
    "묘사": "#1a73e8",
    "위치": "#0f9d58",
    "상황": "#f4b400",
    "이벤트": "#db4437",
}

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; color: #202124; }
h1 { padding: 20px 24px 8px; font-size: 1.3rem; color: #3c4043; }
.session-block { margin: 16px 24px; background: #fff; border-radius: 8px;
                 box-shadow: 0 1px 4px rgba(0,0,0,.15); overflow: hidden; }
.session-header { padding: 12px 16px; background: #1a73e8; color: #fff;
                  cursor: pointer; display: flex; justify-content: space-between; align-items: center; }
.session-header h2 { font-size: 1rem; font-weight: 600; }
.session-meta { font-size: .8rem; opacity: .85; }
.session-body { display: none; }
.session-body.open { display: block; }
.col-headers { display: grid; grid-template-columns: 220px 1fr 1fr;
               background: #f8f9fa; border-bottom: 1px solid #e0e0e0; }
.col-headers span { padding: 8px 12px; font-size: .82rem; font-weight: 700;
                    color: #5f6368; text-align: center; border-right: 1px solid #e0e0e0; }
.col-headers span:last-child { border-right: none; }
.round-row { display: grid; grid-template-columns: 220px 1fr 1fr;
             border-bottom: 1px solid #e8eaed; }
.round-row:last-child { border-bottom: none; }
.round-row:nth-child(even) { background: #fafafa; }
.cell { padding: 10px 12px; border-right: 1px solid #e8eaed; vertical-align: top; }
.cell:last-child { border-right: none; }
.cell-img { display: flex; flex-direction: column; align-items: center; gap: 6px; }
.cell-img img { width: 200px; height: auto; border-radius: 4px; border: 1px solid #ddd;
                cursor: zoom-in; transition: transform .15s; }
.cell-img img:hover { transform: scale(1.04); }
.round-label { font-size: .72rem; color: #9aa0a6; }
.ts-label { font-size: .72rem; color: #9aa0a6; }
.field { margin-bottom: 5px; line-height: 1.5; font-size: .85rem; }
.field-label { font-weight: 700; margin-right: 4px; }
.elapsed { font-size: .72rem; color: #9aa0a6; margin-top: 6px; }
.empty { color: #bdc1c6; font-style: italic; font-size: .82rem; }
/* 라이트박스 */
#lb { display:none; position:fixed; inset:0; background:rgba(0,0,0,.8);
      z-index:9999; align-items:center; justify-content:center; }
#lb.active { display:flex; }
#lb img { max-width:90vw; max-height:90vh; border-radius:6px; }
#lb-close { position:fixed; top:16px; right:20px; font-size:2rem; color:#fff;
             cursor:pointer; line-height:1; }
"""

_JS = """
document.querySelectorAll('.session-header').forEach(h => {
  h.addEventListener('click', () => {
    h.nextElementSibling.classList.toggle('open');
    const arrow = h.querySelector('.arrow');
    arrow.textContent = arrow.textContent === '▶' ? '▼' : '▶';
  });
});
// 첫 세션 자동 열기
const first = document.querySelector('.session-body');
if (first) { first.classList.add('open');
  first.previousElementSibling.querySelector('.arrow').textContent = '▼'; }

// 라이트박스
const lb = document.getElementById('lb');
const lbImg = lb.querySelector('img');
document.querySelectorAll('.cell-img img').forEach(img => {
  img.addEventListener('click', () => {
    lbImg.src = img.src; lb.classList.add('active'); });
});
lb.addEventListener('click', e => { if (e.target !== lbImg) lb.classList.remove('active'); });
document.getElementById('lb-close').addEventListener('click', () => lb.classList.remove('active'));
"""


def _parse_fields(text: str) -> dict[str, str]:
    """묘사/위치/상황/이벤트 파싱."""
    fields: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        matched = False
        for key in ("묘사", "위치", "상황", "이벤트"):
            if stripped.startswith(f"{key}:"):
                current = key
                fields[key] = stripped[len(key) + 1:].strip()
                matched = True
                break
        if not matched and current and stripped:
            fields[current] = fields[current] + " " + stripped
    return fields


def _render_result(text: str | None, elapsed: float | None) -> str:
    if not text:
        return '<span class="empty">결과 없음</span>'
    fields = _parse_fields(text)
    parts = []
    for key, color in _FIELD_COLORS.items():
        val = fields.get(key)
        if val:
            label = f'<span class="field-label" style="color:{color}">{html.escape(key)}:</span>'
            parts.append(f'<div class="field">{label}{html.escape(val)}</div>')
    if elapsed is not None:
        parts.append(f'<div class="elapsed">{elapsed:.2f}s</div>')
    return "\n".join(parts) if parts else f'<pre style="font-size:.8rem">{html.escape(text)}</pre>'


def _render_session(session, rounds, all_results: dict[int, list]) -> str:
    sid = session["id"]
    gemini_model = session["gemini_model"] or "Gemini"
    claude_model = session["claude_model"] or "Claude"
    source = session["source"] or ""
    desc = session["description"] or ""

    meta_parts = [session["started_at"], session["game"] or ""]
    if desc:
        meta_parts.append(desc)
    meta = " · ".join(p for p in meta_parts if p)

    rows = []
    for r in rounds:
        rid = r["id"]
        results_by_model = {res["model"]: res for res in all_results.get(rid, [])}

        # 이미지 셀
        if r["image_b64"]:
            ts_html = ""
            if r["timestamp_sec"] is not None:
                m, s = divmod(int(r["timestamp_sec"]), 60)
                ts_html = f'<span class="ts-label">{r["timestamp_sec"]:.1f}s ({m:02d}:{s:02d})</span>'
            img_tag = (
                f'<img src="data:image/jpeg;base64,{r["image_b64"]}" '
                f'alt="round {r["round_num"]}" loading="lazy">'
            )
            img_cell = f'<div class="cell-img">{img_tag}<span class="round-label">라운드 {r["round_num"]}</span>{ts_html}</div>'
        else:
            img_cell = f'<div class="round-label">라운드 {r["round_num"]}</div>'

        g = results_by_model.get("gemini")
        c = results_by_model.get("claude")

        g_html = _render_result(g["text"] if g else None, g["elapsed_sec"] if g else None)
        c_html = _render_result(c["text"] if c else None, c["elapsed_sec"] if c else None)

        rows.append(
            f'<div class="round-row">'
            f'<div class="cell cell-img">{img_cell}</div>'
            f'<div class="cell">{g_html}</div>'
            f'<div class="cell">{c_html}</div>'
            f'</div>'
        )

    body_rows = "\n".join(rows)
    return f"""
<div class="session-block">
  <div class="session-header">
    <h2><span class="arrow">▶</span>&nbsp;세션 #{sid} — {html.escape(source)}</h2>
    <span class="session-meta">{html.escape(meta)}</span>
  </div>
  <div class="session-body">
    <div class="col-headers">
      <span>캡처</span>
      <span>{html.escape(gemini_model)}</span>
      <span>{html.escape(claude_model)}</span>
    </div>
    {body_rows}
  </div>
</div>"""


def build_html(session_ids: list[int] | None = None) -> str:
    """session_ids=None 이면 전체 세션."""
    sessions = list_sessions()
    if session_ids is not None:
        sessions = [s for s in sessions if s["id"] in session_ids]

    blocks = []
    for session in sessions:
        rounds = get_rounds(session["id"])
        all_results: dict[int, list] = {}
        for r in rounds:
            all_results[r["id"]] = get_results(r["id"])
        blocks.append(_render_session(session, rounds, all_results))

    content = "\n".join(blocks) if blocks else "<p style='padding:24px'>세션이 없습니다.</p>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vision 모델 비교</title>
<style>{_CSS}</style>
</head>
<body>
<h1>Vision 모델 비교 테스트</h1>
{content}
<!-- 라이트박스 -->
<div id="lb"><span id="lb-close">✕</span><img src="" alt=""></div>
<script>{_JS}</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=int, help="특정 세션 ID")
    parser.add_argument("--all", action="store_true", help="전체 세션")
    parser.add_argument("--out", default=str(ROOT / "tests" / "vision" / "result.html"))
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
