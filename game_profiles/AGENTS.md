<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# game_profiles

## Purpose
게임별 자동 생성/수동 편집 프로필 YAML 캐시. `src/chat/game_profile_loader.load_profile(game_name)`이 `{slug}.yaml` 가 있으면 로드한다. 없으면 기본은 빈 프로필로 진행하고, 사용자가 설정 '게임 · 프로필'에서 프롬프트를 복사해 구독 채팅(ChatGPT/Claude/Gemini)에 붙여넣고 받은 답을 붙여넣어 저장한다(무료). '자동 생성' 버튼을 누를 때만 Gemini Google Search grounding으로 5단계 멀티턴 생성 후 이 디렉토리에 저장한다. 채팅 프롬프트와 Vision 프롬프트 양쪽에 컨텍스트로 주입된다 (`build_profile_context` / `build_vision_context`).

## 배포·우선순위
이 폴더의 `*.yaml` 은 배포본에 **읽기전용 기본 제공 프로필**로 들어간다(`solo_stream.spec`). 실행 시 `load_profile` 은 사용자 `%APPDATA%\SoloStream\game_profiles/` 를 먼저 보고, 같은 파일명이 없을 때만 여기 것을 쓴다. 저장·자동 생성은 항상 사용자 폴더에 쓴다. (DEV 모드에선 두 폴더가 같다.)

## Key Files
| File | Description |
|------|-------------|
| `default.yaml` | 프로필 미등록 / 자동 생성 실패 시 fallback |
| `crosscode.yaml` | CrossCode 프로필 (자동 생성된 후 수동 보강 가능) |
| `nte.yaml` | (NTE / 게임 약칭 기반) |
| `forza_horizon_6.yaml` | Forza Horizon 6 |
| `명조워더링_웨이브.yaml` | 명조: 워더링 웨이브 (한글 슬러그 그대로 허용) |

## For AI Agents

### Working In This Directory
- 프로필 스키마:
  - `name` (str) — 표시명, 한국어 병기 권장 (예: `CrossCode (크로스코드)`)
  - `description` (str) — 2~3문장 게임 설명
  - `vision_focus` (list[str]) — Vision 묘사 시 주목할 시각 요소 (이 항목들은 `_build_vision_prompt`가 묘사 힌트로 추출)
  - `systems` (list[str]) — 전투/성장/특수 게이지 등
  - `locations` (list[str]) — `한국어명 (영문): 설명` 형식
  - `enemies` (list[str]) — 동일 형식
  - `skills` (list[str]) — 동일 형식
  - `chat_tips` (list[str]) — 시청자 채팅 밈/반응 포인트
  - `stt_terms` (list[str], 선택) — 받아쓰기 힌트에 넣을 고유명사(캐릭터·동료 이름 등). 지명·적·기술 이름은 자동으로 들어가니 그 밖의 것만 (`src/chat/flow.stt_hint_terms`)
- 자동 생성된 프로필은 한국어 번역이 어색할 수 있다 — 5단계 검수에도 빠지면 직접 수정하거나 UI의 "선택 창 게임 프로필 재생성" 버튼으로 다시 만든다.
- 파일명 규칙: `_safe_filename(game_name)` — 소문자, 공백→`_`, 특수문자 제거, 64자 제한. 한글은 그대로 살림.
- `default.yaml`은 `_load_default()`가 직접 읽는 fallback — 키를 빼면 자동 생성 실패 시 KeyError가 날 수 있어 주의.

### Testing Requirements
- 새 게임 프로필 추가 후 `tests/vision/run.py`로 Vision 묘사 품질, `tests/integration/run.py`로 채팅 톤이 게임 맥락에 맞는지 확인.

### Common Patterns
- 자동 생성은 5번의 Gemini 호출 (Google Search grounding) — 첫 실행 시 십수 초 소요 + API 비용 약간 발생. 캐시되므로 1회로 끝남.
- 수동 편집 후에도 키 구조만 유지하면 그대로 사용됨 — `vision_focus`를 직접 다듬는 것이 가장 큰 품질 개선 포인트.

## Dependencies

### Internal
- `src/chat/game_profile_loader.py` 가 read/write
- `src/ai/model_client.GeminiClient` 의 `_client.models.generate_content` + `Tool(google_search=GoogleSearch())`

<!-- MANUAL: -->
