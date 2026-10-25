<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-10-14 -->

# ui

## Purpose
PyQt6 기반 투명 오버레이 채팅창 + 별도 설정 창. 오버레이는 Frameless / Always-on-top / Tool window 로 게임 위에 떠 있고, 상단바(LIVE·시작/일시정지·설정·닫기)와 하단바(투명도 슬라이더·[배경 있음|글자만]·클릭 통과)를 가진다. 설정은 왼쪽 메뉴(방송 준비/오버레이/음성 인식/시청자 봇/AI 모델·키/게임·프로필/사용량)가 있는 별도 창이며 모든 항목이 바꾸는 즉시 자동 저장된다.

## Key Files
| File | Description |
|------|-------------|
| `overlay.py` | `ChatOverlay`(채팅 목록·드래그/리사이즈·우클릭 메뉴·투명도/글자만/클릭 통과/캡처 제외) + `run_overlay(config, ui_queue, settings_queue, onboarding)` 진입점(전역 단축키 등록 포함). 설정 창이 호출하는 공개 메서드: `set_transparency`, `set_click_through`, `set_fade`, `set_show_stt`, `set_typing_indicator`, `set_font_size`, `set_max_messages`, `set_capture_exclusion`, `toggle_chat`. |
| `settings.py` | `SettingsWindow`. 페이지마다 `_build_page_*` 빌더. 저장은 `_save(section, values)`(config.local.yaml + 인메모리 config), 실행 중 반영은 `self._queue.put(...)`, 재시작 필요 항목은 `_need_restart("이유")` → 하단 바에 '재시작하면 적용' + 버튼. 모델 후보 목록(`_VISION_MODELS`/`_CHAT_MODELS`/`_STT_MODELS`/`_PROFILE_MODELS`)과 STT 실측 수치(`_STT_MEASURED`)도 여기. '사용량' 탭은 실제 사용(`set_usage(source, snap)` ← 오버레이가 `type=usage` 시스템 메시지를 전달)과 예상 비용(`pricing.estimate_costs`)을 함께 보여주고, 실제 사용이 들어오면 메뉴 배지가 누적액으로 바뀐다. |
| `theme.py` | 색 토큰(`ACCENT`, `SURFACE` …), 폰트(`Malgun Gothic`/`Consolas`), QPainter 선 아이콘(`icon(name)`), 설정 창 공용 QSS(`settings_qss()`), 재사용 위젯(`Switch`, `Segmented`, `LevelMeter`, `StatusBadge`, `Chip`, `Card`, `row_box`, `combo`). |
| `winapi.py` | ctypes Windows API: `set_exclude_from_capture`(WDA_EXCLUDEFROMCAPTURE), `set_click_through`(WS_EX_TRANSPARENT 토글), `remove_dwm_border`(Win11 테두리 제거), `GlobalHotkeys`(RegisterHotKey + 네이티브 이벤트 필터). 실패해도 예외 없이 False. |
| `workers.py` | QThread 워커: `MicLevelWorker`(입력 레벨), `STTTestWorker`(4초 녹음→받아쓰기), `KeyCheckWorker`(키 연결 확인, 과금 없음), `CapturePreviewWorker`(캡처 미리보기). `start_worker()` 로 실행. |
| `helpers.py` | 순수 함수: `list_windows`(win32gui + 제외 패턴 `%foo%`/`foo%`/`%foo`/`foo`), `extract_game_name`, `profile_filename`, `parse_device`. |

## For AI Agents

