"""
OpenAI Audio Transcriptions 기반 transcriber (평가용).

GeminiTranscriber 와 동일한 인터페이스(transcribe(pcm, sr) -> (text, elapsed))를 제공해
동일 segment에 대해 두 모델의 받아쓰기 정확도를 나란히 비교할 수 있게 한다.

모델 후보:
  - gpt-4o-transcribe       (정확도 우선)
  - gpt-4o-mini-transcribe  (비용 우선, 50% 저렴)
  - whisper-1               (구형 Whisper)
"""
from __future__ import annotations

import io
import logging
import time

import numpy as np

from tests.stt.transcriber import _normalize_pcm, _pcm_to_wav_bytes

logger = logging.getLogger("solo_stream.test.stt.openai_transcriber")


# gpt-4o-transcribe / mini 는 chat 스타일 프롬프트를 받지 않고 'prompt' 파라미터로
# 도메인 힌트만 받는다. 받아쓰기 규칙은 모델 기본 동작에 맡긴다.
_DEFAULT_PROMPT = "한국어 음성입니다. 짧은 감탄사(아, 어, 음, 와 등)도 그대로 받아 적으세요."


class OpenAITranscriber:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-transcribe",
        language: str = "ko",
        prompt: str | None = None,
        normalize_target_level: float = 0.0,
        normalize_percentile: float = 95.0,
        normalize_max_gain: float = 20.0,
    ):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._language = language
        self._prompt = (prompt or _DEFAULT_PROMPT).strip()
        self._normalize_target_level = normalize_target_level
        self._normalize_percentile = normalize_percentile
        self._normalize_max_gain = normalize_max_gain
        logger.info(
            f"OpenAITranscriber 준비: model={model} language={language} "
            f"normalize_level={normalize_target_level}"
        )

    def transcribe(self, pcm: np.ndarray, sample_rate: int = 16000) -> tuple[str, float]:
        """PCM을 transcribe. (텍스트, 응답시간_초) 반환."""
        pcm = _normalize_pcm(
            pcm,
            target_level=self._normalize_target_level,
            percentile=self._normalize_percentile,
            max_gain=self._normalize_max_gain,
        )
        wav_bytes = _pcm_to_wav_bytes(pcm, sample_rate)

        buf = io.BytesIO(wav_bytes)
        buf.name = "audio.wav"  # SDK가 확장자로 포맷 추론

        t0 = time.perf_counter()
        try:
            resp = self._client.audio.transcriptions.create(
                model=self._model,
                file=buf,
                language=self._language,
                prompt=self._prompt,
                response_format="text",
            )
            text = (resp if isinstance(resp, str) else getattr(resp, "text", "")).strip()
        except Exception as e:
            logger.error(f"transcribe 실패: {e}")
            text = ""
        elapsed = time.perf_counter() - t0
        return text, elapsed
