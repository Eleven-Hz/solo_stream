"""UI 프로세스 공용 순수 함수 (창 목록, 게임명 추출, 장치 문자열 파싱)."""
from __future__ import annotations

import re


def match_exclude_pattern(title: str, pattern: str) -> bool:
    """
    제외 패턴 매칭.
    - %단어%  → 창 제목에 '단어' 포함 여부 (대소문자 무시)
    - 단어%   → 창 제목이 '단어'로 시작
    - %단어   → 창 제목이 '단어'로 끝남
    - 단어    → 완전 일치
    """
    title_l = title.lower()
    p = pattern.strip()
    starts_wild = p.startswith("%")
    ends_wild = p.endswith("%")
    core = p.strip("%").lower()

    if starts_wild and ends_wild:
        return core in title_l
    elif ends_wild:
        return title_l.startswith(core)
    elif starts_wild:
        return title_l.endswith(core)
    else:
        return title_l == core


def list_windows(excludes: list[str] | None = None) -> list[str]:
    """현재 열린 창 목록 반환. excludes 패턴에 해당하는 창은 제외."""
    try:
        import win32gui
        windows = []

        def cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd)
                if t and len(t) > 1:
                    windows.append(t)
        win32gui.EnumWindows(cb, None)
        result = sorted(set(windows))
        if excludes:
            result = [
                w for w in result
                if not any(match_exclude_pattern(w, pat) for pat in excludes)
            ]
        return result
    except Exception:
        return []


def extract_game_name(window_title: str) -> str:
    """창 타이틀에서 게임명 추출. src/main.py::_extract_game_name 과 동일 구현 —
    한쪽 정규식을 고치면 다른 쪽도 같이 고친다 (UI/AI 프로세스가 서로 import 하지 않음)."""
    name = window_title or ""
    name = re.sub(r"\s*[-–]\s*Steam.*", "", name)
    name = re.sub(r"\bMin\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\bv\d[\d.]*[-\w]*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "Unknown Game"


def profile_filename(game_name: str) -> str:
    """game_profile_loader 의 저장 파일명 규칙과 동일한 안전한 stem."""
    return re.sub(r'[\\/:*?"<>|]', "", game_name).strip().lower().replace(" ", "_")[:64]


def parse_device(device: str):
    """STT device 설정 문자열을 sounddevice 인자로 변환.
    'default'/'' → None, 숫자 문자열 → int 인덱스, 그 외 → 이름 문자열."""
    d = (device or "").strip()
    if not d or d.lower() == "default":
        return None
    try:
        return int(d)
    except ValueError:
        return d
