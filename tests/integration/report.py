"""
Vision + STT 통합 테스트 HTML 리포트.

실행:
  python tests/integration/report.py                     # 최신 세션
  python tests/integration/report.py --session 3
  python tests/integration/report.py --all
"""
import argparse
import base64
import html
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from tests.integration.db import (
    init_db, list_sessions, get_rounds, get_transcripts, get_round_transcripts,
    get_chat_calls, get_chat_messages,
)

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
.session-body { display: none; padding: 12px 16px; }
.session-body.open { display: block; }
.summary { font-size: .85rem; color: #5f6368; margin-bottom: 12px; }

/* 타임라인 — 발화 (좌측 narrow) + 라운드 (우측 wide) */
.timeline { display: grid; grid-template-columns: 320px 1fr; gap: 12px; }
.tx-list h3, .round-list h3 {
  font-size: .9rem; color: #5f6368; padding: 6px 8px;
  border-bottom: 1px solid #e0e0e0; margin-bottom: 8px;
}
.tx-card { padding: 8px; background: #f8f9fa; border-left: 3px solid #0f9d58;
           margin-bottom: 8px; border-radius: 0 4px 4px 0; }
.tx-time { font-family: 'Consolas', monospace; font-size: .75rem; color: #5f6368; }
.tx-text { font-size: .9rem; margin-top: 4px; white-space: pre-wrap; }
.tx-text.empty { color: #bdc1c6; font-style: italic; }
.tx-card audio { width: 100%; height: 28px; margin-top: 4px; }

.round-card { background: #fff; border: 1px solid #e0e0e0; border-radius: 6px;
              padding: 12px; margin-bottom: 14px; }
.round-card-head { display: flex; justify-content: space-between; align-items: center;
                   margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid #e8eaed; }
.round-card-head h4 { font-size: .95rem; color: #3c4043; }
.round-time { font-family: 'Consolas', monospace; font-size: .8rem; color: #5f6368; }
.round-body { display: grid; grid-template-columns: 280px 1fr; gap: 14px; }
.round-img img { width: 280px; height: auto; border-radius: 4px; border: 1px solid #ddd;
                 cursor: zoom-in; transition: transform .15s; }
.round-img img:hover { transform: scale(1.04); }
.field { margin-bottom: 5px; line-height: 1.5; font-size: .88rem; }
.field-label { font-weight: 700; margin-right: 4px; }
.elapsed { font-size: .72rem; color: #9aa0a6; margin-top: 6px; }
.ctx-pills { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 4px; }
.ctx-pill { font-size: .72rem; padding: 2px 8px; background: #e8f5e9;
            color: #0f9d58; border-radius: 10px; }
.ctx-pill.empty { background: #fce8e6; color: #c5221f; }

/* chat 가중치 비교 */
.chat-grid { margin-top: 14px; display: grid; gap: 8px;
             grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
.chat-col { background: #fafafa; border: 1px solid #e0e0e0; border-radius: 6px;
            padding: 8px; }
.chat-col-head { display: flex; justify-content: space-between; align-items: center;
                 margin-bottom: 6px; padding-bottom: 4px; border-bottom: 1px solid #e8eaed; }
.weight-badge { font-size: .72rem; padding: 2px 8px; border-radius: 10px; font-weight: 700; }
.weight-off    { background: #eceff1; color: #455a64; }
.weight-low    { background: #e3f2fd; color: #1565c0; }
.weight-medium { background: #fff8e1; color: #f57f17; }
.weight-high   { background: #ffebee; color: #c62828; }
.chat-meta { font-size: .72rem; color: #9aa0a6; }
.chat-msg { font-size: .85rem; line-height: 1.5; padding: 3px 0;
            border-bottom: 1px dotted #eceff1; }
.chat-msg:last-child { border-bottom: none; }
.chat-msg .bot-name { font-weight: 700; color: #5f6368; margin-right: 6px; }
.chat-empty { font-size: .8rem; color: #bdc1c6; font-style: italic; padding: 4px 0; }

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
const first = document.querySelector('.session-body');
if (first) { first.classList.add('open');
  first.previousElementSibling.querySelector('.arrow').textContent = '▼'; }

const lb = document.getElementById('lb');
const lbImg = lb.querySelector('img');
document.querySelectorAll('.round-img img').forEach(img => {
  img.addEventListener('click', () => {
    lbImg.src = img.src; lb.classList.add('active'); });
});
lb.addEventListener('click', e => { if (e.target !== lbImg) lb.classList.remove('active'); });
document.getElementById('lb-close').addEventListener('click', () => lb.classList.remove('active'));
"""


def _fmt_time(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}.{int((sec % 1)*10)}"


def _parse_vision_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current = None
    for line in (text or "").splitlines():
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


def _render_vision(text: str | None, elapsed: float | None) -> str:
    if not text:
        return '<span class="tx-text empty">결과 없음</span>'
    fields = _parse_vision_fields(text)
    parts = []
    for key, color in _FIELD_COLORS.items():
        val = fields.get(key)
        if val:
            label = f'<span class="field-label" style="color:{color}">{html.escape(key)}:</span>'
            parts.append(f'<div class="field">{label}{html.escape(val)}</div>')
    if not parts:
        parts.append(f'<pre style="font-size:.85rem">{html.escape(text)}</pre>')
    if elapsed is not None:
        parts.append(f'<div class="elapsed">{elapsed:.2f}s</div>')
    return "\n".join(parts)


def _render_transcript_card(tx, show_audio: bool = True) -> str:
    start = tx["video_start_s"]
    end = tx["video_end_s"]
    text = tx["transcript"] or ""
    audio_html = ""
    if show_audio and tx["audio_wav"]:
        b64 = base64.b64encode(tx["audio_wav"]).decode("ascii")
        audio_html = f'<audio controls preload="none" src="data:audio/wav;base64,{b64}"></audio>'
    text_html = (
        f'<div class="tx-text">{html.escape(text)}</div>'
        if text
        else '<div class="tx-text empty">(빈 응답)</div>'
    )
    return (
        '<div class="tx-card">'
        f'<div class="tx-time">#{tx["seg_num"]} · {_fmt_time(start)} → {_fmt_time(end)} '
        f'({end - start:.1f}s)</div>'
        f'{text_html}'
        f'{audio_html}'
        '</div>'
    )


def _render_chat_column(call, messages) -> str:
    weight = call["weight"]
    badge = f'<span class="weight-badge weight-{weight}">{html.escape(weight)}</span>'
    meta = f'<span class="chat-meta">{call["elapsed_s"]:.2f}s · {len(messages)}개</span>'

    if messages:
        msg_html = "\n".join(
            f'<div class="chat-msg">'
            f'<span class="bot-name">{html.escape(m["bot_name"])}</span>'
            f'{html.escape(m["message"])}</div>'
            for m in messages
        )
    else:
        msg_html = '<div class="chat-empty">(채팅 없음)</div>'

    return (
        '<div class="chat-col">'
        f'<div class="chat-col-head">{badge}{meta}</div>'
        f'{msg_html}'
        '</div>'
    )


def _render_round_card(r, ctx_transcripts: list, chat_calls: list, messages_by_call: dict) -> str:
    ts = r["video_ts_s"]
    img_b64 = r["image_b64"]
    img_html = (
        f'<img src="data:image/jpeg;base64,{img_b64}" alt="round {r["round_num"]}" loading="lazy">'
        if img_b64 else ''
    )

    vision_html = _render_vision(r["vision_text"], r["vision_elapsed_s"])

    if ctx_transcripts:
        pills = "".join(
            f'<span class="ctx-pill">#{t["seg_num"]} {_fmt_time(t["video_start_s"])} '
            f'{html.escape((t["transcript"] or "")[:24])}'
            f'{"…" if t["transcript"] and len(t["transcript"]) > 24 else ""}</span>'
            for t in ctx_transcripts
        )
    else:
        pills = '<span class="ctx-pill empty">사용자 발화 없음</span>'

    if chat_calls:
        chat_html = (
            '<div class="chat-grid">'
            + "".join(_render_chat_column(c, messages_by_call.get(c["id"], [])) for c in chat_calls)
            + '</div>'
        )
    else:
        chat_html = ''

    return f"""
<div class="round-card">
  <div class="round-card-head">
    <h4>라운드 {r["round_num"]}</h4>
    <span class="round-time">{_fmt_time(ts)}</span>
  </div>
  <div class="round-body">
    <div class="round-img">{img_html}</div>
    <div>
      {vision_html}
      <div class="ctx-pills">{pills}</div>
    </div>
  </div>
  {chat_html}
</div>"""


def _render_session(
    session, rounds, transcripts,
    round_ctx: dict,
    round_chats: dict,
    chat_messages: dict,
) -> str:
    sid = session["id"]
    desc = session["description"] if "description" in session.keys() else ""

    meta_parts = [
        session["started_at"],
        session["game"] or "",
        f"vision={session['vision_model']}",
        f"stt={session['stt_model']}",
        f"offset={session['audio_offset_s']:+.1f}s",
    ]
    meta = " · ".join(p for p in meta_parts if p)

    summary = (
        f'<div class="summary">'
        f'라운드 {len(rounds)}개 · transcript {len(transcripts)}개 · '
        f'lookback {session["lookback_s"]:.0f}s'
        f'</div>'
    )

    tx_html = "\n".join(_render_transcript_card(t) for t in transcripts) \
        if transcripts else '<div class="summary">발화 없음</div>'

    rounds_html = "\n".join(
        _render_round_card(
            r,
            round_ctx.get(r["id"], []),
            round_chats.get(r["id"], []),
            chat_messages,
        ) for r in rounds
    ) if rounds else '<div class="summary">라운드 없음</div>'

    return f"""
<div class="session-block">
  <div class="session-header">
    <h2><span class="arrow">▶</span>&nbsp;세션 #{sid}</h2>
    <span class="session-meta">{html.escape(meta)}</span>
  </div>
  <div class="session-body">
    {summary}
    <div class="timeline">
      <div class="tx-list">
        <h3>STT — 전체 발화 (오디오 타임라인)</h3>
        {tx_html}
      </div>
      <div class="round-list">
        <h3>Vision 라운드 (발화 컨텍스트 합쳐서 호출)</h3>
        {rounds_html}
      </div>
    </div>
  </div>
</div>"""


def build_html(session_ids: list[int] | None = None) -> str:
    sessions = list_sessions()
    if session_ids is not None:
        sessions = [s for s in sessions if s["id"] in session_ids]

    blocks = []
    for session in sessions:
        rounds = get_rounds(session["id"])
        transcripts = get_transcripts(session["id"])
        round_ctx = {r["id"]: get_round_transcripts(r["id"]) for r in rounds}
        round_chats: dict[int, list] = {}
        chat_messages: dict[int, list] = {}
        for r in rounds:
            calls = get_chat_calls(r["id"])
            round_chats[r["id"]] = calls
            for c in calls:
                chat_messages[c["id"]] = get_chat_messages(c["id"])
        blocks.append(_render_session(
            session, rounds, transcripts, round_ctx, round_chats, chat_messages,
        ))

    content = "\n".join(blocks) if blocks else "<p style='padding:24px'>세션이 없습니다.</p>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vision + STT 통합 테스트</title>
<style>{_CSS}</style>
</head>
<body>
<h1>Vision + STT 통합 테스트</h1>
{content}
<div id="lb"><span id="lb-close">✕</span><img src="" alt=""></div>
<script>{_JS}</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=int)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "tests" / "integration" / "result.html"))
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
