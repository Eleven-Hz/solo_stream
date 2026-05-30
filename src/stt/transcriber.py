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


def normalize_pcm(
    pcm: np.ndarray,
    target_level: float = 0.1,
    percentile: float = 95.0,
    max_gain: float = 20.0,
    silence_floor: float = 1e-4,
) -> np.ndarray:
    """발화 PCM을 퍼센타일 기준으로 정규화한다 (transient에 강건, boost-only).

    peak 대신 |pcm| 의 percentile(기본 95) 값을 기준 레벨로 삼아 target_level 로
    끌어올린다. 게임 효과음·기침 같은 한 번의 큰 소리가 peak 를 좌우해도, 퍼센타일
    기준이라 발화 본체 레벨이 흔들리지 않는다. 큰 transient 는 클리핑되지만 Gemini 는
    발화에 집중한다.

    - 기준 레벨이 이미 target_level 이상이면(정상 음량) 건드리지 않는다(boost-only).
    - 기준 레벨이 silence_floor 미만이면 사실상 무음이므로 증폭하지 않는다
      (증폭하면 배경 노이즈만 키운다).
    - 과증폭을 막기 위해 게인을 max_gain 으로 상한 처리한다.
    - target_level <= 0 이면 정규화를 비활성화한다.
    """
    if target_level <= 0:
        return pcm
    if pcm.dtype != np.float32:
        pcm = pcm.astype(np.float32)
    if pcm.size == 0:
        return pcm
    ref = float(np.percentile(np.abs(pcm), percentile))
    if ref < silence_floor:
        return pcm
    gain = min(target_level / ref, max_gain)
    if gain <= 1.0:
        return pcm
    return np.clip(pcm * gain, -1.0, 1.0).astype(np.float32)


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
        normalize_target_level: float = 0.1,
        normalize_percentile: float = 95.0,
        normalize_max_gain: float = 20.0,
    ):
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._language = language
        self._extra_prompt = prompt or ""
        self._normalize_target_level = normalize_target_level
        self._normalize_percentile = normalize_percentile
        self._normalize_max_gain = normalize_max_gain
        logger.info(
            f"GeminiTranscriber 준비: model={model} language={language} "
            f"normalize_level={normalize_target_level} p{normalize_percentile:.0f} "
            f"max_gain={normalize_max_gain}"
        )

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

        pcm = normalize_pcm(
            pcm,
            target_level=self._normalize_target_level,
            percentile=self._normalize_percentile,
            max_gain=self._normalize_max_gain,
        )
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


# OpenAI gpt-4o-transcribe / mini 는 chat 스타일 프롬프트가 아니라 'prompt' 파라미터로
# 도메인 힌트만 받는다. 받아쓰기 규칙은 모델 기본 동작에 맡긴다.
_OPENAI_DEFAULT_PROMPT = "한국어 음성입니다. 짧은 감탄사(아, 어, 음, 와 등)도 그대로 받아 적으세요."


class OpenAITranscriber:
    """OpenAI Audio Transcriptions 기반 transcriber.

    GeminiTranscriber 와 동일하게 transcribe(pcm, sr) -> (text, elapsed) 를 제공한다.
    모델 후보: gpt-4o-transcribe / gpt-4o-mini-transcribe / whisper-1.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini-transcribe",
        language: str = "ko",
        prompt: str | None = None,
        normalize_target_level: float = 0.1,
        normalize_percentile: float = 95.0,
        normalize_max_gain: float = 20.0,
    ):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._language = language
        self._prompt = (prompt or _OPENAI_DEFAULT_PROMPT).strip()
        self._normalize_target_level = normalize_target_level
        self._normalize_percentile = normalize_percentile
        self._normalize_max_gain = normalize_max_gain
        logger.info(
            f"OpenAITranscriber 준비: model={model} language={language} "
            f"normalize_level={normalize_target_level} p{normalize_percentile:.0f} "
            f"max_gain={normalize_max_gain}"
        )

    def transcribe(self, pcm: np.ndarray, sample_rate: int = 16000) -> tuple[str, float]:
        pcm = normalize_pcm(
            pcm,
            target_level=self._normalize_target_level,
            percentile=self._normalize_percentile,
            max_gain=self._normalize_max_gain,
        )
        wav_bytes = pcm_to_wav_bytes(pcm, sample_rate)
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


def create_transcriber(
    provider: str,
    *,
    gemini_api_key: str | None = None,
    openai_api_key: str | None = None,
    model: str = "gemini-2.5-flash",
    language: str = "ko",
    prompt: str | None = None,
    normalize_target_level: float = 0.1,
    normalize_percentile: float = 95.0,
    normalize_max_gain: float = 20.0,
):
    """provider("gemini"|"openai")에 맞는 transcriber를 생성한다.

    두 transcriber 모두 transcribe(pcm, sr) -> (text, elapsed) 인터페이스가 동일하므로
    STTPipeline 입장에서는 교체 가능하다.
    """
    provider = (provider or "gemini").lower()
    if provider == "openai":
        if not openai_api_key:
            raise ValueError(
                "STT provider=openai 인데 OpenAI API 키가 없습니다. "
                ".env.local 에 OPENAI_API_KEY 를 설정하세요."
            )
        return OpenAITranscriber(
            api_key=openai_api_key,
            model=model,
            language=language,
            prompt=prompt,
            normalize_target_level=normalize_target_level,
            normalize_percentile=normalize_percentile,
            normalize_max_gain=normalize_max_gain,
        )
    if not gemini_api_key:
        raise ValueError(
            "STT provider=gemini 인데 Gemini API 키가 없습니다. "
            ".env.local 에 GEMINI_API_KEY 를 설정하세요."
        )
    return GeminiTranscriber(
        api_key=gemini_api_key,
        model=model,
        language=language,
        prompt=prompt,
        normalize_target_level=normalize_target_level,
        normalize_percentile=normalize_percentile,
        normalize_max_gain=normalize_max_gain,
    )
