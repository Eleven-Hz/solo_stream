"""
작은 음성 보정(input_gain + normalize) 효과 A/B 비교 러너.

동일한 very-quiet 입력(sample01_vquiet.wav, peak≈0.0021)을 두 조건으로 처리:
  1) baseline — input_gain=1.0, 정규화 OFF → output/result_baseline.{txt,html}
       (이 레벨에서는 VAD가 발화를 감지하지 못해 발화가 통째로 소실된다)
  2) fixed    — input_gain=8.0(VAD 이전), 정규화 ON → output/result_normalize.{txt,html}
       (캡처-단계 게인이 VAD 감지를 복구하고, 정규화가 transcribe 레벨을 맞춘다)

실행:
  python tests/stt/compare/run_normalize_compare.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.utils.config_loader import load_config
from tests.stt import run as run_mod

INPUT_FILE = "tests/stt/audio/sample01_vquiet.wav"


def _base_cfg(output_file: str, description: str,
              input_gain: float, normalize_level: float) -> dict:
    return {
        "input": {"mode": "file", "file": INPUT_FILE},
        "vad": {
            "threshold": 0.5,
            "min_speech_ms": 250,
            "min_silence_ms": 500,
            "sample_rate": 16000,
            "speech_pad_ms": 100,
        },
        "stt": {
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "language": "ko",
            "prompt": "사용자가 게임을 플레이하며 한국어로 혼잣말/감상을 말합니다. 짧은 감탄사도 그대로 옮겨 적으세요.",
            "input_gain": input_gain,
            "normalize_target_level": normalize_level,
            "normalize_percentile": 95,
            "normalize_max_gain": 20.0,
        },
        "output": {"file": output_file, "description": description, "append": False},
    }


def _run_one(output_file: str, description: str,
             input_gain: float, normalize_level: float) -> None:
    app_config = load_config()
    txt_path = ROOT / output_file
    html_path = ROOT / Path(output_file).with_suffix(".html")
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = _base_cfg(output_file, description, input_gain, normalize_level)
    print(f"\n===== 실행: {description} -> {output_file} =====")
    with open(txt_path, "w", encoding="utf-8") as out:
        run_mod.run(cfg, app_config, out, html_path=html_path)


def main() -> None:
    _run_one(
        "tests/stt/output/result_baseline.txt",
        "보정 OFF — very-quiet 입력 baseline (input_gain=1, normalize OFF)",
        input_gain=1.0,
        normalize_level=0.0,
    )
    _run_one(
        "tests/stt/output/result_normalize.txt",
        "보정 ON — very-quiet 입력 (input_gain=8, normalize target_level=0.1 p95)",
        input_gain=8.0,
        normalize_level=0.1,
    )
    print("\n완료: result_baseline.* / result_normalize.* 생성됨")


if __name__ == "__main__":
    main()
