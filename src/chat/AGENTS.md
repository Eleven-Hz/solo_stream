<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# chat

## Purpose
채팅 도메인 로직. 봇 페르소나 로딩(YAML), 게임 프로필 로딩(캐시 또는 자동 생성), 그리고 생성된 채팅 목록을 봇별 빈도/랜덤 딜레이로 UI 큐에 흘려보내는 오케스트레이션.

## Key Files
| File | Description |
|------|-------------|
| `persona_loader.py` | `load_personas(persona_dir)` — 번들 `personas/*.yaml` + 사용자 파일을 `name` 기준 병합하고, 마지막에 설정 창 파일 `UI_FILE`(settings_bots.yaml)을 덮어 풀 반환. 설정 창용 `persona_entries`(꺼진 봇 포함 `{bot, builtin, edited, enabled}`) / `save_persona_entries`(기본값 그대로인 봇은 안 적음). `normalize_user_speech_weight(value, default)` — YAML이 `off`를 boolean False로 파싱하는 함정 + `off/low/medium/high` 검증. 봇 dict의 선택 필드 `aliases`(list)는 STT가 이름을 풀어 받아쓸 때의 호명 매칭용 — `src/main.py::_detect_mentioned_bots`가 name+aliases를 공백 제거 후 부분문자열 검사. |
| `game_profile_loader.py` | `load_profile(game_name, ai_client, config, auto_generate=False)` — `game_profiles/<slug>.yaml` 캐시 우선. 없으면 기본은 빈 프로필+`_missing`(UI 가 '프로필 없음' 안내). **수동(기본, 비용 0)**: `build_manual_prompt` 를 구독 채팅에 붙여넣고 받은 답을 `parse_profile_text`(코드블록·JSON·출처 링크 정리, YAML 이 깨지면 줄 단위 `_parse_loose`) → `save_profile`. `auto_generate=True`(설정 '자동 생성' 버튼 → `{"game_name", "generate": True}`)일 때만 Gemini Google Search grounding으로 5단계 멀티턴 생성(기본+인물/시스템·지역/적·스킬/vision_focus/chat_tips/한국어 번역 검수, 공통 규칙 `_RULES`: 확인된 것만·설명 30자). `build_profile_context` (채팅용 전체) / `build_vision_context` (Vision 묘사 전용, 토큰 최소화 — 인물 중 '주인공'이 있으면 '플레이어 캐릭터: 이름' 줄로 외형 대신 이름을 쓰게 함) 헬퍼 제공. |
| `emotion.py` | 봇 감정 변화 시스템(Phase 2). `EmotionState`(봇별 score -1~+1, `apply`/`decay`/`label`), `update_emotions(states, bot_pool, chat_history, event_text, user_speech_texts)` — **3신호(채팅 톤 + Vision `이벤트:` + STT 발화)** 합산으로 매 라운드 감정 전이 후 `{name: 라벨}` 반환. 5단계 라벨(흥분/신남/평온/실망/짜증). Vision 이벤트가 비어도 나머지 신호로 작동(`event_valence("")==0`). 봇 `relationship`별 감도 차등(안티는 긍정 둔감/부정 민감, 찐팬은 반대), `decay`로 base 감정 복귀, `apply`의 `max_step=0.4`로 라운드당 급변 제한. |
| `flow.py` | 대화 흐름 장치(2026-10-15 실사용 로그 분석 대응). `pick_batch`(호명 봇 → 직전 발언자 `carry_over` 명 → 무작위), `pick_reply`(받아칠 봇·대상 채팅을 **코드가** 고름 — LLM 은 '30% 확률' 지시를 못 지킴), `build_round_notes`(프롬프트의 [이번 라운드 대화 과제]: 새 발화엔 절반 이상 반응·인사엔 인사·질문엔 답(`speech_kind` 질문 판정은 의문사+서술 어미면 제외)·계획엔 계획 언급·**새 말이 여러 개면 `split_topics`→`assign_topics` 로 주제별 담당 봇 지정**(최대 3주제, 질문·최근 말 우선, 호명 봇은 자기 이름 나온 말), 받아치기 지정, 발화 즉시 반응 라운드 표시), `exempt_current`(지금 화면·발화에 나온 소재는 과열 회피 면제), `BotHabits`(봇별 최근 8발언에서 2번 이상 쓴 단어를 '말버릇 금지'로 표시 + 그대로 쓰면 드롭), `stt_hint_terms`/`stt_prompt_with_terms`(프로필 고유명사 → 받아쓰기 힌트). |
| `lookup.py` | 정확한 정보 봇(페르소나 `accurate: true`) 웹 검색. `LookupDesk.pick(batch, speech)` — 그 봇이 batch 에 있고 새 질문이 있으면 (봇, 정리한 질문: 호명·'그리고' 제거), 이름 불린 질문 우선, 이미 찾았거나 찾는 중인 질문(유사도 0.7↑)은 건너뜀. `start(...)` 가 백그라운드 스레드로 `client.lookup_answer` → 답이 있으면 시작 8초 뒤 이후에 `post(chat)`(라운드의 '찾아볼게요'가 먼저 뜨게). 찾은 답은 세션 동안 8개 기억(`facts()` → 봇 dict `known_facts` → 프롬프트 '이 봇이 검색해 둔 정보'), `pop_done()` 은 ai_process 가 채팅 기록에 넣는다. 라운드 노트엔 `build_round_notes(lookup=(봇, 질문))` 로 '찾아보는 중만 말하기' + 그 봇을 그 질문에 고정(`assign_topics(pinned=)`). |
| `orchestrator.py` | `schedule_chats(chats, bots, ui_queue, delay_min, delay_max, mentioned_names)` — 별도 스레드에서 봇 `frequency`(high=1.0/medium=0.7/low=0.4)에 따라 일부 스킵, 1~8초 랜덤 딜레이 후 ui_queue.put. **`mentioned_names`(호명된 봇)는 frequency 스킵에서 면제** — low 빈도 봇이라도 호명 답변은 반드시 UI에 띄운다. `round_id` 를 주면 마지막 채팅 뒤 그 라운드의 '입력 중' 표시를 끄는 `{system, type: typing, on: False}` 를 보낸다(`main.ai_process` 가 라운드 시작 때 켬). |
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
