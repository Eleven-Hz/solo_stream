<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# ui

## Purpose
PyQt6 기반 투명 오버레이 채팅창 + 설정 패널. Frameless / Always-on-top / Tool window 플래그로 게임 위에 떠 있으며, 드래그 이동·우하단 리사이즈·우클릭 컨텍스트 메뉴(투명도/지우기/종료)·시작·정지 토글·⚙ 설정 탭(게임/AI 모델/음성)을 제공한다.

## Key Files
| File | Description |
|------|-------------|
| `overlay.py` | 전부 한 파일. `ChatOverlay`(메인 위젯, 드래그/리사이즈/페인트), `TitleBar`(시작·정지 토글 + ⚙/✕), `SettingsPanel`(3탭 QTabWidget: 게임/AI 모델/음성, 각 섹션은 `_build_section_*` 메서드로 분리). `_list_windows`로 win32gui enumeration + 제외 패턴 필터(`%foo%` 포함 / `foo%` 시작 / `%foo` 끝 / `foo` 완전 일치). `run_overlay(config, ui_queue, settings_queue)`가 진입점. |
| `__init__.py` | 패키지 마커 |

## For AI Agents

### Working In This Directory
- `overlay.py`는 한 파일에 ~1380줄로 크지만, **분리하지 말 것** — UI 프로세스는 자체 PyQt6 이벤트 루프이고, 위젯 간 시그널·슬롯이 `_settings_queue`로 떨어지는 흐름이 한 눈에 보여야 디버그가 쉽다.
- 새 설정 항목 추가 절차:
  1. `SettingsPanel._build_section_*` 메서드 안에서 위젯 생성 + 디스크 저장 핸들러 작성 (저장 패턴은 `_save_config_*` 참조 — yaml.safe_load → 수정 → yaml.dump).
  2. `apply` 버튼에서 `self._settings_queue.put({...})` 로 브로드캐스트.
  3. `src/main.py`의 해당 프로세스에서 `settings_queue.empty()` 루프에 elif 추가.
- 채팅 메시지는 `_poll_queue`가 100ms 타이머로 `ui_queue`를 drain — `system` 키가 있으면 시스템 메시지(예: 프로필 로드 완료), 아니면 `{name, message}` 일반 채팅.
- 한국어/이모지·이모티콘 폭 계산 때문에 `QListWidgetItem.setSizeHint(label.sizeHint())` 후 `setItemWidget` 패턴 사용 — wordwrap 라벨이 행 높이를 결정하게 함.
- `_extract_game_name`은 `src/main.py`에도 동일 구현이 있다 (UI/AI 프로세스 격리). 한쪽 정규식 수정 시 다른 쪽도 같이 수정.

### Testing Requirements
- `tests/ui/test_ui.py` — 채팅 스크롤 동작 (fake 메시지 30개 feed).
- `tests/ui/preview_settings.py` — 설정 패널 단독 띄우기.
- `tests/ui/smoke_settings_tabs.py` — 탭 전환 스모크.

### Common Patterns
- 모든 위젯에 인라인 stylesheet (다크 테마, `#1e1e2e`/`#1a1a25` 계열). 공용 스타일은 `_btn_style(bg, hover)` 헬퍼.
- 색상 닉네임 매핑: `_name_color(name)` — 이름의 ord 합 % 팔레트 길이.
- `WA_TranslucentBackground` + `paintEvent`에서 우하단 리사이즈 헤어라인 직접 그림.

## Dependencies

### Internal
- `src/utils/config_loader` 의 config 구조를 그대로 받아 `SettingsPanel` 에 전달.
- `_settings_queue` (= main의 `broadcast_queue`)로 모든 프로세스에 설정 변경 전파.

### External
- `PyQt6` — QtCore/QtGui/QtWidgets
- `PyYAML` — `_save_*` 핸들러에서 config.yaml in-place 갱신
- `pywin32` (`win32gui`) — 캡처 가능한 창 enumeration

<!-- MANUAL: -->
