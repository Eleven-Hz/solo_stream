<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# src

## Purpose
멀티프로세스 진입점(`main.py`)과 도메인별 모듈(capture/ai/chat/stt/ui/utils)을 담는 애플리케이션 코드 루트. 세 개 또는 네 개의 프로세스(Capture / AI / STT(선택) / UI)가 `multiprocessing.Queue`로 연결되며, UI에서 발생한 설정 변경은 `broadcast_queue` → 각 프로세스의 settings_queue로 전파된다.

## Key Files
| File | Description |
|------|-------------|
| `main.py` | 엔트리포인트. 큐/프로세스 생성, `capture_process` / `ai_process` / `stt_process` / `ui_process` 정의, 설정 브로드캐스트 스레드 구동, 라운드 블록 로그 포맷 (`_write_round_block`) 정의 |
| `__init__.py` | 패키지 마커 + **앱 버전 단일 소스** (`__version__`). UI 설정 화면(게임 탭 하단 about 섹션)·`사용설명.txt`가 이 값을 참조. 릴리스 시 여기 한 곳만 올린다. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `ai/` | Gemini/Claude 클라이언트 단일 진입점 (`model_client.py`) — see `ai/AGENTS.md` |
| `capture/` | mss 기반 화면 캡처 + SSIM 변화 감지 — see `capture/AGENTS.md` |
| `chat/` | 페르소나/게임 프로필 로더 + 봇별 타이밍 오케스트레이터 — see `chat/AGENTS.md` |
| `stt/` | silero-vad + Gemini Audio 실시간 음성 인식 파이프라인 — see `stt/AGENTS.md` |
| `ui/` | PyQt6 투명 오버레이 + 설정 패널 (탭: 게임/AI 모델/음성) — see `ui/AGENTS.md` |
| `utils/` | `config_loader`, `logger` 공용 유틸 — see `utils/AGENTS.md` |

## For AI Agents

### Working In This Directory
- `main.py`의 프로세스 함수는 **무거운 import를 함수 본문 안에서** 수행한다. 모듈 top-level에서 `PyQt6`/`google.genai`/`mss`를 import하면 모든 자식 프로세스가 spawn 시 다시 로딩하면서 시작이 느려지고, 일부 SDK는 fork-safe하지 않다.
- 새 프로세스 간 메시지를 추가할 때:
  1. `broadcast_queue`에 들어갈 설정 dict 키를 정한다 (예: `{"chat_active": True}`)
  2. 받는 쪽 프로세스(`capture_process` / `ai_process` / `stt_process`)의 `while not settings_queue.empty():` 블록에 elif 분기 추가
  3. UI 쪽 `SettingsPanel`에서 `self._settings_queue.put(...)` 호출 추가
- `_extract_game_name`은 `main.py`와 `ui/overlay.py` 두 곳에 중복 정의되어 있다 — 한쪽을 수정하면 다른 쪽도 같이 수정한다 (UI 프로세스와 AI 프로세스가 서로 import하지 않기 위함).

### Testing Requirements
- 통합 실행: `run.bat` (= `python -m src.main`). 반드시 `.env.local`에 `GEMINI_API_KEY` 필요.
- 도메인 모듈 단위 평가는 `tests/<module>/run.py`로 수행 — see `tests/AGENTS.md`.

### Common Patterns
- 모든 로깅은 `setup_logger("solo_stream")` 후 `logging.getLogger("solo_stream.<subsystem>")` 로 자식 로거 사용 — 핸들러는 부모만 가진다.
- 채팅 한 라운드의 vision/STT/프롬프트/응답은 `_write_round_block`이 `\n` join 멀티라인 메시지로 한 번에 `chat_log.info()` — `_BlockFormatter`가 첫 줄에만 타임스탬프를 찍어 시각적 블록을 유지.
- 봇 호명: `ai_process`의 batch 선정부에서 `_detect_mentioned_bots(user_speech_payload, bot_pool)`로 발화에 이름이 불린 봇을 찾아 batch에 먼저 채우고(`mentioned_in_batch`), 남은 자리만 `random.sample`로 채운다. 호명 봇 이름은 `generate_chats(mentioned_names=...)`로 넘겨 프롬프트의 `[호명됨] true` 표시·강제 응답 규칙과 연결되고, 동일한 `mentioned_names`를 `schedule_chats(..., mentioned_names)`에도 넘겨 **호명 봇의 답변이 frequency 스킵으로 UI에서 누락되지 않게** 한다 (호명→batch 포함→LLM 응답까지 됐어도 orchestrator 스킵에 걸리면 화면에 안 뜸). 매칭은 발화·후보(봇 `name` + `aliases`)를 모두 공백 제거 후 부분문자열 비교 — STT가 이름을 풀어 받아쓰는 경우(예: "도네요정"→"돈의 요정")는 페르소나 `aliases`로 보완한다.
- 봇 감정: `ai_process`가 `emotion_states`(봇별 `EmotionState`)를 보관하고 매 라운드 `update_emotions(...)`로 3신호(채팅 톤 + `이벤트:` 줄 + STT 발화) 갱신. 반환된 `{name: 라벨}`로 **batch 봇 dict의 `emotion`을 동적 라벨로 덮은 사본**을 만들어 `generate_chats`에 넘긴다 (원본 `bot_pool`의 정적 emotion/aliases/triggers는 보존). `이벤트:`는 `game_context`에서 `위치:`와 함께 파싱되며 없으면 빈 문자열 → 감정은 채팅/발화 신호로만 굴러간다. `_write_round_block`의 봇 요약에 `(weight/감정)`으로 표시.

## Dependencies

### Internal
- 도메인 모듈끼리 직접 의존하지 않고 `main.py`가 모든 와이어링을 담당.
- `utils/`는 모든 모듈이 사용하는 공용 유틸.

### External
- `multiprocessing` (표준 라이브러리) — 큐 + 프로세스
- 각 서브디렉토리의 AGENTS.md 참조

<!-- MANUAL: -->
