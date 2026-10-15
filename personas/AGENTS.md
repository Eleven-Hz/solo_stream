<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# personas

## Purpose
봇 페르소나 YAML 모음. `src/chat/persona_loader.load_personas("personas")`가 이 디렉토리의 모든 `*.yaml`의 `bots:` 배열을 합쳐 풀로 만들고, 매 채팅 라운드마다 `chat.batch_size` 명을 랜덤 샘플링해 사용한다.

## Key Files
| File | Description |
|------|-------------|
| `default_bots.yaml` | 기본 봇 세트 (겜린이/고인물킹/트롤마스터 등). 파일 상단 주석에 모든 필드의 기본값/의미 설명 (단일 source of truth) |

## For AI Agents

### Working In This Directory
- 봇 스키마 (각 항목):
  - `name` (str, 필수) — UI에 표시되는 닉네임
  - `style` (str, 필수) — 말투 (반말/존댓말/이모티콘 사용 등)
  - `emotion` (str, 필수) — 감정 상태 (신남/평온/열받음 등)
  - `relationship` (str, 필수) — 스트리머와의 관계 (뉴비팬/고인물/트롤러 등)
  - `frequency`: `high` (100%) / `medium` (70%, 기본) / `low` (40%) — `src/chat/orchestrator._FREQUENCY_CHANCE`
  - `memory_depth` (int, 기본 0) — 자기 과거 발언을 몇 개까지 기억하는지
  - `react_to_others` (bool, 기본 true)
  - `react_chance` (float 0~1, 기본 1.0)
  - `user_speech_weight`: `off`/`low`/`medium`/`high` (없으면 `config.stt.default_weight`)
  - `triggers` (list, 옵션) — `{type, reaction}` 패턴 (현재 프롬프트에서 직접 사용은 안 함, 참고용)
  - `known_streamers` (list[str], 옵션) — 이 봇이 "다른 방송" 얘기할 때 부르는 가상의 스트리머 이름. 프롬프트 봇 정보에 주입됨 (방랑객)
- YAML에서 `user_speech_weight: off` 는 boolean False로 파싱될 수 있다 — loader가 `normalize_user_speech_weight`로 처리하므로 그대로 적어도 안전하지만, 명시적으로 `"off"` 문자열로 quote 하는 것을 권장.
- 새 페르소나 파일을 추가하면 다음 실행부터 자동 로드 — 별도 등록 코드 없음.

### Testing Requirements
- 페르소나 변경 후 실제 `run.bat`로 라운드 몇 번 돌려보고 `logs/chat_*.log`의 라운드 블록에서 의도한 말투/반응 강도가 나오는지 사람이 확인.

### Common Patterns
- 봇 10~15명 정도가 목표. 너무 많으면 `batch_size`가 작아서 매 라운드 같은 봇이 등장하지 않게 됨.
- 말투가 겹치지 않게 — 프롬프트 규칙에 "봇마다 말투가 확실히 달라야 함, 같은 내용 다르게 말하기 금지" 가 있어 모델이 강제하지만, 페르소나 자체가 비슷하면 한계.

## Dependencies

### Internal
- `src/chat/persona_loader.py` 가 로드
- `src/ai/model_client._build_bots_info` 가 봇 라인 포맷 결정

<!-- MANUAL: -->
