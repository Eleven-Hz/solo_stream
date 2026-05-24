<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# utils

## Purpose
프로젝트 공용 유틸 두 개: 설정/API 키 로딩과 로깅 설정. 다른 모든 서브패키지가 부담 없이 import한다.

## Key Files
| File | Description |
|------|-------------|
| `config_loader.py` | `load_config(path=None)` — 프로젝트 루트 기준 `config.yaml` 로드. `get_api_key(config)` — `config.ai.api_key_env` (기본 `GEMINI_API_KEY`) 환경변수 + `GEMINI_API` fallback. `get_claude_api_key(config)` — `CLAUDE_API_KEY` / `ANTHROPIC_API_KEY` fallback (없으면 None). `.env.local` + `.env`를 자동 로드(dotenv). |
| `logger.py` | `setup_logger(name)` — `solo_stream` 부모 로거에만 콘솔+파일 핸들러 부착(`logs/session_YYYYMMDD_HHMMSS.log`), 자식은 propagate만 한다. `get_chat_logger()` — `logs/chat_*.log` 전용, `_BlockFormatter`가 멀티라인 메시지의 첫 줄에만 timestamp를 찍어 한 라운드 블록 가독성 유지. |
| `__init__.py` | 패키지 마커 |

## For AI Agents

### Working In This Directory
- 새 API provider 키를 추가할 때는 `get_<provider>_api_key(config)` 형태의 작은 함수를 추가하고 `config.ai.<provider>_api_key_env` 키로 환경변수명을 외부화한다 — 직접 `os.getenv("FOO")` 하지 말 것.
- `setup_logger`는 멀티프로세스에서 **자식 프로세스 안에서도 다시 호출**해야 한다 (각 프로세스가 자기 파일 핸들러를 가짐). 이름이 `solo_stream`이면 핸들러를 만들고, `solo_stream.<sub>`이면 부모 propagate만 사용한다.
- 채팅 라운드 블록 로그를 더 멀티라인 친화적으로 바꾸려면 `_BlockFormatter`만 수정 — 다른 로그 포맷터는 건드리지 말 것.

### Testing Requirements
- 단위 테스트 없음 — 진입 시 .env 로드 + 로그 파일 생성 여부는 실제 run.bat 실행으로 확인.

### Common Patterns
- `_ROOT = Path(__file__).parent.parent.parent` — utils가 `src/utils/` 깊이라는 가정. 폴더 구조 바꾸면 동기화.
- 로그 파일은 매 프로세스 start마다 새로 생성됨 (`session_<ts>.log`, `chat_<ts>.log`) — 정리는 수동 또는 외부 cron.

## Dependencies

### Internal
- 모든 다른 src 모듈이 import.

### External
- `PyYAML`
- `python-dotenv`

<!-- MANUAL: -->
