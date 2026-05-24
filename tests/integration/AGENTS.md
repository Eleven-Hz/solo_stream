<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# integration

## Purpose
Vision + STT + Chat 합성 평가. 영상에서 N초마다 프레임을 뽑아 vision 분석을 돌리고, 별도 WAV(보통 같은 게임 플레이의 음성)에서 VAD/STT로 발화를 추출한 뒤, 각 vision 라운드 직전 `lookback_seconds` 구간에 떨어진 발화만 채팅 프롬프트에 합쳐 호출 — 즉 실서비스의 라운드 흐름을 오프라인으로 재현.

## Key Files
| File | Description |
|------|-------------|
| `run.py` | CLI 엔트리포인트. `Transcript`(seg별 video_start/end/text) → vision 라운드 sampler → 각 라운드의 lookback 윈도우에서 발화 선택 → `_build_bots_info` + `_parse_chats` 직접 호출 → DB+HTML |
| `config.yaml` | 영상/오디오 경로, 라운드 간격, lookback_seconds, 사용 봇 페르소나 수, 모델 선택 |
| `db.py` | SQLite 스키마 (`sessions`, `transcripts`, `rounds`, `chat_calls`) + save helpers |
| `report.py` | HTML — 라운드 단위로 [화면 묘사 / 직전 발화 / 봇 채팅 응답]을 나란히 표시 |
| `results.db`, `result.html`, `result_lookback_10s.html`, `result.txt` | 산출물 (gitignored) |

## For AI Agents

### Working In This Directory
- 이 러너는 `src.ai.model_client._build_bots_info` / `_parse_chats` 같은 **밑줄 prefix 내부 함수**를 직접 import 한다 (`# noqa: PLC2701` 표시). production 함수 시그니처를 바꾸면 여기도 같이 깨진다.
- `lookback_seconds`를 바꿔가며 비교하려면 `config.yaml`에 후보를 두고 run.py가 각 값으로 한 세션씩 돌게 한다 — 결과 HTML 파일명에 `_lookback_<N>s` suffix.
- STT segment의 `video_start_s/end_s`는 영상 시간축이고, vision 라운드의 시각도 같은 영상 시간축 — 두 시간축이 정확히 일치해야 lookback 매칭이 의미 있다.

### Testing Requirements
- 같은 video+audio 입력에 대해 lookback 값만 바꿔가며 비교 → 어떤 길이에서 봇 반응이 가장 자연스러운지 사람이 판정.
- 모델 변경 (Gemini ↔ Claude)도 같은 입력으로 비교 가능.

### Common Patterns
- transcribe는 평가 fork(`tests.stt.transcriber.GeminiTranscriber`) 사용 — `tests/stt/AGENTS.md` 참조.
- 봇 풀은 `src.chat.persona_loader.load_personas("personas")`로 그대로 가져온다.

## Dependencies

### Internal
- `src.ai.model_client.GeminiClient`, `_build_bots_info`, `_parse_chats`
- `src.chat.game_profile_loader.load_profile`, `build_profile_context`, `build_vision_context`
- `src.chat.persona_loader.load_personas`
- `tests.stt.transcriber.GeminiTranscriber`
- `tests.stt.vad_util.SileroVAD, SpeechSegment`

### External
- 영상/오디오 디코딩 lib (`imageio`, `soundfile`), `numpy`, `PyYAML`

<!-- MANUAL: -->
