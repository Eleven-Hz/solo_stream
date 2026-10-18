"""
Vision + STT 통합 테스트 러너.
영상에서 N초마다 프레임을 뽑아 vision 분석하고,
별도 WAV에서 VAD/STT로 발화를 추출하여
각 vision 라운드 직전 lookback_seconds 구간 발화를 프롬프트에 합쳐 호출한다.

실행:
  python tests/integration/run.py
  python tests/integration/run.py --config tests/integration/config.yaml
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

import json
import random

import numpy as np
import yaml

from src.utils.config_loader import load_config, get_api_key
from src.ai.model_client import GeminiClient
from src.ai.model_client import _build_bots_info, _parse_chats  # noqa: PLC2701
from src.chat.game_profile_loader import load_profile, build_profile_context, build_vision_context
from src.chat.persona_loader import load_personas

from tests.integration.db import (
    init_db, save_session, save_transcript, save_round, save_chat_call,
)
from tests.integration.report import build_html
from tests.stt.transcriber import GeminiTranscriber, _pcm_to_wav_bytes
from tests.stt.vad_util import SileroVAD, SpeechSegment


@dataclass
class Transcript:
    seg_num: int
    video_start_s: float
    video_end_s: float
    text: str
    elapsed: float
    audio_wav: bytes
    db_id: int = 0


# ── 입력 로드 ────────────────────────────────────────────────────────────────

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


def _extract_video_frames(video_path: Path, video_cfg: dict) -> list[tuple[float, bytes]]:
    """영상에서 (timestamp_s, jpeg_bytes) 추출. tests/vision/run.py 로직과 동일."""
    import cv2
    from PIL import Image

    interval = video_cfg.get("frame_interval", 15.0)
    start_sec = video_cfg.get("start", 0.0)
    end_sec = video_cfg.get("end", None)
    resize_w = video_cfg.get("resize_width", 1024)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"영상 파일을 열 수 없습니다: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps
    end_sec = min(end_sec, duration) if end_sec is not None else duration

    frames: list[tuple[float, bytes]] = []
    ts = start_sec
    while ts <= end_sec + 1e-6:
        cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000)
        ret, frame = cap.read()
        if not ret:
            break
        h, w = frame.shape[:2]
        if w > resize_w:
            scale = resize_w / w
            frame = cv2.resize(frame, (resize_w, int(h * scale)), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        frames.append((ts, buf.getvalue()))
        ts += interval

    cap.release()
    return frames


# ── 게임명 추출 (vision/run.py 와 동일) ───────────────────────────────────────

def _extract_game_name(raw: str) -> str:
    name = raw or ""
    name = re.sub(r"\s*[-]\s*Steam.*", "", name)
    name = re.sub(r"\bMin\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\bv\d[\d.]*[-\w]*", "", name, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", name).strip() or "Unknown Game"


# ── chat 프롬프트: 사용자 발화 가중치 ─────────────────────────────────────

# 가중치별 지침. off 는 사용자 발화 섹션 자체를 안 넣음.
_WEIGHT_GUIDELINE = {
    "off": None,
    "low": (
        "[사용자 발화 반영 강도: 낮음]\n"
        "- 사용자가 한 말은 '들은 것'으로 인지하지만 채팅의 주제는 화면 상황이 우선이다.\n"
        "- 봇 중 한 명 정도가 가볍게 동조/리액션 (예: \"ㅇㅈ\", \"진짜요?\") 해도 좋고, 나머지는 화면만 봐도 된다.\n"
        "- 사용자 발화에 직접 답하는 봇은 1명을 넘기지 말 것."
    ),
    "medium": (
        "[사용자 발화 반영 강도: 보통]\n"
        "- 사용자가 한 말을 자연스럽게 받아치거나 동조하는 봇이 1~2명 정도 있어야 한다.\n"
        "- 봇 페르소나에 맞게 반응 — 막내팬은 공감/감탄, 고인물은 짧은 조언/비웃음, 트롤은 빈정거림.\n"
        "- 나머지 봇들은 화면 상황 위주로 반응한다."
    ),
    "high": (
        "[사용자 발화 반영 강도: 높음]\n"
        "- 사용자가 한 말이 이번 라운드의 중심 화제다.\n"
        "- 봇 중 절반 이상이 사용자 발화를 받아치거나 그에 대한 의견/리액션을 낸다.\n"
        "- 단, 사용자 말을 그대로 인용하지 말고 봇 말투로 변형한다."
    ),
}


def _format_user_speech_block(recent: list[Transcript], current_ts: float) -> str:
    """프롬프트용 사용자 발화 섹션 (균일 가중치 = 시간 표시만 함)."""
    if not recent:
        return "(최근 사용자 발화 없음)"
    lines = []
    for t in recent:
        delta_from_now = max(0.0, current_ts - t.video_end_s)
        lines.append(f'- ({delta_from_now:4.1f}s 전) "{t.text}"')
    return "\n".join(lines)


_CHAT_PROMPT_WITH_SPEECH = """당신은 게임 스트리밍 방송의 시청자 봇들입니다.
아래 게임 정보와 현재 상황을 바탕으로 각 봇이 채팅을 칩니다.