### Working In This Directory
- 새 설정 항목 추가: `settings.py` 의 해당 `_build_page_*` 에 위젯 + 저장 핸들러(`self._save(...)`). 실행 중 반영이 필요하면 `self._queue.put({...})` 후 `src/main.py` 받는 프로세스의 settings_queue 루프에 elif 추가. 오버레이 표시 옵션이면 `ChatOverlay` 에 `set_*` 메서드를 만들고 설정 창에서 호출.
- **적용 버튼을 만들지 않는다** — 바꾸는 즉시 저장이 원칙. 예외: 비용이 드는 동작(게임 창 적용 → 프로필 생성, 프로필 다시 만들기, 받아쓰기 테스트, 키 저장)과 여러 줄 편집(화면 분석 프롬프트 대화상자)만 명시적 버튼.
- SDK import 는 UI 에 두지 않는다 — 키 확인은 `src/ai/model_client.check_api_key`, `src/stt/transcriber.check_openai_key` 를 워커에서 호출.
- 키 없는 provider 의 모델도 고를 수 있다(저장됨). AI 프로세스는 키가 생길 때까지 `resolve_provider` 로 있는 키로 대체해 돌고, 설정 'AI 모델' 카드가 행마다 `_refresh_model_warns` 로 '⚠ X 키가 없어요 — Y로 대체' 경고와 목록의 '(키 없음)' 표시를 띄운다(키 저장·모델 변경 때 갱신). 비슷한 '키 필요' 선택지를 추가하면 같은 방식으로 경고한다. 모델 키 이름은 직접 조립하지 말고 `model_client.model_config_key` / `selected_model` 을 쓴다.
- '시청자 봇' 페이지: `persona_loader.persona_entries` 목록(켜기 스위치·편집·'+ 새 봇') → 바꾸면 즉시 `save_persona_entries` + `{"personas_reload": True}`. 편집은 `_BotDialog`(주요 항목 + 접힌 '고급': 기억 개수·반응 확률·별칭, 화면에 없는 필드는 보존). 기본 봇은 이름 변경 불가·'기본값으로 되돌리기', 내가 만든 봇만 삭제. 마지막 한 명은 끌 수 없다. 예전 버전 봇 파일(`stale_persona_files`)·기본값이 바뀐 고친 봇(`outdated`, 칩 '기본값 바뀜')이 있으면 페이지 위 경고 배너 + 메뉴 '시청자 봇' 옆 ⚠, 'v{버전} 기본 봇으로 초기화'(`reset_personas`, 확인 대화상자) 버튼은 배너와 페이지 아래 둘 다.
- '고인물 봇 웹 검색' 스위치('AI 모델' 카드) → `chat.web_search` 저장 + `{"web_search": bool}` 브로드캐스트(AI 프로세스 즉시 반영).
- 분석 주기(`capture.interval_seconds`)는 'AI 모델' 카드의 스핀박스(5~60초)로 바꾸고 `{"interval_seconds"}` 로 캡처·AI 프로세스에 바로 반영된다. 15초 미만이면 예상 비용 증가 경고. 주기를 화면에 쓰는 문구는 `_interval_text` 로 만든다(숫자 하드코딩 금지).
- 클릭 통과는 Ctrl+Shift+O 가 유일한 해제 수단이다. 단축키 등록에 실패하면(`hotkeys_ok=False`) 클릭 통과를 켤 수 없게 막아 둔다 — 이 가드를 없애지 말 것.
- 클릭 통과·캡처 제외는 Qt 창 플래그가 아니라 winapi 로 확장 스타일/디스플레이 속성만 바꾼다(Qt 플래그 변경은 네이티브 창을 다시 만들어 설정이 풀린다).
- 채팅 메시지: `_poll_queue` 가 100ms 마다 `ui_queue` drain → `_handle_message`. `system` 키 = 프로필 로드·`type=typing`(라운드별 '입력 중' on/off — 채팅 목록 아래 별도 줄, 기록에 안 남음, 겹친 라운드가 모두 끝나야 꺼지고 45초 지나면 자동 해제) 등, `stt_transcript` = 내 말(설정 켜졌을 때만), 그 외 `{name, message}` 채팅(`name == "시스템"` 이면 시스템 줄). 본문은 `html.escape` 후 RichText 라벨.
- 줄 높이는 `QListWidgetItem.setSizeHint` + `lbl.heightForWidth` 로 정한다. 높이를 재기 전에 `ensurePolished()` 를 불러야 스타일시트 폰트가 반영돼 줄이 겹치지 않는다. 창 크기가 바뀌면 `_relayout_messages` 가 다시 잰다.
- `extract_game_name`(helpers.py)은 `src/main.py::_extract_game_name` 과 동일 구현 — 한쪽 정규식을 고치면 다른 쪽도 같이.

### Testing Requirements
- `tests/ui/smoke_settings_tabs.py` — 가짜 게임 화면 위에 오버레이를 띄워 투명도 단계·메시지 종류·캡처 제외와 설정 7페이지를 실제 화면 캡처 PNG 로 남긴다(API 호출 없음, 몇 초간 창이 뜬다). `QWidget.render()` 는 그래픽 효과·반투명 합성을 실제와 다르게 그려 쓰지 않는다.
- `tests/ui/test_ui.py` — 가짜 메시지를 흘려 넣는 수동 확인용.
- 최종 확인은 `run.bat` 로 실제 실행.

### Common Patterns
- 다크 톤 토큰은 `theme.py` 에서만 정의. 설정 창은 루트에 `settings_qss()` 한 번 + objectName 셀렉터(`#card`, `#lbl`, `#sub`, `#btn`, `#primary` …).
- 아이콘은 이모지 대신 `theme.icon(name, color, size)`.
- 투명도: `ui.transparency`(0~100, 100=글자만). 60 이상이면 줄마다 옅은 받침 + 그림자. 없으면 옛 `ui.opacity` 에서 환산(`transparency_from_config`).

## Dependencies

### Internal
- `src/utils/config_loader` 의 config 구조를 그대로 받아 `ChatOverlay` → `SettingsWindow` 에 전달.
- `settings_queue` (= main 의 `broadcast_queue`)로 모든 프로세스에 설정 변경 전파.
- `src/utils/paths` — `update_user_config`, `save_last_session`, `restart_app`, `user_data_dir`.

### External
- `PyQt6` — QtCore/QtGui/QtWidgets
- `PyYAML` — 프로필 목록 이름 읽기
- `pywin32` (`win32gui`) — 캡처 가능한 창 enumeration
- `ctypes` (user32/dwmapi) — 캡처 제외·클릭 통과·전역 단축키

<!-- MANUAL: -->
