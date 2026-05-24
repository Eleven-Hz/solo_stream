"""STT 실시간 파이프라인.

마이크에서 PCM을 받아 silero-vad로 발화 시작/끝을 감지하고,
발화가 끝나면 누적된 PCM을 GeminiTranscriber 로 transcribe 한 뒤
콜백(또는 큐 push)을 호출한다.

silero-vad는 16kHz에서 512샘플(32ms) 윈도우 단위로 확률을 계산한다.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from src.stt.transcriber import GeminiTranscriber

logger = logging.getLogger("solo_stream.stt.pipeline")

# silero-vad 입력 윈도우 크기 (16kHz 기준 32ms)
_VAD_WINDOW_SAMPLES = 512


@dataclass
class UtteranceEvent:
    """파이프라인이 큐로 push 하는 단위."""
    timestamp: float       # 발화 끝난 시각 (time.time())
    text: str              # transcript
    duration_s: float      # 발화 길이
    elapsed_s: float       # transcribe 소요 시간


class STTPipeline:
    def __init__(
        self,
        transcriber: GeminiTranscriber,
        sample_rate: int = 16000,
        device: str | int | None = None,
        threshold: float = 0.5,
        min_speech_ms: int = 250,
        min_silence_ms: int = 500,
        speech_pad_ms: int = 100,
        max_utterance_s: float = 30.0,
        min_transcript_chars: int = 1,
    ):
        from silero_vad import load_silero_vad

        self._transcriber = transcriber
        self._sample_rate = sample_rate
        self._device = None if (device is None or device == "default") else device
        self._threshold = threshold
        self._min_speech_samples = int(min_speech_ms * sample_rate / 1000)
        self._min_silence_samples = int(min_silence_ms * sample_rate / 1000)
        self._pad_samples = int(speech_pad_ms * sample_rate / 1000)
        self._max_utterance_samples = int(max_utterance_s * sample_rate)
        self._min_transcript_chars = min_transcript_chars

        self._vad_model = load_silero_vad()

        self._stop_event = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._transcribe_thread: threading.Thread | None = None
        self._utt_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=8)
        self._on_event: Callable[[UtteranceEvent], None] | None = None

    def start(self, on_event: Callable[[UtteranceEvent], None]) -> None:
        if self._capture_thread is not None:
            logger.warning("이미 실행 중인 STTPipeline.start() 무시")
            return
        self._on_event = on_event
        self._stop_event.clear()
        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="STT-Capture", daemon=True,
        )
        self._transcribe_thread = threading.Thread(
            target=self._transcribe_loop, name="STT-Transcribe", daemon=True,
        )
        self._capture_thread.start()
        self._transcribe_thread.start()
        logger.info("STTPipeline 시작")

    def stop(self) -> None:
        self._stop_event.set()
        if self._capture_thread:
            self._capture_thread.join(timeout=2)
            self._capture_thread = None
        # transcribe thread는 큐가 빌 때까지 처리 후 종료
        self._utt_queue.put(None)  # type: ignore[arg-type]  sentinel
        if self._transcribe_thread:
            self._transcribe_thread.join(timeout=10)
            self._transcribe_thread = None
        logger.info("STTPipeline 정지")

    # ── 캡처 + VAD ───────────────────────────────────────────────────────

    def _capture_loop(self) -> None:
        import sounddevice as sd

        sr = self._sample_rate
        # InputStream을 _VAD_WINDOW_SAMPLES 의 배수로 읽으면 청크 누적 단순화
        chunk = _VAD_WINDOW_SAMPLES

        # 상태
        speaking = False
        speech_buf: list[np.ndarray] = []
        silence_after_speech_samples = 0
        # 발화 시작 직전 패딩(짧은 링버퍼)
        pad_ring: list[np.ndarray] = []
        pad_ring_samples = 0

        try:
            stream = sd.InputStream(
                samplerate=sr,
                channels=1,
                dtype="float32",
                device=self._device,
                blocksize=chunk,
            )
        except Exception as e:
            logger.error(f"마이크 InputStream 생성 실패: {e}")
            return

        try:
            info = sd.query_devices(self._device if self._device is not None else None, "input")
            logger.info(f"마이크 캡처 시작: {info['name']} ({sr}Hz)")
        except Exception:
            logger.info(f"마이크 캡처 시작 (sr={sr}Hz)")

        with stream:
            while not self._stop_event.is_set():
                try:
                    data, overflowed = stream.read(chunk)
                except Exception as e:
                    logger.error(f"마이크 read 오류: {e}")
                    time.sleep(0.1)
                    continue
                if overflowed:
                    logger.debug("마이크 입력 overflow")

                pcm = data.reshape(-1).astype(np.float32, copy=False)
                prob = self._vad_prob(pcm)
                is_speech = prob >= self._threshold

                if not speaking:
                    # 패딩 링버퍼 유지
                    pad_ring.append(pcm.copy())
                    pad_ring_samples += len(pcm)
                    while pad_ring_samples > self._pad_samples and len(pad_ring) > 1:
                        pad_ring_samples -= len(pad_ring[0])
                        pad_ring.pop(0)

                    if is_speech:
                        speaking = True
                        # 패딩 + 현재 청크로 발화 시작
                        speech_buf = list(pad_ring)
                        pad_ring = []
                        pad_ring_samples = 0
                        silence_after_speech_samples = 0
                else:
                    speech_buf.append(pcm.copy())
                    if is_speech:
                        silence_after_speech_samples = 0
                    else:
                        silence_after_speech_samples += len(pcm)

                    total_samples = sum(len(c) for c in speech_buf)

                    # 종료 조건: 충분히 긴 침묵 또는 최대 발화 길이 도달
                    if (silence_after_speech_samples >= self._min_silence_samples
                            or total_samples >= self._max_utterance_samples):
                        # 너무 짧은 발화는 버림
                        if total_samples >= self._min_speech_samples:
                            utterance = np.concatenate(speech_buf)
                            try:
                                self._utt_queue.put(utterance, timeout=0.5)
                            except queue.Full:
                                logger.warning("transcribe 큐 가득 — 발화 1개 드랍")
                        speaking = False
                        speech_buf = []
                        silence_after_speech_samples = 0

        logger.info("마이크 캡처 종료")

    def _vad_prob(self, pcm: np.ndarray) -> float:
        import torch
        # 정확히 _VAD_WINDOW_SAMPLES 만큼만 모델에 줘야 함
        if len(pcm) != _VAD_WINDOW_SAMPLES:
            if len(pcm) > _VAD_WINDOW_SAMPLES:
                pcm = pcm[:_VAD_WINDOW_SAMPLES]
            else:
                pcm = np.pad(pcm, (0, _VAD_WINDOW_SAMPLES - len(pcm)))
        tensor = torch.from_numpy(pcm)
        with torch.no_grad():
            return float(self._vad_model(tensor, self._sample_rate))

    # ── 별도 스레드에서 transcribe ───────────────────────────────────────

    def _transcribe_loop(self) -> None:
        while True:
            item = self._utt_queue.get()
            if item is None:
                # 정지 sentinel
                return
            try:
                duration_s = len(item) / self._sample_rate
                text, elapsed = self._transcriber.transcribe(item, self._sample_rate)
                if len(text) < self._min_transcript_chars:
                    logger.debug(f"빈/짧은 transcript 무시: '{text}'")
                    continue
                event = UtteranceEvent(
                    timestamp=time.time(),
                    text=text,
                    duration_s=duration_s,
                    elapsed_s=elapsed,
                )
                logger.info(f"[STT] ({duration_s:.1f}s, {elapsed:.2f}s) {text}")
                if self._on_event:
                    try:
                        self._on_event(event)
                    except Exception as e:
                        logger.error(f"on_event 콜백 실패: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"transcribe 처리 오류: {e}", exc_info=True)