{game_profile_context}

[현재 게임 상황]
{game_context}

[최근 사용자 발화 (스트리머가 직접 한 말)]
{user_speech}

{weight_guideline}

[최근 채팅 기록]
{chat_history}

[봇 목록 - {bot_count}명]
{bots_info}

===규칙===
- 각 봇은 반드시 1개의 메시지만 생성. 2개 이상 금지.
- 메시지는 15자 이내. 실제 채팅처럼 짧게.
- 봇 중 일부(1~2명)는 이번엔 침묵해도 됨. 그러면 해당 봇은 결과에서 제외.
- 게임 정보를 아는 시청자처럼: 게임 용어, 캐릭터명, 스킬명을 자연스럽게 사용.
- HP/SP 수치는 직접 읽지 말고 "피 빵빵하네", "기력 다 썼다" 처럼 표현.
- 봇마다 말투가 확실히 달라야 함. 같은 내용 다르게 말하기 금지.

반드시 아래 JSON 배열 형식으로만 응답하세요:
[
  {{"name": "봇이름", "message": "채팅내용"}},
  ...
]"""

_CHAT_PROMPT_NO_SPEECH = """당신은 게임 스트리밍 방송의 시청자 봇들입니다.
아래 게임 정보와 현재 상황을 바탕으로 각 봇이 채팅을 칩니다.

{game_profile_context}

[현재 게임 상황]
{game_context}

[최근 채팅 기록]
{chat_history}

[봇 목록 - {bot_count}명]
{bots_info}

===규칙===
- 각 봇은 반드시 1개의 메시지만 생성. 2개 이상 금지.
- 메시지는 15자 이내. 실제 채팅처럼 짧게.
- 봇 중 일부(1~2명)는 이번엔 침묵해도 됨. 그러면 해당 봇은 결과에서 제외.
- 게임 정보를 아는 시청자처럼: 게임 용어, 캐릭터명, 스킬명을 자연스럽게 사용.
- HP/SP 수치는 직접 읽지 말고 "피 빵빵하네", "기력 다 썼다" 처럼 표현.
- 봇마다 말투가 확실히 달라야 함. 같은 내용 다르게 말하기 금지.

