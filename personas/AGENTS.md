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
  - `accurate` (bool, 옵션, 기본 false) — true면 프롬프트 봇 라인에 `[정확한 정보] true` → 근거([게임 정보]·화면 글자·발화) 있는 사실만 단정, 모르면 지어내지 않음. 다른 봇의 헛소리는 허용(KNOWN_ISSUES 4-6)이지만 고인물킹은 예외라 켜 둠. 이 봇이 질문을 맡으면 웹 검색해 따로 답한다(`src/chat/lookup.py`, `chat.web_search`)
  - `known_streamers` (list[str], 옵션) — 이 봇이 "다른 방송" 얘기할 때 부르는 가상의 스트리머 이름. 프롬프트 봇 정보에 주입됨 (방랑객)
- YAML에서 `user_speech_weight: off` 는 boolean False로 파싱될 수 있다 — loader가 `normalize_user_speech_weight`로 처리하므로 그대로 적어도 안전하지만, 명시적으로 `"off"` 문자열로 quote 하는 것을 권장.
- 새 페르소나 파일을 추가하면 다음 실행부터 자동 로드 — 별도 등록 코드 없음.
- 설정 창 '시청자 봇' 페이지(켜기/끄기·편집·새 봇)는 사용자 데이터 폴더의 `personas/settings_bots.yaml`(`persona_loader.UI_FILE`)에만 쓴다 — 번들·직접 둔 파일을 다 합친 뒤 **맨 마지막에** 덮는다. 기본값 그대로인 봇은 적지 않고, 끈 기본 봇은 `{name, disabled: true}`, 고친 봇·새 봇은 봇 전체(끄면 `disabled: true` 추가 — 내용은 남아 다시 켤 수 있음). DEV 모드면 이 프로젝트의 `personas/` 에 생기며 gitignore 대상. 저장하면 `{"personas_reload": True}` 로 AI 프로세스가 다음 라운드부터 새 봇 풀을 쓴다. 파일에 `version`(저장한 앱 버전)과 `based_on`(고친 기본 봇의 '고칠 때 기본값' 해시)을 남겨, 업데이트로 그 기본 봇이 바뀌면 `outdated` 로 알린다.
- 예전 버전 경고·초기화: `stale_persona_files` = 사용자 폴더에 번들과 **같은 파일 이름**(예: 옛 시딩 `default_bots.yaml`)이 있으면 지금 기본 봇을 덮는 옛 복사본으로 본다. 설정 '시청자 봇' 페이지가 배너·메뉴 ⚠ 로 알리고, 'vX 기본 봇으로 초기화'(`reset_personas`)는 그 파일과 설정 창 파일을 `*.bak-날짜` 로 옮긴다(삭제 안 함, 직접 둔 다른 파일은 그대로).

### Testing Requirements
- 페르소나 변경 후 실제 `run.bat`로 라운드 몇 번 돌려보고 `logs/chat_*.log`의 라운드 블록에서 의도한 말투/반응 강도가 나오는지 사람이 확인.

### Common Patterns
- 봇 10~15명 정도가 목표. 너무 많으면 `batch_size`가 작아서 매 라운드 같은 봇이 등장하지 않게 됨.
- `style` 괄호 안 예시는 모델이 **첫 예시를 매 줄 그대로** 쓴다(2026-10-23 리플레이: 지나가던행인 "(ㅇㅇ, ㄷㄷ…)" → 4줄 모두 "ㅇㅇ…", PD님 "(클립각…)" → 5줄 중 4줄 "클립각"). 구체 단어 대신 종류를 적고 "매번 다른"을 붙인다.
- 존댓말·반말 비율: 실제 방송 채팅은 스트리머에게 존댓말을 쓰는 시청자가 많다 — 기본 23명 중 존댓말 7명(+외쿸인 섞어 씀). 새 봇을 넣을 때 반말로 쏠리지 않게.
- 말투가 겹치지 않게 — 프롬프트 규칙에 "봇마다 말투가 확실히 달라야 함, 같은 내용 다르게 말하기 금지" 가 있어 모델이 강제하지만, 페르소나 자체가 비슷하면 한계.

## Dependencies

### Internal
- `src/chat/persona_loader.py` 가 로드
- `src/ai/model_client._build_bots_info` 가 봇 라인 포맷 결정

<!-- MANUAL: -->
