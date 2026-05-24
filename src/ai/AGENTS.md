<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# ai

## Purpose
**유일한 AI API 접점**. 외부 SDK (`google.genai`, `anthropic`)는 이 디렉토리에서만 import된다. Vision 분석(`analyze_screenshot`)과 봇 채팅 생성(`generate_chats`)을 동일 인터페이스로 노출하고, `config.yaml`의 `ai.vision_provider` / `ai.chat_provider`로 provider(`gemini` | `claude`)를 런타임 교체할 수 있다. 모델 deprecate 주기가 짧다는 전제 하에 한 파일만 수정해 갈아끼울 수 있도록 설계됨.

## Key Files
| File | Description |
|------|-------------|
| `model_client.py` | `GeminiClient`, `ClaudeClient`, `create_vision_client`, `create_chat_client`. Vision 프롬프트 빌더(`_build_vision_prompt`), 채팅 프롬프트 빌더(`_build_bots_info`, `_build_user_speech_section`), JSON 응답 파서(`_parse_chats`, trailing-comma·코드펜스·잡음 텍스트 보수적 cleanup), 429 quota 재시도 로직 포함 |
| `__init__.py` | 패키지 마커 |

## For AI Agents

### Working In This Directory
- 새 provider 추가 시 `GeminiClient`/`ClaudeClient`와 동일한 시그니처(`analyze_screenshot(image_bytes, game_profile_context, previous_location)`, `generate_chats(game_context, bots, chat_history, game_profile_context, user_speech, default_speech_weight)`)를 구현한 후 `create_vision_client`/`create_chat_client`의 분기에 추가.
- 채팅 응답 파싱은 LLM이 자주 망치는 패턴(코드펜스, trailing comma, 앞뒤 설명)을 모두 정규식으로 잡고, 최후의 fallback으로 `{"name":...,"message":...}` 개별 매칭까지 수행 — 새 케이스를 발견하면 `_parse_chats`만 수정한다.
- `ChatCallDebug`는 `ai_process`에서 라운드 블록 로그에 합쳐 출력하는 용도 — `provider`/`model`/`prompt`/`raw_response`/`elapsed_s` 필드 시그니처를 유지할 것.

### Testing Requirements
- `tests/vision/run.py` — Vision 모델 비교 (Gemini/Claude × 영상 프레임 샘플).
- 채팅 생성은 `tests/integration/run.py`에서 vision+STT와 함께 검증.

### Common Patterns
- `_call_with_retry(fn)`는 429/`RESOURCE_EXHAUSTED` 에러 메시지의 `retry in Ns` 를 파싱해 그만큼 대기 후 재시도. exponential fallback 포함.
- Vision 프롬프트는 한국어, 출력 형식은 `묘사:/위치:/상황:/이벤트:` 4줄 고정 — `src/main.py`의 위치 추출 로직이 `위치:` 접두사에 의존한다.
- 채팅 프롬프트의 봇별 라인 형식 (`/ [자기 기억] N개 / [타인 반응] true / [타인 반응 빈도] X% / [스트리머 발화 반응] medium`)을 바꾸면 모델의 답변 일관성이 깨질 수 있으니 신중히.

## Dependencies

### Internal
- `src/chat/persona_loader.normalize_user_speech_weight` — `off/low/medium/high` 정규화 (YAML이 `off`를 `False`로 파싱하는 함정 대비)

### External
- `google.genai` — Gemini SDK (`types.Part.from_bytes`, `types.GenerateContentConfig`, `types.ThinkingConfig`)
- `anthropic` — Claude SDK (`messages.create` with image base64)

<!-- MANUAL: -->
