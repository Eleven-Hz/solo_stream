<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# chat

## Purpose
채팅 도메인 로직. 봇 페르소나 로딩(YAML), 게임 프로필 로딩(캐시 또는 자동 생성), 그리고 생성된 채팅 목록을 봇별 빈도/랜덤 딜레이로 UI 큐에 흘려보내는 오케스트레이션.

## Key Files
| File | Description |
|------|-------------|
| `persona_loader.py` | `load_personas(persona_dir)` — `personas/*.yaml`의 `bots:` 배열 모두 합쳐 풀 반환. `normalize_user_speech_weight(value, default)` — YAML이 `off`를 boolean False로 파싱하는 함정 + `off/low/medium/high` 검증. 봇 dict의 선택 필드 `aliases`(list)는 STT가 이름을 풀어 받아쓸 때의 호명 매칭용 — `src/main.py::_detect_mentioned_bots`가 name+aliases를 공백 제거 후 부분문자열 검사. |
| `game_profile_loader.py` | `load_profile(game_name, ai_client, config)` — `game_profiles/<slug>.yaml` 캐시 우선, 없으면 Gemini Google Search grounding으로 5단계 멀티턴 생성(기본/시스템·지역/적·스킬/vision_focus/chat_tips/한국어 번역 검수). `build_profile_context` (채팅용 전체) / `build_vision_context` (Vision 묘사 전용, 토큰 최소화) 헬퍼 제공. |
| `orchestrator.py` | `schedule_chats(chats, bots, ui_queue, delay_min, delay_max, mentioned_names)` — 별도 스레드에서 봇 `frequency`(high=1.0/medium=0.7/low=0.4)에 따라 일부 스킵, 1~8초 랜덤 딜레이 후 ui_queue.put. **`mentioned_names`(호명된 봇)는 frequency 스킵에서 면제** — low 빈도 봇이라도 호명 답변은 반드시 UI에 띄운다. |
| `__init__.py` | 패키지 마커 |

## For AI Agents

### Working In This Directory
- 페르소나 YAML에 `user_speech_weight: off` 같이 YAML 키워드와 충돌하는 값이 들어가도 `normalize_user_speech_weight`가 처리 — 새 weight 추가 시 `_VALID_WEIGHTS` 에 추가하고 `src/ai/model_client.py`의 채팅 프롬프트 설명에도 동일하게 적힘에 유의.
- 게임 프로필 자동 생성은 Gemini만 지원 (Google Search grounding 필요) — `src/main.py`의 `ai_process`가 항상 별도 `GeminiClient(gemini_client)`를 만들어 전달하는 이유. Claude provider로 바꿔도 프로필 생성은 Gemini로 가야 한다.
- `_safe_filename`은 게임명을 소문자·언더스코어·64자로 만든다. 한글 게임명도 그대로 (예: `명조워더링_웨이브.yaml`).
- `schedule_chats`는 daemon thread에서 돈다 — 라운드가 끝나기 전에 다음 라운드의 캡처/분석이 시작될 수 있다. `ui_queue`는 maxsize=100이고 채팅 한 라운드 5개 이하이므로 일반적으로 안전.

### Testing Requirements
- 페르소나/프로필 로딩 단위 검증은 없음 — `tests/integration/run.py`가 실제 vision+chat을 묶어 검증.

### Common Patterns
- 게임 프로필 생성 단계는 try/except로 감싸 한 단계 실패해도 다음 단계 진행 (부분 프로필 허용).
- `build_vision_context`는 `: 설명` 앞부분만 잘라 지명/적/스킬 목록만 컴팩트하게 전달 — Vision 프롬프트 토큰 절약 + 모델이 화면 텍스트와 비교하기 쉬움.

## Dependencies

### Internal
- `src/ai/model_client.py`의 `GeminiClient` — `_client.models.generate_content` 를 통해 Google Search grounding 호출.

### External
- `PyYAML` — 페르소나/프로필 직렬화
- `google.genai.types` — `Tool(google_search=GoogleSearch())` grounding 설정

<!-- MANUAL: -->
