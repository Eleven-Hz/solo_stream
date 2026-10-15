<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# tests

## Purpose
**평가 러너 모음 (pytest 아님)**. 각 서브디렉토리(`vision/`, `stt/`, `integration/`, `ui/`)에 `run.py`가 있고, 이를 직접 실행하면 SQLite DB(`results.db`)에 결과를 누적하고 `result.html` / `result.txt` 리포트를 생성한다. 모델/파라미터를 바꿔가며 품질·지연·비용을 비교하는 용도.

## Key Files
| File | Description |
|------|-------------|
| `check_chat_log.py` | 세션 chat_log를 파싱해 봇별 발언 횟수/패턴 점검 |
| `check_parse.py` | LLM 응답의 `_parse_chats` 동작 단위 점검 |
| `check_round_log.py` | 라운드 블록 로그 포맷 점검 |
| `analyze_chat_log.py` | 채팅 로그에서 paired 응답을 추출 |
| `chat_analysis_pairs.txt` | analyze_chat_log 산출 텍스트 (gitignored) |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `vision/` | Vision 모델 비교 러너 (영상 프레임 샘플링) — see `vision/AGENTS.md` |
| `stt/` | VAD + transcribe 평가 (WAV 입력) — see `stt/AGENTS.md` |
| `integration/` | Vision + STT 합쳐 vision 라운드의 lookback 컨텍스트 검증 — see `integration/AGENTS.md` |
| `ui/` | PyQt 오버레이/설정 패널 스모크 + README 스크린샷(`readme_shots.py`) — see `ui/AGENTS.md` |
| `replay/` | 샘플 영상·음성 리플레이(화면 분석→받아쓰기→채팅, main 과 같은 후처리) + 채팅 모델 A/B — see `replay/AGENTS.md` |

## For AI Agents

### Working In This Directory
- 새 평가를 추가할 때는 기존 패턴 따르기: `run.py` (CLI), `config.yaml` (입력/모델 후보), `db.py` (SQLite 스키마 + save helper), `report.py` (HTML 빌더).
- `tests/<X>/run.py`는 `sys.path.insert(0, ROOT)` 후 `src/...`를 import — pytest처럼 자동 발견되지 않는다.
- 비용이 드는 호출이므로 입력 샘플은 짧게 (영상 1~2분, WAV 30초 내외) 유지.
- 산출물(`*.txt`, `*.html`, `results.db`, `audio/*.wav`)은 모두 `.gitignore` 처리 — 결과를 PR에 첨부하려면 별도 경로 또는 인라인.

### Testing Requirements
- 자동 회귀 테스트 없음. 변경 전후 동일 입력으로 러너를 재실행하고 HTML diff를 사람이 비교.

### Common Patterns
- `db.py`는 SQLite 스키마(세션/라운드/결과) + idempotent `init_db()`.
- `report.py`의 `build_html()`은 인라인 CSS로 단일 파일 출력.

## Dependencies

### Internal
- `src/ai/model_client`, `src/chat/*`, `src/capture/*`, `src/stt/*` 를 그대로 import해 실서비스와 동일 경로로 평가.

### External
- `numpy`, `soundfile`, `PyYAML` 등 (각 서브디렉토리에서 명시)

<!-- MANUAL: -->
