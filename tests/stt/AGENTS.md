<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# stt

## Purpose
STT (silero-vad + Gemini Audio) 평가 러너. WAV 파일 입력에서 VAD로 segment를 뽑고 각 segment를 transcribe해 정확도·지연·VAD 파라미터 튜닝 결과를 HTML로 비교한다.

## Key Files
| File | Description |
|------|-------------|
| `run.py` | CLI 엔트리포인트. WAV → resample(16kHz mono float32) → SileroVAD → transcribe → DB+HTML |
| `config.yaml` | 입력 WAV 경로, VAD 파라미터 후보, Gemini 모델 후보 |
| `vad_util.py` | SileroVAD 래퍼 (segment 단위 — `src/stt/vad.py`와 거의 동일, 평가용 fork) |
| `transcriber.py` | GeminiTranscriber + `_pcm_to_wav_bytes` (평가용 fork — `src/stt/transcriber.py`와 거의 동일) |
| `db.py` / `report.py` | SQLite 스키마 + HTML 빌더 |
| `list_devices.py` | sounddevice 입력 장치 목록 (마이크 device 인덱스 확인용) |
| `smoke_main_process.py` | `src.main.stt_process`를 단독으로 띄워 마이크에서 stt_queue로 발화가 흐르는지 확인 |
| `audio/sample01.wav`, `audio/synthetic.wav`, `audio/crosscode_audio_01.wav` | 샘플 입력 (gitignored 일부) |
| `results.db`, `result.html`, `result.txt` | 산출물 (gitignored) |

## For AI Agents

### Working In This Directory
- `vad_util.py`, `transcriber.py`가 `src/stt/`의 동명 모듈과 거의 같다 — 둘 다 손볼 일이 있으면 평가 fork는 평가 시나리오 특화(예: 디버그 출력)로 유지하고 production 코드는 `src/stt/`만 수정.
- 새 transcribe 프롬프트 변형 비교가 목적이면 `config.yaml`의 `prompt:` 항목을 배열로 두고 run.py에서 루프.
- `list_devices.py` 결과를 보고 `config.yaml`의 `device: <index>` 를 설정 — 잘못된 장치는 무음 캡처로 이어진다.

### Testing Requirements
- VAD 파라미터 튜닝 시 동일 입력에 대해 `threshold`/`min_speech_ms`/`min_silence_ms`/`speech_pad_ms`만 바꿔 비교.
- transcribe 정확도는 사람이 HTML을 읽고 판정 (자동 WER 계산은 없음).

### Common Patterns
- WAV가 16kHz가 아니면 `_resample(pcm, src_sr, dst_sr)` 으로 보정 (run.py 내부).
- 발화 segment 직렬화: pcm을 numpy 배열로 들고 다니다가 transcribe 직전에만 WAV bytes로 변환.

## Dependencies

### Internal
- `src.utils.config_loader` (`load_config`, `get_api_key`)

### External
- `silero-vad`, `torch`, `numpy`, `soundfile`, `sounddevice`, `google.genai`, `PyYAML`

<!-- MANUAL: -->
