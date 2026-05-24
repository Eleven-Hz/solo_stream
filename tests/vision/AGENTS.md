<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# vision

## Purpose
Vision 모델 비교 러너. `tests/vision/config.yaml`이 지정한 영상에서 N초 간격으로 프레임을 뽑아 (선택적으로 SSIM 스킵) Gemini / Claude vision 모델들에 동일 입력을 보내고 응답·지연을 SQLite + HTML로 기록한다.

## Key Files
| File | Description |
|------|-------------|
| `run.py` | CLI 엔트리포인트. 영상 → 프레임 → vision 호출 → DB save → HTML 빌드 |
| `config.yaml` | 영상 경로, 프레임 간격, 비교할 모델 목록 (Gemini/Claude × 모델), 게임 프로필 이름 |
| `db.py` | SQLite 스키마 (`sessions`, `rounds`, `results`) + `init_db/save_session/save_round/save_result` |
| `report.py` | `build_html(db_path)` — 모델별 응답을 라운드 단위로 나열한 단일 HTML 파일 생성 |
| `video/crosscode_video_01.mkv` | 샘플 영상 (gitignored) |
| `results.db`, `result_*.html`, `result_*.txt` | 산출물 (gitignored) |

## For AI Agents

### Working In This Directory
- 영상 디코딩은 `imageio` 또는 `cv2` 중 환경에 있는 것을 사용 — 새 모델 추가 시 디코딩 경로는 건드리지 않고 `config.yaml`의 모델 리스트만 늘리면 된다.
- 출력 텍스트가 `묘사:/위치:/상황:/이벤트:` 형식이라는 가정으로 리포트가 만들어진다 — Vision 프롬프트 포맷을 바꾸면 `report.py`의 파싱도 같이 수정.
- 한 영상에 대해 여러 차례 실행하면 같은 `results.db`에 누적 — 새 세션은 별도 row로 들어가므로 HTML에서 비교 가능.

### Testing Requirements
- 의미 있는 비교를 위해 동일 프레임 시점·동일 게임 프로필 컨텍스트를 두 모델에 모두 보낸다 (run.py 내부에서 보장).
- 영상 파일은 직접 준비 (저작권 이슈 — 자기 플레이 녹화 권장).

### Common Patterns
- 게임 프로필 자동 생성도 평가 대상이라면 `run.py`가 `load_profile(game_name, ai_client=GeminiClient, ...)`을 호출 — 첫 실행 시 `game_profiles/<slug>.yaml`이 생성된다.

## Dependencies

### Internal
- `src.ai.model_client.GeminiClient` / `ClaudeClient`
- `src.chat.game_profile_loader` (`build_vision_context`)
- `src.capture.screen` (필요 시)

### External
- `imageio` / `cv2` (둘 중 하나, 영상 디코딩)
- `PyYAML`, `numpy`

<!-- MANUAL: -->
