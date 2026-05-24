"""
STT 테스트 러너.
tests/stt/config.yaml 의 설정을 읽어 실행하고 결과를 텍스트/HTML로 저장한다.

실행:
  python tests/stt/run.py
  python tests/stt/run.py --config tests/stt/config.yaml
"""
from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import yaml

from src.utils.config_loader import load_config, get_api_key
from tests.stt.db import init_db, save_session, save_segment, save_result
from tests.stt.report import build_html
from tests.stt.transcriber import GeminiTranscriber, _pcm_to_wav_bytes
from tests.stt.vad_util import SileroVAD, SpeechSegment


# ── 입력 로드 ────────────────────────────────────────────────────────────────

def _load_file_audio(path: Path, target_sr: int) -> tuple[np.ndarray, int]:
    """오디오 파일을 mono float32, target_sr로 변환."""
    import soundfile as sf

    pcm, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1).astype(np.float32)
    if sr != target_sr:
        pcm = _resample(pcm, sr, target_sr)
        sr = target_sr
    return pcm.astype(np.float32), sr


def _resample(pcm: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """간단한 선형 보간 리샘플 (외부 의존성 없이)."""
    if src_sr == dst_sr:
        return pcm
    ratio = dst_sr / src_sr
    out_len = int(round(len(pcm) * ratio))
    xp = np.linspace(0, len(pcm) - 1, num=len(pcm), dtype=np.float64)
    x = np.linspace(0, len(pcm) - 1, num=out_len, dtype=np.float64)
    return np.interp(x, xp, pcm).astype(np.float32)


def _record_mic(duration_seconds: float, sample_rate: int, device: str | int | None = None) -> np.ndarray:
    """마이크에서 duration_seconds 동안 녹음. 카운트다운 + 입력 레벨 표시."""
    import sounddevice as sd
    import time

    sd_device = None if (device is None or device == "default") else device
    if sd_device is not None:
        try:
            info = sd.query_devices(sd_device, "input")
            print(f"  ▶ 입력 장치: [{sd_device}] {info['name']}")
        except Exception as e:
            print(f"  ▶ device='{device}' 조회 실패, 시스템 기본값 사용 ({e})")
            sd_device = None
    else:
        default_idx = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
        try:
            info = sd.query_devices(default_idx, "input")
            print(f"  ▶ 입력 장치: [기본 {default_idx}] {info['name']}")
        except Exception:
            print(f"  ▶ 입력 장치: 시스템 기본")

    total_frames = int(duration_seconds * sample_rate)
    print(f"  ▶ 마이크 녹음 시작 ({duration_seconds:.0f}s) — 지금 말하세요...")

    stream = sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=sd_device,
    )
    chunks: list[np.ndarray] = []
    captured = 0
    chunk_frames = sample_rate // 5  # 200ms 단위로 표시

    with stream:
        t0 = time.perf_counter()
        while captured < total_frames:
            want = min(chunk_frames, total_frames - captured)
            data, overflowed = stream.read(want)
            if overflowed:
                print("  (overflow)")
            chunks.append(data.reshape(-1).copy())
            captured += want

            elapsed = time.perf_counter() - t0
            remaining = max(0.0, duration_seconds - elapsed)
            peak = float(np.max(np.abs(data))) if data.size else 0.0
            bar_len = int(min(peak, 1.0) * 30)
            bar = "█" * bar_len + "·" * (30 - bar_len)
            print(f"\r  [{bar}] peak={peak:.2f}  남은 {remaining:4.1f}s", end="", flush=True)

    print()  # 줄바꿈
    pcm = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    print(f"  ▶ 녹음 완료 ({len(pcm)/sample_rate:.1f}s, {len(pcm):,} samples)")
    return pcm


def _extract_audio_from_video(video_path: Path, target_sr: int) -> np.ndarray:
    """비디오에서 오디오 트랙 추출 (ffmpeg 필요)."""
    import subprocess

    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", str(target_sr),
        "-f", "wav", "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True)

    import soundfile as sf
    pcm, sr = sf.read(io.BytesIO(proc.stdout), dtype="float32", always_2d=False)
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1).astype(np.float32)
    return pcm.astype(np.float32)


# ── 메인 실행 ────────────────────────────────────────────────────────────────

def _divider(char: str = "-", width: int = 60) -> str:
    return char * width


