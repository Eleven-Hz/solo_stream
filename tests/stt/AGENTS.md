<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-30 -->

# stt

## Purpose
STT (silero-vad + Gemini Audio) 평가 러너. WAV 파일 입력에서 VAD로 segment를 뽑고 각 segment를 transcribe해 정확도·지연·VAD 파라미터 튜닝 결과를 HTML로 비교한다.

## Layout (가벼운 분류)
공유 모듈(`transcriber.py`/`openai_transcriber.py`/`vad_util.py`/`db.py`/`report.py`)과 메인 러너(`run.py`/`config.yaml`)는 **루트 유지** — `tests.stt.X` 임포트가 그대로 동작하고 `tests/integration/run.py`도 이 경로를 쓴다. 비교 러너는 `compare/`, 보조 도구는 `tools/`, 산출물은 `output/`(전부 gitignored).

```
tests/stt/
  transcriber.py openai_transcriber.py vad_util.py db.py report.py  # 공유 모듈(루트)
  run.py config.yaml                                                # 메인 러너(루트)
  compare/   compare_providers.py compare_real_quiet.py
             run_normalize_compare.py groundtruth_eval.py
  tools/     list_devices.py smoke_main_process.py
  output/    result*.html result*.txt results.db   (gitignored)
  audio/     *.wav                                  (일부 gitignored)
```

## Key Files
| File | Description |
|------|-------------|
| `run.py` | CLI 엔트리포인트. WAV/mic → resample(16kHz mono float32) → SileroVAD → transcribe → DB+`output/` HTML |
| `config.yaml` | 입력 경로, VAD 파라미터, STT provider/모델. `output.file`은 `tests/stt/output/` 아래 |
| `vad_util.py` | SileroVAD 래퍼 (segment 단위 — `src/stt/vad.py`와 거의 동일, 평가용 fork) |
| `transcriber.py` | GeminiTranscriber + `_normalize_pcm` + `_pcm_to_wav_bytes` (평가용 fork) |
| `openai_transcriber.py` | OpenAITranscriber (gpt-4o-transcribe 계열, GeminiTranscriber와 동일 인터페이스) |
| `db.py` / `report.py` | SQLite 스키마(`output/results.db`) + HTML 빌더 |
| `compare/compare_providers.py` | Gemini vs OpenAI 같은 VAD segment를 나란히 받아쓰기 비교 |
| `compare/groundtruth_eval.py` | 모델 받아쓰기 vs **내가 직접 적은 정답** 비교. 정답 입력 시 글자단위 정확도(1−CER)·diff·모델별 평균을 브라우저에서 실시간 계산, 정답은 localStorage 저장 + JSON export/import |
| `compare/compare_real_quiet.py` | DB의 실제 작은 mic 세션으로 정규화 전/후 비교 |
| `compare/run_normalize_compare.py` | very-quiet 입력에 input_gain+정규화 A/B 비교 러너 |
| `tools/list_devices.py` | sounddevice 입력 장치 목록 (마이크 device 인덱스 확인용) |
| `tools/smoke_main_process.py` | `src.main.stt_process`를 단독으로 띄워 마이크→stt_queue 흐름 확인 |
| `audio/*.wav` | 샘플 입력 (일부 gitignored) |
| `output/` | 산출물 `result*.{html,txt}` / `results.db` (gitignored) |

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
