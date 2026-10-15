<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-10-14 -->

# ui

## Purpose
PyQt6 오버레이·설정 창 스모크 테스트. 실서비스 백엔드(캡처/AI/STT) 없이 가짜 메시지로 UI 동작을 확인한다.

## Key Files
| File | Description |
|------|-------------|
| `smoke_settings_tabs.py` | 가짜 게임 화면 창 위에 오버레이를 띄우고 투명도 15/50/100%, 글자만+마우스 없음, 캡처 제외 상태와 설정 창 6페이지를 **실제 화면 캡처**로 PNG 저장(기본 `tests/ui/output/`). 메시지 수·예외 여부도 확인. |
| `readme_shots.py` | README 스크린샷 생성. 위젯을 직접 렌더(`grab()`)해 원격·작은 화면에서도 안 잘리고, 임시 데이터 폴더(가짜 키·crosscode 프로필)로 실행해 실제 키·경로가 안 찍힌다. 기본 출력 `tests/ui/output/readme/` → 확인 후 `docs/images/` 로 복사. |
| `test_ui.py` | 0.5초 간격 가짜 메시지를 흘려 넣고 `run_overlay` 로 띄우는 수동 확인용. |
| `__init__.py` | 패키지 마커 |

## For AI Agents

### Working In This Directory
- `QWidget.render()` 는 QGraphicsEffect·반투명 창 합성을 실제와 다르게 그리고, offscreen 플랫폼은 한글 폰트가 없다 — 시각 확인은 화면 캡처(`QScreen.grabWindow`)로 한다.
- 캡처 전에는 `set_capture_exclusion(False)` 로 캡처 제외를 꺼야 오버레이가 찍힌다(테스트가 그렇게 하고, 마지막에 켜서 '안 찍히는지'를 따로 남긴다).
- 몇 초간 화면 왼쪽 위에 창이 떴다 사라진다. 사용자가 작업 중인 화면이면 미리 알린다.

### Testing Requirements
- Windows + PyQt6 환경. 실 API 호출 없음 — 비용 0.
- `SOLO_STREAM_DEV=1` 로 실행하면 사용자 데이터 폴더가 프로젝트 루트가 된다(설정 저장 테스트를 할 때는 `APPDATA` 를 임시 폴더로 돌려 실제 설정을 건드리지 않게).

## Dependencies

### Internal
- `src.ui.overlay.ChatOverlay`, `run_overlay`, `src.ui.settings.SettingsWindow`

### External
- `PyQt6`

<!-- MANUAL: -->
