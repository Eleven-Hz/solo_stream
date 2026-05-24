<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# capture

## Purpose
화면 캡처 + 변화 감지. `mss`로 모니터/특정 창을 JPEG bytes로 캡처하고, `scikit-image`의 SSIM으로 직전 프레임과 비교해 유사하면 Vision API 호출을 스킵한다 (목표 스킵률 50~60%, API 비용 절감의 핵심).

## Key Files
| File | Description |
|------|-------------|
| `screen.py` | `capture_screen(target_window, resize_width)` — mss로 캡처해 JPEG bytes + 캡처 대상 설명 반환. `get_window_rect`는 win32gui로 창 좌표 조회, 창이 주 모니터를 완전히 덮으면 fullscreen으로 간주해 모니터 전체 캡처. `list_windows`로 enumeration. |
| `change_detect.py` | `has_significant_change(prev, curr, threshold)` — 두 JPEG를 grayscale로 변환 후 SSIM 계산, threshold 미만이면 True (변화 있음). 해상도가 다르면 작은 쪽에 맞춰 crop. |
| `__init__.py` | 패키지 마커 |

## For AI Agents

### Working In This Directory
- `capture_screen`은 (bytes, str) 튜플 반환 — 두 번째 값은 사람이 읽는 캡처 대상 설명(`"창 'CrossCode' (1280x720)"`)으로 로그에 그대로 쓰인다.
- 풀스크린 게임 지원은 현재 "창이 모니터 전체를 덮는지" 휴리스틱으로 판단한다. 진짜 fullscreen exclusive 모드(DXGI/WGC)는 미구현 — 기획서 §10 열린 질문에 명시되어 있음.
- `resize_width`는 비율 유지 리사이즈 (LANCZOS). JPEG quality=85 고정 — Vision API 토큰 비용 균형점.

### Testing Requirements
- `tests/vision/run.py`가 영상 프레임을 SSIM으로 샘플링해서 vision 호출 (실서비스와 동일 경로).

### Common Patterns
- win32gui는 ImportError를 try로 감싸 다른 OS에서 빈 결과를 반환 — 실제 동작 보장은 Windows.
- SSIM은 grayscale 단일채널로만 계산 (RGB 3채널 대비 3배 빠름, 게임 UI 변화 감지에 충분).

## Dependencies

### Internal
- `src/main.py`의 `capture_process`가 직접 호출. UI에서 변경되는 `target_window` 설정을 settings_queue로 받는다.

### External
- `mss` — 화면 캡처
- `PIL` (Pillow) — JPEG 인/디코드 + 리사이즈
- `scikit-image.metrics.structural_similarity` — SSIM
- `numpy` — 배열
- `pywin32` (`win32gui`) — 창 enumeration / GetWindowRect

<!-- MANUAL: -->
