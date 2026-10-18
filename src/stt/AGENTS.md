<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# stt

## Purpose
실시간 마이크 음성 인식 파이프라인 (Phase 2). silero-vad로 발화 시작/끝을 감지해 누적된 PCM을 Gemini Audio로 transcribe하고, `UtteranceEvent`를 `stt_queue`로 push해 AI 프로세스가 다음 vision 라운드의 lookback 구간에서 채팅 컨텍스트로 합친다.

## Key Files
| File | Description |
|------|-------------|
| `pipeline.py` | `STTPipeline` — 캡처 스레드(`_capture_loop`, 32ms 윈도우 단위 sounddevice InputStream + VAD 확률 계산 + 발화 패딩 ring-buffer + min_silence_ms로 종료 판정) + transcribe 스레드(`_transcribe_loop`, 큐 consumer + 결과를 `on_event` 콜백으로). `UtteranceEvent`: `timestamp/text/duration_s/elapsed_s`. |
| `transcriber.py` | `GeminiTranscriber.transcribe(pcm, sample_rate)` — float32 PCM → 16-bit WAV bytes(`pcm_to_wav_bytes`) → Gemini `generate_content` (audio Part + 받아쓰기 프롬프트, `thinking_budget=0`). 429 재시도 포함. 두 transcriber 모두 `set_prompt()` 로 힌트 교체 — `stt_process` 가 게임 프로필 고유명사(`src/chat/flow.stt_hint_terms`)를 붙여 만들고 `game_name` 설정이 오면 다시 만든다(실측: "가을에 몰락"→"가을의 몰락", "초보자 안고"→"초보자 항구"). |
| `vad.py` | `SileroVAD` — silero_vad의 `get_speech_timestamps` 래퍼. **현재 pipeline.py에서는 직접 사용하지 않고** `load_silero_vad` + 윈도우별 직접 호출 패턴 사용. 이 모듈은 단위 평가용(`tests/stt/`)에서 segment 단위로 쓰임. |
| `__init__.py` | `STTPipeline`, `UtteranceEvent` re-export |

## For AI Agents

### Working In This Directory
- silero-vad는 16kHz 기준 **정확히 512샘플(32ms)** 윈도우만 받는다 (`_VAD_WINDOW_SAMPLES`) — `_vad_prob`이 길이 보정.
- `_capture_loop`의 패딩 ring-buffer는 발화 시작 직전 `speech_pad_ms` 만큼 음성을 함께 보존해 단어 머리가 잘리지 않게 한다.
- 발화 종료 조건은 (`min_silence_ms` 만큼 무음) **또는** (`max_utterance_seconds` 도달) 둘 중 하나. 너무 짧으면 (`min_speech_ms` 미만) 폐기.
- transcribe 결과가 `min_transcript_chars` 미만이면 폐기 — Gemini가 "(웃음)" 같은 메타 텍스트만 뱉는 경우 필터.
- pipeline.py가 import-time에 `from src.stt.transcriber import GeminiTranscriber`를 한다 — `src.stt.__init__`에서 pipeline을 import하므로 `GeminiTranscriber`만 따로 쓰고 싶을 때는 `from src.stt.transcriber import GeminiTranscriber`로 직접 import.

### Testing Requirements
- `tests/stt/run.py` — WAV 파일을 입력으로 VAD segmentation + transcribe 정확도 평가.
- `tests/integration/run.py` — STT + vision 라운드의 lookback 매칭 검증.
- `tests/stt/tools/list_devices.py` — sounddevice 장치 인덱스 확인 (마이크 device 설정용).

### Common Patterns
- `STTPipeline.start(on_event)`로 두 스레드 기동, `stop()`으로 큐 sentinel(`None`) 전달 후 join.
- `src/main.py`의 `stt_process`가 `chat_active` 토글에 따라 `pipeline.start/stop`을 호출 — 시작/정지 버튼이 STT까지 함께 제어한다.

## Dependencies

### Internal
- `src/main.py`의 `stt_process`가 인스턴스화 + `stt_queue`로 결과 전달.
- AI 프로세스가 vision 라운드 시작/완료 시점에 `stt_queue`를 drain해 `lookback_seconds` 안의 발화만 사용.

### External
- `silero-vad` — `load_silero_vad`, `get_speech_timestamps`
- `torch` — silero-vad 추론 (CPU)
- `sounddevice` — 마이크 InputStream (PortAudio)
- `soundfile` — float32 PCM → WAV 인메모리 인코딩
- `google.genai` — Gemini Audio transcription

<!-- MANUAL: -->