def run(test_cfg: dict, app_config: dict, out: io.TextIOWrapper, html_path: Path | None = None):
    def w(line: str = ""):
        out.write(line + "\n")
        out.flush()

    input_cfg = test_cfg["input"]
    vad_cfg = test_cfg["vad"]
    stt_cfg = test_cfg["stt"]
    out_cfg = test_cfg.get("output", {})

    mode = input_cfg["mode"]
    sample_rate = vad_cfg["sample_rate"]

    # 1) 입력 로드
    if mode == "file":
        path = ROOT / input_cfg["file"]
        if not path.exists():
            w(f"[오류] 입력 파일이 없습니다: {path}")
            return
        w(f"오디오 파일 로드: {path}")
        pcm, sr = _load_file_audio(path, sample_rate)
        source = f"file:{input_cfg['file']}"
    elif mode == "mic":
        pcm = _record_mic(
            input_cfg["duration_seconds"],
            sample_rate,
            device=input_cfg.get("device"),
        )
        sr = sample_rate
        source = "mic"
    elif mode == "video_audio":
        path = ROOT / input_cfg["video"]
        if not path.exists():
            w(f"[오류] 영상 파일이 없습니다: {path}")
            return
        w(f"비디오에서 오디오 추출: {path}")
        pcm = _extract_audio_from_video(path, sample_rate)
        sr = sample_rate
        source = f"video_audio:{input_cfg['video']}"
    else:
        w(f"[오류] 알 수 없는 모드: {mode}")
        return

    total_duration = len(pcm) / sr
    w(f"오디오: {total_duration:.1f}s · {sr}Hz · {len(pcm):,} samples")

    # 2) VAD
    w("\nVAD 발화 구간 검출 중...")
    vad = SileroVAD(
        threshold=vad_cfg["threshold"],
        sample_rate=sample_rate,
        min_speech_ms=vad_cfg["min_speech_ms"],
        min_silence_ms=vad_cfg["min_silence_ms"],
        speech_pad_ms=vad_cfg.get("speech_pad_ms", 100),
    )
    segments: list[SpeechSegment] = vad.segments(pcm)
    w(f"발화 segment {len(segments)}개")

    # 3) DB 세션 생성
    init_db()
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    description = out_cfg.get("description", "")
    session_id = save_session(
        started_at=started_at,
        description=description,
        source=source,
        stt_model=stt_cfg["model"],
        language=stt_cfg["language"],
        vad_threshold=vad_cfg["threshold"],
        sample_rate=sample_rate,
        total_duration_s=total_duration,
    )

    w(_divider("="))
    w(f"  시작 시각  : {started_at}")
    if description:
        w(f"  설명       : {description}")
    w(f"  모드       : {mode}")
    w(f"  소스       : {source}")
    w(f"  STT 모델   : {stt_cfg['model']}")
    w(f"  언어       : {stt_cfg['language']}")
    w(_divider("="))

    if not segments:
        w("\n발화가 감지되지 않았습니다. VAD threshold나 입력 음성을 확인해 주세요.")
        return

    # 4) Transcribe
    api_key = get_api_key(app_config)
    transcriber = GeminiTranscriber(
        api_key=api_key,
        model=stt_cfg["model"],
        language=stt_cfg["language"],
        prompt=stt_cfg.get("prompt"),
    )

    elapseds: list[float] = []

    for i, seg in enumerate(segments, start=1):
        w(f"\n[segment {i}/{len(segments)}] {seg.start_s:.2f}s → {seg.end_s:.2f}s "
          f"({seg.duration_s:.2f}s)")

        wav_bytes = _pcm_to_wav_bytes(seg.pcm, sample_rate)
        segment_id = save_segment(
            session_id=session_id,
            seg_num=i,
            start_s=seg.start_s,
            end_s=seg.end_s,
            audio_wav=wav_bytes,
        )

        text, elapsed = transcriber.transcribe(seg.pcm, sample_rate)
        elapseds.append(elapsed)
        save_result(segment_id, text, elapsed)

        display = text if text else "(빈 응답)"
        w(f"  → {display}")
        w(f"  ({elapsed:.2f}s)")

    # 5) 요약
    if elapseds:
        w(f"\n{_divider('=')}")
        w("  요약")
        w(_divider("="))
        w(f"  segment 수        : {len(segments)}")
        w(f"  평균 STT 응답시간 : {sum(elapseds)/len(elapseds):.2f}s")
        w(f"  전체 발화 시간    : {sum(s.duration_s for s in segments):.2f}s "
          f"({sum(s.duration_s for s in segments)/total_duration*100:.0f}% of {total_duration:.1f}s)")
        w(_divider("="))

    # 6) HTML 리포트
    if html_path:
        html_content = build_html([session_id])
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_content, encoding="utf-8")
        w(f"\n리포트: {html_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=str(ROOT / "tests" / "stt" / "config.yaml"),
        help="테스트 설정 파일 경로",
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        test_cfg = yaml.safe_load(f)

    app_config = load_config()

    out_cfg = test_cfg.get("output", {})
    output_path = ROOT / out_cfg.get("file", "tests/stt/result.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoding = out_cfg.get("encoding", "utf-8")
    append = out_cfg.get("append", False)
    mode = "a" if append else "w"

    txt_file = out_cfg.get("file", "tests/stt/result.txt")
    html_path = ROOT / Path(txt_file).with_suffix(".html")

    print(f"결과 저장: {output_path}  ({'추가' if append else '덮어쓰기'})")

    with open(output_path, mode, encoding=encoding) as out:
        if append and output_path.stat().st_size > 0:
            out.write("\n" + "=" * 60 + "\n")
        run(test_cfg, app_config, out, html_path=html_path)

    print("완료.")


if __name__ == "__main__":
    main()
