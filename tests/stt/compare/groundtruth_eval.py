"""
정답 기반 STT 정확도 평가 — 모델 받아쓰기 vs 내가 실제로 한 말.

compare_providers.py 와 동일하게 (오디오 파일 → VAD segment → 모델별 받아쓰기)를
표로 보여주되, 제일 오른쪽에 "정답(내가 한 말)" 입력 칸을 둔다.
왼쪽 오디오를 듣고 직접 받아 적으면, 각 모델 셀이 그 정답과 비교해
  - 글자 단위 정확도(%) = 1 - CER (편집거리 기반, 공백/문장부호 무시)
  - 정답 대비 diff 하이라이트 (모델이 틀린 글자=빨강, 빠뜨린 글자=초록)
를 실시간으로 보여주고, 하단에 모델별 평균 정확도를 집계한다.

입력한 정답은 브라우저 localStorage 에 저장되어 새로고침/재실행해도 유지되며,
JSON export/import 로 백업·이전할 수 있다 (key 는 파일명 + segment 번호).

결과:
  - 콘솔에 segment별 모델 텍스트/지연 출력
  - tests/stt/output/result_groundtruth.html (오디오 + 모델 비교 + 정답 입력)

실행:
  python tests/stt/compare/groundtruth_eval.py
  python tests/stt/compare/groundtruth_eval.py --file tests/stt/audio/crosscode_audio_01.wav --max-segments 8
  python tests/stt/compare/groundtruth_eval.py --also-mini      # OpenAI mini 열 추가
  python tests/stt/compare/groundtruth_eval.py --only gemini    # gemini 만
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np

from src.utils.config_loader import load_config, get_api_key
from tests.stt.transcriber import GeminiTranscriber, _pcm_to_wav_bytes
from tests.stt.openai_transcriber import OpenAITranscriber
from tests.stt.vad_util import SileroVAD, SpeechSegment

SR = 16000
_PROMPT = "사용자가 게임을 플레이하며 한국어로 혼잣말/감상을 말합니다. 짧은 감탄사도 그대로 옮겨 적으세요."
_OUT = ROOT / "tests" / "stt" / "output" / "result_groundtruth.html"


def _load_audio(path: Path, target_sr: int) -> np.ndarray:
    import soundfile as sf

    pcm, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1).astype(np.float32)
    if sr != target_sr:
        out_len = int(round(len(pcm) * target_sr / sr))
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
    parser.add_argument("--only", choices=["gemini", "openai"], default=None,
                        help="한 provider만 비교")
    args = parser.parse_args()

    path = ROOT / args.file
    if not path.exists():
        print(f"[오류] 입력 파일이 없습니다: {path}")
        return

    print(f"오디오 로드: {path}")
    pcm = _load_audio(path, SR)
    print(f"  {len(pcm)/SR:.1f}s · {SR}Hz · {len(pcm):,} samples")

    vad = SileroVAD(threshold=0.5, sample_rate=SR,
                    min_speech_ms=250, min_silence_ms=500, speech_pad_ms=100)
    segments: list[SpeechSegment] = vad.segments(pcm)
    if args.max_segments > 0:
        segments = segments[: args.max_segments]
    print(f"발화 segment {len(segments)}개\n")
    if not segments:
        print("발화가 감지되지 않았습니다.")
        return

    # ── 비교 컬럼(모델) 구성 ──────────────────────────────────────────────
    columns: list[tuple[str, object]] = []
    if args.only != "openai":
        app_config = load_config()
        columns.append((
            f"Gemini · {args.gemini_model}",
            GeminiTranscriber(api_key=get_api_key(app_config),
                              model=args.gemini_model, language="ko", prompt=_PROMPT),
        ))
    if args.only != "gemini":
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            print("[오류] .env.local 에 OPENAI_API_KEY 가 없습니다.")
            return
        columns.append((
            f"OpenAI · {args.openai_model}",
            OpenAITranscriber(api_key=openai_key, model=args.openai_model, language="ko"),
        ))
        if args.also_mini:
            columns.append((
                "OpenAI · gpt-4o-mini-transcribe",
                OpenAITranscriber(api_key=openai_key, model="gpt-4o-mini-transcribe", language="ko"),
            ))

    labels = [label for label, _ in columns]
    rows = []
    totals = {label: 0.0 for label in labels}
    for i, seg in enumerate(segments, start=1):
        print(f"[seg {i}/{len(segments)}] {seg.start_s:.2f}s → {seg.end_s:.2f}s ({seg.duration_s:.2f}s)")
        cells = []
        for label, tr in columns:
            text, elapsed = tr.transcribe(seg.pcm, SR)
            totals[label] += elapsed
            print(f"  {label:42s} ({elapsed:5.2f}s): '{text}'")
            cells.append({"text": text, "elapsed": elapsed})
        wav = _pcm_to_wav_bytes(seg.pcm, SR)
        rows.append({"num": i, "start": seg.start_s, "end": seg.end_s,
                     "dur": seg.duration_s, "audio": _audio_tag(wav), "cells": cells})
        print()

    print("=" * 60)
    for label in labels:
        print(f"  {label:42s} 평균 응답 {totals[label]/len(segments):.2f}s")
    print("=" * 60)
    print("정답은 생성된 HTML 의 오른쪽 칸에 직접 입력하세요 (브라우저에 자동 저장).")

    _write_html(rows, labels, path.name)


# ── HTML (표 본문은 Python, 정확도/diff/평균은 브라우저 JS) ──────────────────────

def _write_html(rows, labels, filename) -> None:
    head_cells = "".join(f"<th>{html.escape(l)}</th>" for l in labels)
    avg_cells = "".join(f'<th class="avg" id="avg-{c}">—</th>' for c in range(len(labels)))

    body = []
    for r in rows:
        model_cells = []
        for c, cell in enumerate(r["cells"]):
            esc = html.escape(cell["text"]) if cell["text"] else ""
            model_cells.append(
                f'<td class="m" data-row="{r["num"]}" data-col="{c}">'
                f'<div class="orig" data-text="{html.escape(cell["text"] or "", quote=True)}">'
                f'{esc or "<span class=empty>(빈 응답)</span>"}</div>'
                f'<div class="badge"></div>'
                f'<div class="el">{cell["elapsed"]:.2f}s</div></td>'
            )
        truth_cell = (
            f'<td class="truth"><textarea data-num="{r["num"]}" rows="3" '
            f'placeholder="왼쪽 오디오를 듣고 실제로 한 말을 적으세요"></textarea></td>'
        )
        body.append(
            f'<tr><td class="audio"><div class="meta">#{r["num"]} · '
            f'{r["start"]:.1f}–{r["end"]:.1f}s ({r["dur"]:.1f}s)</div>{r["audio"]}</td>'
            f'{"".join(model_cells)}{truth_cell}</tr>'
        )

    file_key = json.dumps(filename, ensure_ascii=False)
    labels_json = json.dumps(labels, ensure_ascii=False)

    tpl = _HTML_TEMPLATE
    tpl = tpl.replace("__FILENAME__", html.escape(filename))
    tpl = tpl.replace("__NSEG__", str(len(rows)))
    tpl = tpl.replace("__NCOL__", str(len(labels)))
    tpl = tpl.replace("__HEAD_CELLS__", head_cells)
    tpl = tpl.replace("__AVG_CELLS__", avg_cells)
    tpl = tpl.replace("__BODY__", "".join(body))
    tpl = tpl.replace("__FILE_KEY__", file_key)
    tpl = tpl.replace("__LABELS__", labels_json)

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(tpl, encoding="utf-8")
    print(f"\n리포트: {_OUT}")


_HTML_TEMPLATE = r"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>STT 정답 비교 — 모델 vs 내가 한 말</title>
<style>
*{box-sizing:border-box;}
body{font-family:'Segoe UI',sans-serif;background:#f0f2f5;color:#202124;padding:20px;margin:0;}
h1{font-size:1.25rem;margin:0 0 4px;}
p.sub{color:#5f6368;font-size:.88rem;margin:0 0 14px;}
.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px;}
.bar button{font:inherit;font-size:.82rem;padding:6px 12px;border:1px solid #d0d3d7;border-radius:6px;
  background:#fff;cursor:pointer;}
.bar button:hover{background:#f1f3f4;}
.bar .stat{font-size:.85rem;color:#3c4043;background:#e8f0fe;padding:6px 12px;border-radius:6px;}
table{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.15);
  border-radius:8px;overflow:hidden;}
th,td{border:1px solid #e8eaed;padding:9px 11px;vertical-align:top;text-align:left;}
th{background:#f8f9fa;font-size:.82rem;color:#3c4043;position:sticky;top:0;}
td.audio{width:210px;background:#f4f6f8;}
td.audio .meta{font-size:.74rem;color:#5f6368;margin-bottom:6px;}
audio{width:190px;height:34px;}
td.m{min-width:200px;}
td.m .orig{line-height:1.55;font-size:.95rem;white-space:pre-wrap;word-break:break-word;}
td.m .el{font-size:.72rem;color:#9aa0a6;margin-top:6px;}
.empty{color:#bdc1c6;font-style:italic;}
.badge{display:inline-block;font-size:.78rem;font-weight:700;margin-top:7px;padding:2px 8px;
  border-radius:10px;}
.badge.g{background:#e6f4ea;color:#137333;}
.badge.o{background:#fef7e0;color:#b06000;}
.badge.r{background:#fce8e6;color:#c5221f;}
del{background:#fce8e6;color:#c5221f;text-decoration:line-through;border-radius:2px;}
ins{background:#e6f4ea;color:#137333;text-decoration:none;border-radius:2px;}
td.truth{width:260px;background:#fffdf5;}
td.truth textarea{width:100%;font:inherit;font-size:.95rem;line-height:1.5;padding:8px;
  border:1px solid #e0c97f;border-radius:6px;resize:vertical;background:#fff;}
td.truth textarea:focus{outline:2px solid #f9c84a;border-color:#f9c84a;}
tfoot th{background:#e8f0fe;font-size:.85rem;}
th.avg{font-weight:700;color:#174ea6;}
tr:nth-child(even) td.m{background:#fcfcfc;}
.legend{font-size:.78rem;color:#5f6368;margin-top:10px;}
.legend del,.legend ins{padding:0 3px;}
</style></head><body>
<h1>STT 정답 비교 — 모델 vs 내가 한 말</h1>
<p class="sub">입력: __FILENAME__ · segment __NSEG__개 · 모델 __NCOL__개. 오른쪽 칸에 실제로 한 말을 적으면 모델별 정확도와 diff 가 실시간 계산됩니다.</p>
<div class="bar">
  <span class="stat" id="filled">정답 0/__NSEG__</span>
  <span class="stat" id="overall">전체 평균 —</span>
  <button id="export">정답 JSON 내보내기</button>
  <button id="import">정답 JSON 불러오기</button>
  <button id="clear">정답 전체 지우기</button>
  <input type="file" id="importFile" accept="application/json" style="display:none">
</div>
<table>
<thead><tr><th>오디오</th>__HEAD_CELLS__<th>정답(내가 한 말)</th></tr></thead>
<tbody>__BODY__</tbody>
<tfoot><tr><th>평균 정확도</th>__AVG_CELLS__<th></th></tr></tfoot>
</table>
<p class="legend">정확도 = 1 − CER(글자 편집거리 / 정답 길이), 공백·문장부호 무시. diff 색: <del>모델이 틀린 글자</del> · <ins>정답에 있는데 빠뜨린 글자</ins>. 정답은 이 브라우저에 자동 저장됩니다.</p>
<script>
const FILE_KEY = __FILE_KEY__;
const LABELS = __LABELS__;
const NSEG = __NSEG__;
const STORE = "solo_stream_gt::" + FILE_KEY;

function loadTruths(){
  try { return JSON.parse(localStorage.getItem(STORE) || "{}"); }
  catch(e){ return {}; }
}
function saveTruths(t){ localStorage.setItem(STORE, JSON.stringify(t)); }

// 공백/문장부호 제거 + 소문자 + NFC
function norm(s){
  s = (s||"").normalize("NFC").toLowerCase();
  try { return s.replace(/[\s\p{P}\p{S}]/gu, ""); }
  catch(e){ return s.replace(/[\s.,!?~…"'`·。、!?]/g, ""); }
}

function lev(a, b){
  const n=a.length, m=b.length;
  if(!n) return m; if(!m) return n;
  let prev=new Array(m+1); for(let j=0;j<=m;j++) prev[j]=j;
  for(let i=1;i<=n;i++){
    let cur=[i];
    for(let j=1;j<=m;j++){
      const cost = a[i-1]===b[j-1] ? 0 : 1;
      cur[j] = Math.min(prev[j]+1, cur[j-1]+1, prev[j-1]+cost);
    }
    prev=cur;
  }
  return prev[m];
}

function accuracy(ref, hyp){
  const r=norm(ref); if(!r.length) return null;
  const h=norm(hyp);
  return Math.max(0, 1 - lev(r, h)/r.length);
}

const esc = s => s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");

// ref(정답) 대비 hyp(모델) diff — LCS 백트레이스
function diffHtml(ref, hyp){
  const n=ref.length, m=hyp.length;
  if(!n) return esc(hyp);
  const dp=[]; for(let i=0;i<=n;i++) dp.push(new Int32Array(m+1));
  for(let i=n-1;i>=0;i--) for(let j=m-1;j>=0;j--)
    dp[i][j] = ref[i]===hyp[j] ? dp[i+1][j+1]+1 : Math.max(dp[i+1][j], dp[i][j+1]);
  let i=0, j=0, out=[];
  while(i<n && j<m){
    if(ref[i]===hyp[j]){ out.push(esc(hyp[j])); i++; j++; }
    else if(dp[i+1][j] >= dp[i][j+1]){ out.push("<ins>"+esc(ref[i])+"</ins>"); i++; }
    else { out.push("<del>"+esc(hyp[j])+"</del>"); j++; }
  }
  while(i<n){ out.push("<ins>"+esc(ref[i])+"</ins>"); i++; }
  while(j<m){ out.push("<del>"+esc(hyp[j])+"</del>"); j++; }
  return out.join("");
}

function badgeClass(acc){ return acc>=0.9 ? "g" : acc>=0.7 ? "o" : "r"; }

function recomputeRow(num){
  const ta = document.querySelector('textarea[data-num="'+num+'"]');
  const truth = ta ? ta.value : "";
  document.querySelectorAll('td.m[data-row="'+num+'"]').forEach(td=>{
    const origDiv = td.querySelector(".orig");
    const badge = td.querySelector(".badge");
    const hyp = origDiv.getAttribute("data-text") || "";
    if(!truth.trim()){
      origDiv.innerHTML = hyp ? esc(hyp) : '<span class="empty">(빈 응답)</span>';
      badge.className="badge"; badge.textContent=""; td.removeAttribute("data-acc");
      return;
    }
    const acc = accuracy(truth, hyp);
    origDiv.innerHTML = diffHtml(truth, hyp) || '<span class="empty">(빈 응답)</span>';
    badge.className = "badge " + badgeClass(acc);
    badge.textContent = (acc*100).toFixed(0) + "%";
    td.setAttribute("data-acc", acc);
  });
}

function recomputeAverages(){
  const ncol = LABELS.length;
  for(let c=0;c<ncol;c++){
    const cells=[...document.querySelectorAll('td.m[data-col="'+c+'"][data-acc]')];
    const el=document.getElementById("avg-"+c);
    if(!cells.length){ el.textContent="—"; continue; }
    const avg = cells.reduce((s,td)=>s+parseFloat(td.getAttribute("data-acc")),0)/cells.length;
    el.textContent = (avg*100).toFixed(1)+"% (n="+cells.length+")";
  }
  const truths=loadTruths();
  const filled=Object.values(truths).filter(v=>v && v.trim()).length;
  document.getElementById("filled").textContent = "정답 "+filled+"/"+NSEG;
  // 전체 평균 = 모든 모델 셀의 정확도 평균
  const all=[...document.querySelectorAll('td.m[data-acc]')].map(td=>parseFloat(td.getAttribute("data-acc")));
  document.getElementById("overall").textContent = all.length
    ? "전체 평균 "+((all.reduce((a,b)=>a+b,0)/all.length)*100).toFixed(1)+"%" : "전체 평균 —";
}

function recomputeAll(){
  document.querySelectorAll('textarea[data-num]').forEach(ta=>recomputeRow(ta.getAttribute("data-num")));
  recomputeAverages();
}

document.addEventListener("DOMContentLoaded", ()=>{
  const truths=loadTruths();
  document.querySelectorAll('textarea[data-num]').forEach(ta=>{
    const num=ta.getAttribute("data-num");
    if(truths[num]!=null) ta.value=truths[num];
    ta.addEventListener("input", ()=>{
      const t=loadTruths(); t[num]=ta.value; saveTruths(t);
      recomputeRow(num); recomputeAverages();
    });
  });
  recomputeAll();

  document.getElementById("export").addEventListener("click", ()=>{
    const data={file:FILE_KEY, generatedAt:new Date().toISOString(), truths:loadTruths()};
    const blob=new Blob([JSON.stringify(data,null,2)],{type:"application/json"});
    const a=document.createElement("a");
    a.href=URL.createObjectURL(blob);
    a.download="groundtruth_"+FILE_KEY.replace(/[^\w.-]/g,"_")+".json";
    a.click(); URL.revokeObjectURL(a.href);
  });
  document.getElementById("import").addEventListener("click", ()=>document.getElementById("importFile").click());
  document.getElementById("importFile").addEventListener("change", ev=>{
    const f=ev.target.files[0]; if(!f) return;
    const rd=new FileReader();
    rd.onload=()=>{
      try{
        const obj=JSON.parse(rd.result);
        const t=obj.truths||obj;
        saveTruths(t);
        document.querySelectorAll('textarea[data-num]').forEach(ta=>{
          const num=ta.getAttribute("data-num");
          ta.value = t[num]!=null ? t[num] : "";
        });
        recomputeAll();
      }catch(e){ alert("JSON 파싱 실패: "+e); }
    };
    rd.readAsText(f);
    ev.target.value="";
  });
  document.getElementById("clear").addEventListener("click", ()=>{
    if(!confirm("이 파일의 정답 입력을 모두 지울까요?")) return;
    localStorage.removeItem(STORE);
    document.querySelectorAll('textarea[data-num]').forEach(ta=>ta.value="");
    recomputeAll();
  });
});
</script>
</body></html>"""


if __name__ == "__main__":
    main()
