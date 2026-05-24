"""Gemini Audio 기반 transcriber.
PCM(float32, mono, 16kHz)을 WAV로 인메모리 인코딩하여 Gemini에 전송한다.
"""
from __future__ import annotations

import io
import logging
import re
import time

import numpy as np

logger = logging.getLogger("solo_stream.stt.transcriber")


_TRANSCRIBE_PROMPT_TEMPLATE = """첨부된 음성을 한국어로 그대로 받아쓰기 하세요.

규칙:
- 화자가 실제로 한 발화만 한국어 텍스트로 옮길 것.
- 짧은 감탄사(아, 어, 음, 와 등)도 빠뜨리지 말고 그대로 표기.
- 해설/설명/요약 금지. 텍스트만 출력.
- 발화 내용이 명확하지 않으면 빈 줄로 응답.

{context_hint}"""


def pcm_to_wav_bytes(pcm: np.ndarray, sample_rate: int) -> bytes:
    """float32 PCM(-1..1)을 16-bit PCM WAV bytes로 변환."""
    import soundfile as sf

    if pcm.dtype != np.float32:
        pcm = pcm.astype(np.float32)
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1).astype(np.float32)

    buf = io.BytesIO()
    sf.write(buf, pcm, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class GeminiTranscriber:
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        language: str = "ko",
        prompt: str | None = None,
    ):
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._language = language
        self._extra_prompt = prompt or ""
        logger.info(f"GeminiTranscriber 준비: model={model} language={language}")

    def _build_prompt(self) -> str:
        context_hint = self._extra_prompt.strip()
        return _TRANSCRIBE_PROMPT_TEMPLATE.format(
            context_hint=f"맥락: {context_hint}" if context_hint else "",
        ).strip()

    def _call_with_retry(self, fn, max_retries: int = 3):
        for attempt in range(max_retries):
            try:
                return fn()
            except Exception as e:
                msg = str(e)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    m = re.search(r"retry in (\d+(?:\.\d+)?)s", msg)
                    wait = float(m.group(1)) if m else (2 ** attempt * 2)
                    logger.warning(f"429 quota 초과, {wait:.1f}초 후 재시도 ({attempt+1}/{max_retries})")
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError("최대 재시도 횟수 초과")

    def transcribe(self, pcm: np.ndarray, sample_rate: int = 16000) -> tuple[str, float]:
        from google.genai import types

        wav_bytes = pcm_to_wav_bytes(pcm, sample_rate)
        prompt = self._build_prompt()

        def _call():
            return self._client.models.generate_content(
                model=self._model,
                contents=[
                    types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )

        t0 = time.perf_counter()
        try:
            response = self._call_with_retry(_call)
            text = (response.text or "").strip()
        except Exception as e:
            logger.error(f"transcribe 실패: {e}")
            text = ""
        elapsed = time.perf_counter() - t0
        return text, elapsed
