<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# solo_stream

## Purpose
혼자 게임할 때 실제 스트리머처럼 AI 시청자 봇들이 채팅을 치는 **몰입형 솔로 게이밍 오버레이**. 화면 캡처 → Vision 분석 → 페르소나 기반 채팅 생성 → 투명 오버레이 표시 파이프라인을 멀티프로세스로 구동한다. Phase 2 기능으로 마이크 STT(silero-vad + Gemini Audio)를 통해 스트리머 발화도 채팅 컨텍스트로 반영한다.

## Key Files
| File | Description |
|------|-------------|
| `config.yaml` | 앱 전체 런타임 설정 (AI provider/model, 캡처 주기, SSIM threshold, 채팅 batch, UI, STT) |
| `requirements.txt` | Python 의존성 (google-genai, anthropic, mss, PyQt6, silero-vad 등) |
| `setup.bat` | venv 생성 + 의존성 설치 (Windows) |
| `run.bat` | `python -m src.main` 진입 (Windows) |
| `.env.example` | `GEMINI_API_KEY`, `CLAUDE_API_KEY` 템플릿 |
| `.env.local` | 실제 API 키 (gitignore 대상) |
| `.gitignore` | venv, logs, dev_history, .env.local, 테스트 산출물 제외 |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `src/` | 애플리케이션 소스 (멀티프로세스 진입점 + 도메인 모듈) — see `src/AGENTS.md` |
| `tests/` | 컴포넌트별 평가 러너 + UI 스모크 (pytest 아님, DB+HTML 리포트형) — see `tests/AGENTS.md` |
| `personas/` | 봇 페르소나 YAML (이름/말투/감정/반응 강도) — see `personas/AGENTS.md` |
| `game_profiles/` | 게임별 캐시된 프로필 YAML (자동 생성 + 수동 편집) — see `game_profiles/AGENTS.md` |
| `docs/` | 기획서, 기능 추가 계획 등 한국어 설계 문서 — see `docs/AGENTS.md` |
| `dev_history/` | 일자별 개발 일지 (gitignore 대상, dev-history skill로 자동 생성) — see `dev_history/AGENTS.md` |
| `logs/` | 세션 로그 + 채팅 라운드 블록 로그 (gitignore 대상) |
| `venv/` | Python 가상환경 (gitignore 대상, 분석 제외) |

## For AI Agents

### Working In This Directory
- 새 외부 SDK import는 **반드시** `src/ai/model_client.py` 또는 `src/stt/transcriber.py` 같은 단일 진입점에 격리한다 — 모델 deprecate 주기가 짧기 때문에 한 곳에서만 갈아끼울 수 있어야 한다.
- `config.yaml` 스키마를 바꿀 때는 `src/main.py`(프로세스 부팅), `src/ui/overlay.py`의 `SettingsPanel`(UI 동기화), 해당 도메인 모듈을 모두 같이 수정한다.
- Windows 전용 의존성 (`pywin32`, `win32gui`) 사용 — 다른 OS 호환성을 가정하지 말 것.
- 한국어 사용자 인터페이스/프롬프트가 기본 — 로그·UI·페르소나·프롬프트 모두 한국어 톤을 유지한다.

### Testing Requirements
- 자동화된 pytest 스위트는 없다. `tests/<module>/run.py` 형식의 평가 러너를 직접 실행해 HTML/TXT 리포트를 생성하는 방식이다.
- UI 변경은 `tests/ui/test_ui.py` 또는 `tests/ui/preview_settings.py`로 스모크 후 실제 `run.bat` 실행으로 확인.
- API 비용이 드는 호출(Vision/Chat/STT)은 실제 키 + Free-tier 한도 안에서 수동 검증.

### Common Patterns
- 멀티프로세스 통신은 `multiprocessing.Queue` (이미지/UI/STT/설정 브로드캐스트). 자식 프로세스는 import를 **함수 안에서** 한다 — top-level import는 spawn 시 직렬화 비용·순환 import 문제를 유발하기 때문.
- 모든 사용자 설정은 `config.yaml` 단일 진실 + `.env.local` API 키. UI 변경 시 즉시 디스크에 다시 dump 한다.
- 로그는 `src/utils/logger.py`의 `setup_logger("solo_stream")`로 통일 — 자식 로거(`solo_stream.ai` 등)는 propagate로만 출력한다.

## Dependencies

### External
- `google-genai>=1.0.0` — Gemini Vision/Chat/Audio + Google Search grounding
- `anthropic>=0.40.0` — Claude Vision/Chat (선택적 provider)
- `mss>=9.0.0` — 빠른 화면 캡처 (창모드/모니터)
- `scikit-image>=0.22.0` — SSIM 변화 감지
- `PyQt6>=6.6.0` — 투명 오버레이 UI
- `silero-vad>=5.1` + `sounddevice` + `soundfile` — 마이크 캡처 + VAD
- `pywin32>=306` — 창 enumeration / rect 조회

<!-- MANUAL: 사용자 추가 메모는 이 줄 아래에 작성 -->
