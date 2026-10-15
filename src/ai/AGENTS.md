<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# ai

## Purpose
**유일한 AI API 접점**. 외부 SDK (`google.genai`, `anthropic`, `openai`(vision/채팅))는 이 디렉토리에서만 import된다(OpenAI STT 는 `src/stt/transcriber.py`). Vision 분석(`analyze_screenshot`)과 봇 채팅 생성(`generate_chats`)을 동일 인터페이스로 노출하고, `config.yaml`의 `ai.vision_provider` / `ai.chat_provider`로 provider(`gemini` | `claude` | `openai`)를 런타임 교체할 수 있다. 모델 이름 키는 `model_config_key(provider, kind)`(gemini=`vision_model`/`chat_model`, 그 외 `<provider>_<kind>_model`), 현재 선택은 `selected_model(config, kind)`로 읽는다 — main·설정 창이 모두 이 두 함수를 쓴다. 고른 provider 키가 없으면 팩토리가 Gemini 로 대체한다(죽지 않음). 모델 deprecate 주기가 짧다는 전제 하에 한 파일만 수정해 갈아끼울 수 있도록 설계됨.

## Key Files
| File | Description |
|------|-------------|
| `model_client.py` | `GeminiClient`, `ClaudeClient`, `OpenAIClient`(Responses API, reasoning.effort 를 none→minimal→미지정 순으로 낮춰 시도·모델별 기억), `create_vision_client`, `create_chat_client`. 채팅 프롬프트는 `_build_chat_prompt` 하나로 세 provider 가 공유. Claude 는 thinking off(+5세대는 effort low). Vision 프롬프트 빌더(`_build_vision_prompt`), 채팅 프롬프트 빌더(`_build_bots_info`, `_build_user_speech_section`), JSON 응답 파서(`_parse_chats`, trailing-comma·코드펜스·잡음 텍스트 보수적 cleanup), 429 quota 재시도 로직 포함 |
| `__init__.py` | 패키지 마커 |

## For AI Agents

### Working In This Directory
- 새 provider 추가 시 `GeminiClient`/`ClaudeClient`와 동일한 시그니처(`analyze_screenshot(image_bytes, game_profile_context, previous_location)`, `generate_chats(game_context, bots, chat_history, game_profile_context, user_speech, default_speech_weight, mentioned_names)`)를 구현한 후 `create_vision_client`/`create_chat_client`의 분기에 추가.
- `mentioned_names`는 스트리머 발화에서 이름이 불린 봇 목록 — `_build_bots_info`가 해당 봇 라인에 `[호명됨] true`를 붙이고, 프롬프트 규칙이 그 봇의 강제 응답을 지시한다. 호명 봇을 batch에 실제로 포함시키는 책임은 `src/main.py::_detect_mentioned_bots` + ai_process의 batch 선정부에 있다 (여기선 표시·규칙만 담당).
- 채팅 응답 파싱은 LLM이 자주 망치는 패턴(코드펜스, trailing comma, 앞뒤 설명)을 모두 정규식으로 잡고, 최후의 fallback으로 `{"name":...,"message":...}` 개별 매칭까지 수행 — 새 케이스를 발견하면 `_parse_chats`만 수정한다.
- `ChatCallDebug`는 `ai_process`에서 라운드 블록 로그에 합쳐 출력하는 용도 — `provider`/`model`/`prompt`/`raw_response`/`elapsed_s` 필드 시그니처를 유지할 것.

### Testing Requirements
- `tests/vision/run.py` — Vision 모델 비교 (Gemini/Claude × 영상 프레임 샘플).
- 채팅 생성은 `tests/integration/run.py`에서 vision+STT와 함께 검증.

### Common Patterns
- `_call_with_retry(fn)`는 429/`RESOURCE_EXHAUSTED` 에러 메시지의 `retry in Ns` 를 파싱해 그만큼 대기 후 재시도. exponential fallback 포함.
- Vision 프롬프트는 한국어, 출력 형식은 `묘사:/위치:/상황:/이벤트:` 4줄 고정 — `src/main.py`의 위치 추출 로직이 `위치:` 접두사에 의존한다.
- Vision 분석 실패 시 `analyze_screenshot`은 `VISION_ERROR_MARKER` 상수를 반환한다. 채팅 프롬프트 빌드 시 `_game_context_for_prompt()`가 이 값을 "(화면 정보 없음 ...)"으로 치환해, 봇이 오류 문장을 채팅 소재로 삼거나 스트리머 발화를 무시하는 것을 막는다. 오류 문구를 바꾸면 이 상수만 고치면 된다.
- 채팅 프롬프트는 `[최근 스트리머 발화]`를 `[현재 게임 상황]` **위**에 배치한다 (호명 봇 응답률 위해 발화 주목도 상향). 순서를 되돌리면 호명/발화 반응이 약해질 수 있다.
- 질문 응답 규칙은 호명 봇이 없어도 최소 1봇이 "구체적 내용으로" 답하도록 강제하고, 게임내 질문(고인물/분석봇이 정보 근거 답)과 추천형 질문(실제 제목 제시)을 구분해 회피("글쎄/음...")를 막는다. 규칙 완화 시 순수 질문 무응답이 재발할 수 있다.
- 채팅 프롬프트의 봇별 라인 형식 (`/ [자기 기억] N개 / [타인 반응] true / [타인 반응 빈도] X% / [스트리머 발화 반응] medium / [호명됨] true`)을 바꾸면 모델의 답변 일관성이 깨질 수 있으니 신중히. `[호명됨] true`는 호명된 봇에만 붙고, 규칙 블록의 호명/질문 응답 지시와 짝을 이룬다.

## Dependencies

### Internal
- `src/chat/persona_loader.normalize_user_speech_weight` — `off/low/medium/high` 정규화 (YAML이 `off`를 `False`로 파싱하는 함정 대비)

### External
- `google.genai` — Gemini SDK (`types.Part.from_bytes`, `types.GenerateContentConfig`, `types.ThinkingConfig`)
- `anthropic` — Claude SDK (`messages.create` with image base64)

<!-- MANUAL: -->
