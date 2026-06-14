<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# utils

## Purpose
프로젝트 공용 유틸 두 개: 설정/API 키 로딩과 로깅 설정. 다른 모든 서브패키지가 부담 없이 import한다.

## Key Files
| File | Description |
|------|-------------|
| `paths.py` | 실행 환경(스크립트/frozen onedir) 무관 경로 해석. `resource_dir()` — 읽기전용 번들 리소스 루트. `user_data_dir()` — 쓰기 가능한 `%APPDATA%\SoloStream` (DEV 모드면 프로젝트 루트). `resolve_resource(rel)` — 사용자 오버라이드 우선→번들. `ensure_user_data()` — 최초 실행 시딩(config/personas 복사, game_profiles·logs 생성). `SOLO_STREAM_DEV=1` 이면 사용자 데이터를 프로젝트 루트로. |
| `config_loader.py` | `load_config(path=None)` — 사용자 config 우선→번들 기본(`paths.resolve_resource`) 로드. `get_api_key(config)` — `config.ai.api_key_env` (기본 `GEMINI_API_KEY`) 환경변수 + `GEMINI_API` fallback. **키 없으면 None 반환**(예전엔 ValueError raise — frozen 무콘솔에서 조용히 죽어 첫 실행 온보딩으로 전환). main 이 None 이면 AI/STT 미기동 + 오버레이 온보딩 노출. `get_claude_api_key(config)` — `CLAUDE_API_KEY` / `ANTHROPIC_API_KEY` fallback (없으면 None). `.env.local` + `.env`를 `user_data_dir()` 기준으로 자동 로드(dotenv). |
| `logger.py` | `setup_logger(name)` — `solo_stream` 부모 로거에만 콘솔+파일 핸들러 부착(`logs/session_YYYYMMDD_HHMMSS.log`), 자식은 propagate만 한다. `get_chat_logger()` — `logs/chat_*.log` 전용, `_BlockFormatter`가 멀티라인 메시지의 첫 줄에만 timestamp를 찍어 한 라운드 블록 가독성 유지. |
| `__init__.py` | 패키지 마커 |

## For AI Agents

### Working In This Directory
- **새 파일 입출력은 `Path(__file__)...` 을 직접 쓰지 말고 `paths` 모듈을 거친다.** 읽기전용 리소스→`paths.resolve_resource()`/`paths.resource_dir()`, 쓰기/사용자 데이터→`paths.user_data_dir()`. (포터블 배포 시 경로 깨짐·읽기전용 폴더 권한 오류 방지. 경로 변경 시 한 곳만 고치면 됨.)
- 새 API provider 키를 추가할 때는 `get_<provider>_api_key(config)` 형태의 작은 함수를 추가하고 `config.ai.<provider>_api_key_env` 키로 환경변수명을 외부화한다 — 직접 `os.getenv("FOO")` 하지 말 것.
- `setup_logger`는 멀티프로세스에서 **자식 프로세스 안에서도 다시 호출**해야 한다 (각 프로세스가 자기 파일 핸들러를 가짐). 이름이 `solo_stream`이면 핸들러를 만들고, `solo_stream.<sub>`이면 부모 propagate만 사용한다.
- 채팅 라운드 블록 로그를 더 멀티라인 친화적으로 바꾸려면 `_BlockFormatter`만 수정 — 다른 로그 포맷터는 건드리지 말 것.

### Testing Requirements
- 단위 테스트 없음 — 진입 시 .env 로드 + 로그 파일 생성 여부는 실제 run.bat 실행으로 확인.

### Common Patterns
- `paths._PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent` — paths가 `src/utils/` 깊이라는 가정. 폴더 구조 바꾸면 이 한 곳만 동기화 (다른 모듈은 모두 `paths.*()` 를 거치므로 영향 없음).
- 로그/게임프로필/.env.local/사용자 config 는 `paths.user_data_dir()` 아래에 쓴다 (DEV 모드면 프로젝트 루트, 배포본이면 `%APPDATA%\SoloStream`).
- 로그 파일은 일별 append (`session_YYYYMMDD.log`, `chat_YYYYMMDD.log`), `prune_old_logs()` 가 메인 시작 시 7일 경과분 정리.

## Dependencies

### Internal
- 모든 다른 src 모듈이 import.

### External
- `PyYAML`
- `python-dotenv`

<!-- MANUAL: -->
