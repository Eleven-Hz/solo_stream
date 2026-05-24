<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# ui

## Purpose
PyQt6 오버레이 스모크 테스트. 실서비스 백엔드(캡처/AI/STT) 없이 가짜 메시지 또는 단독 위젯만 띄워 UI 동작(스크롤, 탭 전환, 설정 패널 위젯 배치)을 즉시 확인.

## Key Files
| File | Description |
|------|-------------|
| `test_ui.py` | 채팅 스크롤 스모크. 별도 스레드에서 0.5초 간격으로 fake 메시지를 ui_queue에 push, `run_overlay`로 띄움. |
| `preview_settings.py` | `SettingsPanel`만 단독 위젯으로 띄워서 시각 점검 |
| `smoke_settings_tabs.py` | 탭 전환·각 섹션 빌더가 예외 없이 돌아가는지 확인 |
| `__init__.py` | 패키지 마커 |

## For AI Agents

### Working In This Directory
- `test_ui.py`는 현재 `run_overlay(cfg, q)` 시그니처 (2 args)로 호출하지만 production은 `run_overlay(cfg, ui_queue, settings_queue)` (3 args) — 이 파일은 settings_queue 추가 전 버전 그대로다. 새로 실행하려면 두 번째 Queue 인자 추가.
- 새 위젯 추가 시 production `SettingsPanel`에서 빌더 메서드를 분리해두면 `preview_settings.py`에서 단독 호출하기 쉽다.
- 위젯 시각 검증은 캡처 비교가 없고 사람이 봐야 함 — 변경 전후 스크린샷을 PR에 첨부하는 패턴.

### Testing Requirements
- PyQt6가 설치된 환경에서만 동작 (Windows 권장 — 다른 OS는 win32gui import가 실패해도 SettingsPanel은 열리지만 캡처 창 목록은 비어있음).
- 실 API 호출 없음 — 비용 0.

### Common Patterns
- 가짜 메시지 feeder는 daemon thread + sleep 루프.
- cfg dict는 최소 키만 (`ui.opacity`, `width`, `height`, `max_messages`, `font_size`) 채우면 띄울 수 있음.

## Dependencies

### Internal
- `src.ui.overlay.run_overlay`, `SettingsPanel`

### External
- `PyQt6`

<!-- MANUAL: -->