반드시 아래 JSON 배열 형식으로만 응답하세요:
[
  {{"name": "봇이름", "message": "채팅내용"}},
  ...
]"""


def _build_chat_prompt(
    *,
    weight: str,
    game_profile_context: str,
    game_context: str,
    chat_history: list[dict],
    bots: list[dict],
    recent_speech: list[Transcript],
    current_ts: float,
) -> str:
    bots_info = _build_bots_info(bots, chat_history)
    history_text = (
        "\n".join(f"{c['name']}: {c['message']}" for c in chat_history[-10:])
        if chat_history else "(아직 채팅 없음)"
    )
    if weight == "off":
        return _CHAT_PROMPT_NO_SPEECH.format(
            game_profile_context=game_profile_context or "(게임 프로필 없음)",
            game_context=game_context,
            chat_history=history_text,
            bots_info=bots_info,
            bot_count=len(bots),
        )
    guideline = _WEIGHT_GUIDELINE[weight]
    return _CHAT_PROMPT_WITH_SPEECH.format(
        game_profile_context=game_profile_context or "(게임 프로필 없음)",
        game_context=game_context,
        user_speech=_format_user_speech_block(recent_speech, current_ts),
        weight_guideline=guideline,
        chat_history=history_text,
        bots_info=bots_info,
        bot_count=len(bots),
    )


def _call_chat(
    client: GeminiClient,
    chat_model: str,
    prompt: str,
) -> tuple[list[dict], str, float]:
    """generate_content 직접 호출 (본 코드 generate_chats 와 비슷하지만 chat_model 임의 지정)."""
    from google import genai  # noqa: PLC0415
    t0 = time.perf_counter()
    try:
        response = client._client.models.generate_content(  # noqa: SLF001
            model=chat_model,
            contents=prompt,
        )
        raw = (response.text or "").strip()
        chats = _parse_chats(raw)
    except json.JSONDecodeError:
        raw = ""
        chats = []
    except Exception as e:
        raw = f"(error) {e}"
        chats = []
    elapsed = time.perf_counter() - t0
    return chats, raw, elapsed


# ── 메인 ─────────────────────────────────────────────────────────────────────

def _divider(char: str = "-", width: int = 70) -> str:
    return char * width


def run(test_cfg: dict, app_config: dict, out, html_path: Path | None = None):
    def w(line: str = ""):
        out.write(line + "\n")
        out.flush()

    video_cfg = test_cfg["video"]
    audio_cfg = test_cfg["audio"]
    vad_cfg = test_cfg["vad"]
    stt_cfg = test_cfg["stt"]
    vision_cfg = test_cfg["vision"]
    chat_cfg = test_cfg["chat"]
    ctx_cfg = test_cfg["context"]
    out_cfg = test_cfg.get("output", {})

    sample_rate = vad_cfg["sample_rate"]
    audio_offset = float(audio_cfg.get("offset_seconds", 0.0))
    lookback_s = float(ctx_cfg["lookback_seconds"])
    min_chars = int(ctx_cfg.get("min_transcript_chars", 0))

    # YAML이 off/on을 bool로 파싱하는 함정 대비
    raw_weights = chat_cfg.get("weights", ["medium"])
    weights = [
        "off" if v is False else "on" if v is True else str(v).lower()
        for v in raw_weights
    ]
    for w_ in weights:
        if w_ not in _WEIGHT_GUIDELINE:
            raise ValueError(f"알 수 없는 chat weight: {w_}")
    share_bots = bool(chat_cfg.get("share_bots_across_weights", True))
    batch_size = int(chat_cfg["batch_size"])
    chat_model = chat_cfg["model"]

    # 1) 입력 검증
    video_path = ROOT / video_cfg["file"]
    audio_path = ROOT / audio_cfg["file"]
    if not video_path.exists():
        w(f"[오류] 비디오 없음: {video_path}"); return
    if not audio_path.exists():
        w(f"[오류] 오디오 없음: {audio_path}"); return

    # 2) 키 준비
    api_key = get_api_key(app_config)
    app_config["ai"]["vision_model"] = vision_cfg["model"]
    gemini_client = GeminiClient(api_key=api_key, config=app_config)
    transcriber = GeminiTranscriber(
        api_key=api_key,
        model=stt_cfg["model"],
        language=stt_cfg["language"],
        prompt=stt_cfg.get("prompt"),
    )

    # 3) 게임 프로필
    game_raw = test_cfg.get("game") or ""
    game_name = _extract_game_name(game_raw)
    w(f"게임 프로필 로드: {game_name}")
    game_profile = load_profile(game_name, ai_client=gemini_client, config=app_config, auto_generate=True)
    vision_profile_context = build_vision_context(game_profile)
    chat_profile_context = build_profile_context(game_profile)

    # 페르소나 풀
    bot_pool = load_personas(chat_cfg["persona_dir"])
    w(f"페르소나 풀 {len(bot_pool)}명")

    # 4) 비디오 프레임 추출
    w(f"비디오 프레임 추출: {video_path}")
    frames = _extract_video_frames(video_path, video_cfg)
    w(f"  → {len(frames)}개 프레임 ({video_cfg.get('start',0)}s ~ {video_cfg.get('end','end')}s, "
      f"{video_cfg['frame_interval']}s 간격)")

    # 5) 오디오 로드 + VAD
    w(f"오디오 로드: {audio_path}")
    pcm = _load_audio(audio_path, sample_rate)
    audio_duration = len(pcm) / sample_rate
    w(f"  → {audio_duration:.1f}s @ {sample_rate}Hz (offset {audio_offset:+.1f}s)")

    w("VAD 발화 구간 검출 중...")
    vad = SileroVAD(
        threshold=vad_cfg["threshold"],
        sample_rate=sample_rate,
        min_speech_ms=vad_cfg["min_speech_ms"],
        min_silence_ms=vad_cfg["min_silence_ms"],
        speech_pad_ms=vad_cfg.get("speech_pad_ms", 100),
    )
    segments: list[SpeechSegment] = vad.segments(pcm)
    w(f"  → 발화 segment {len(segments)}개")

    # 비디오 윈도우([start, end])와 겹치는 segment만 transcribe
    video_start = float(video_cfg.get("start", 0.0))
    video_end = float(video_cfg.get("end")) if video_cfg.get("end") is not None else None
    relevant: list[SpeechSegment] = []
    for s in segments:
        v_start = s.start_s + audio_offset
        v_end = s.end_s + audio_offset
        if v_end < video_start:
            continue
        if video_end is not None and v_start > video_end:
            continue
        relevant.append(s)
    w(f"  → 비디오 윈도우 내 발화 {len(relevant)}개 (transcribe 대상)")

    # 6) DB 세션 생성
    init_db()
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    description = out_cfg.get("description", "")
    session_id = save_session(
        started_at=started_at,
        description=description,
        game=game_profile.get("name", game_name),
        video_file=video_cfg["file"],
        audio_file=audio_cfg["file"],
        audio_offset_s=audio_offset,
        vision_model=vision_cfg["model"],
        stt_model=stt_cfg["model"],
        lookback_s=lookback_s,
    )

    w(_divider("="))
    w(f"  시작 시각  : {started_at}")
    if description: w(f"  설명       : {description}")
    w(f"  게임       : {game_profile.get('name', game_name)}")
    w(f"  vision     : {vision_cfg['model']}")
    w(f"  stt        : {stt_cfg['model']}")
    w(f"  chat       : {chat_model}  (batch={batch_size}, share_bots={share_bots})")
    w(f"  weights    : {weights}")
    w(f"  lookback   : {lookback_s:.1f}s")
    w(_divider("="))

    # 7) 모든 발화 transcribe (라운드 처리에 앞서 한 번에)
    w("\n[STT — 전체 발화 transcribe]")
    transcripts: list[Transcript] = []
    for i, seg in enumerate(relevant, start=1):
        v_start = seg.start_s + audio_offset
        v_end = seg.end_s + audio_offset
        wav_bytes = _pcm_to_wav_bytes(seg.pcm, sample_rate)
        text, elapsed = transcriber.transcribe(seg.pcm, sample_rate)
        text = (text or "").strip()
        tx = Transcript(
            seg_num=i,
            video_start_s=v_start,
            video_end_s=v_end,
            text=text,
            elapsed=elapsed,
            audio_wav=wav_bytes,
        )
        tx.db_id = save_transcript(
            session_id=session_id,
            seg_num=tx.seg_num,
            video_start_s=tx.video_start_s,
            video_end_s=tx.video_end_s,
            audio_wav=tx.audio_wav,
            transcript=tx.text,
            elapsed_s=tx.elapsed,
        )
        transcripts.append(tx)
        display = text if text else "(빈 응답)"
        w(f"  #{i:02d} [{v_start:6.1f}s → {v_end:6.1f}s {v_end-v_start:.1f}s] "
          f"({elapsed:.2f}s)  {display}")

    # 8) 각 비디오 프레임에 대해 vision 호출 (화면만 본다 — 발화 미주입)
    w(f"\n[Vision 라운드 — 화면만 분석 (발화 미주입)]")
    vision_elapseds: list[float] = []
    last_location = ""
    chat_elapseds_by_weight: dict[str, list[float]] = {wt: [] for wt in weights}
    chat_history: list[dict] = []  # 모든 가중치 공통 (off 가중치도 같은 히스토리 본다)

    for round_num, (ts, image_bytes) in enumerate(frames, start=1):
        # lookback 윈도우 안에 끝나는 발화 (chat 단계에서만 사용)
        ctx = [
            t for t in transcripts
            if t.video_end_s <= ts + 0.001
            and t.video_end_s >= ts - lookback_s
            and t.text and len(t.text) >= min_chars
        ]

        w(f"\n  ── 라운드 {round_num}/{len(frames)} @ {ts:6.1f}s "
          f"(이미지 {len(image_bytes):,}B, 직전 {lookback_s:.0f}s 발화 {len(ctx)}개)")
        if ctx:
            for t in ctx:
                w(f"     · #{t.seg_num} [{t.video_start_s:6.1f}s] {t.text}")

        # vision 호출 (화면만)
        t0 = time.perf_counter()
        vision_text = gemini_client.analyze_screenshot(
            image_bytes,
            game_profile_context=vision_profile_context,
            previous_location=last_location,
        )
        v_elapsed = time.perf_counter() - t0
        vision_elapseds.append(v_elapsed)

        # 위치 추적
        for line in vision_text.splitlines():
            if line.startswith("위치:"):
                loc = line[len("위치:"):].strip()
                if loc and loc != "알 수 없음":
                    last_location = loc
                break

        w(f"     vision ({v_elapsed:.2f}s):")
        for line in vision_text.splitlines():
            w(f"       {line}")

        round_id = save_round(
            session_id=session_id,
            round_num=round_num,
            video_ts_s=ts,
            image_bytes=image_bytes,
            vision_text=vision_text,
            vision_elapsed_s=v_elapsed,
            transcript_ids=[t.db_id for t in ctx],
        )

        # chat 단계 — 가중치별로 반복
        # 봇 샘플은 share_bots=true 면 가중치 전체가 동일, false 면 가중치별 새로 뽑기
        sample_size = min(batch_size, len(bot_pool))
        shared_batch = random.sample(bot_pool, sample_size) if share_bots else None

        per_round_chats_for_history: list[dict] = []
        for wt in weights:
            batch = shared_batch if shared_batch is not None \
                else random.sample(bot_pool, sample_size)

            prompt = _build_chat_prompt(
                weight=wt,
                game_profile_context=chat_profile_context,
                game_context=vision_text,
                chat_history=chat_history,
                bots=batch,
                recent_speech=ctx,
                current_ts=ts,
            )
            chats, raw, c_elapsed = _call_chat(gemini_client, chat_model, prompt)
            chat_elapseds_by_weight[wt].append(c_elapsed)

            save_chat_call(
                round_id=round_id,
                weight=wt,
                bots_csv=",".join(b["name"] for b in batch),
                elapsed_s=c_elapsed,
                raw_response=raw,
                messages=chats,
            )

            w(f"     chat[{wt:>6}] ({c_elapsed:.2f}s) {len(chats)}개:")
            for c in chats:
                w(f"       {c['name']}: {c['message']}")

            # 마지막 가중치(또는 첫 가중치)의 결과를 다음 라운드 chat_history에 반영
            # 비교 일관성을 위해 첫 weight의 결과만 히스토리로 사용
            if wt == weights[0]:
                per_round_chats_for_history = chats

        chat_history.extend(per_round_chats_for_history)
        ctx_window = 10
        if len(chat_history) > ctx_window:
            chat_history = chat_history[-ctx_window:]

    # 9) 요약
    w(f"\n{_divider('=')}")
    w("  요약")
    w(_divider("="))
    w(f"  vision 라운드     : {len(frames)}개")
    if vision_elapseds:
        w(f"  vision 평균 응답  : {sum(vision_elapseds)/len(vision_elapseds):.2f}s")
    w(f"  transcript        : {len(transcripts)}개")
    stt_times = [t.elapsed for t in transcripts]
    if stt_times:
        w(f"  STT 평균 응답     : {sum(stt_times)/len(stt_times):.2f}s")
    for wt, elaps in chat_elapseds_by_weight.items():
        if elaps:
            w(f"  chat[{wt:>6}] 평균 : {sum(elaps)/len(elaps):.2f}s "
              f"({len(elaps)}회 호출)")
    w(_divider("="))

    # 10) HTML 리포트
    if html_path:
        html_content = build_html([session_id])
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_content, encoding="utf-8")
        w(f"\n리포트: {html_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=str(ROOT / "tests" / "integration" / "config.yaml"),
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        test_cfg = yaml.safe_load(f)

    app_config = load_config()

    out_cfg = test_cfg.get("output", {})
    output_path = ROOT / out_cfg.get("file", "tests/integration/result.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoding = out_cfg.get("encoding", "utf-8")
    append = out_cfg.get("append", False)
    mode = "a" if append else "w"

    txt_file = out_cfg.get("file", "tests/integration/result.txt")
    html_path = ROOT / Path(txt_file).with_suffix(".html")

    print(f"결과 저장: {output_path}  ({'추가' if append else '덮어쓰기'})")

    with open(output_path, mode, encoding=encoding) as out:
        if append and output_path.stat().st_size > 0:
            out.write("\n" + "=" * 70 + "\n")
        run(test_cfg, app_config, out, html_path=html_path)

    print("완료.")


if __name__ == "__main__":
    main()
