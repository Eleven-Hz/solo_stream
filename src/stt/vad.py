"""silero-vad 래퍼.
오디오 PCM(float32, mono, 16kHz)을 받아 발화 segment 리스트를 반환한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger("solo_stream.stt.vad")


@dataclass
class SpeechSegment:
    start_s: float
    end_s: float
    pcm: np.ndarray  # float32, mono

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


class SileroVAD:
    def __init__(
        self,
        threshold: float = 0.5,
        sample_rate: int = 16000,
        min_speech_ms: int = 250,
        min_silence_ms: int = 500,
        speech_pad_ms: int = 100,
    ):
        from silero_vad import load_silero_vad

        self._threshold = threshold
        self._sample_rate = sample_rate
        self._min_speech_ms = min_speech_ms
        self._min_silence_ms = min_silence_ms
        self._speech_pad_ms = speech_pad_ms
        self._model = load_silero_vad()
        logger.info(
            f"SileroVAD 로드: threshold={threshold} sr={sample_rate} "
            f"min_speech={min_speech_ms}ms min_silence={min_silence_ms}ms"
        )

    def segments(self, pcm: np.ndarray) -> list[SpeechSegment]:
        import torch
        from silero_vad import get_speech_timestamps

        if pcm.dtype != np.float32:
            pcm = pcm.astype(np.float32)
        if pcm.ndim > 1:
            pcm = pcm.mean(axis=1).astype(np.float32)

        tensor = torch.from_numpy(pcm)
        ts = get_speech_timestamps(
            tensor,
            self._model,
            threshold=self._threshold,
            sampling_rate=self._sample_rate,
            min_speech_duration_ms=self._min_speech_ms,
            min_silence_duration_ms=self._min_silence_ms,
            speech_pad_ms=self._speech_pad_ms,
            return_seconds=False,
        )

        out: list[SpeechSegment] = []
        for entry in ts:
            start = int(entry["start"])
            end = int(entry["end"])
            out.append(
                SpeechSegment(
                    start_s=start / self._sample_rate,
                    end_s=end / self._sample_rate,
                    pcm=pcm[start:end],
                )
            )
        return out
