"""
오버레이용 Windows API 헬퍼 (ctypes).

- 화면 캡처 제외: SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE). 채팅창이 게임 위에
  있어도 캡처 프로세스(mss)·녹화 프로그램 화면에 찍히지 않게 한다. Windows 10 2004+.
- 클릭 통과: WS_EX_TRANSPARENT 를 켜고 끈다. Qt 창 플래그를 바꾸면 네이티브 창이
  다시 만들어져 위치·캡처 제외가 풀리므로, 확장 스타일만 직접 토글한다.
- 전역 단축키: RegisterHotKey(스레드 단축키) + Qt 네이티브 이벤트 필터.
  클릭 통과 중에는 채팅창을 클릭할 수 없으니 단축키가 유일한 해제 수단이다.

모든 함수는 실패해도 예외를 던지지 않고 False 를 돌려준다(오버레이는 계속 동작).
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Callable

from PyQt6.QtCore import QAbstractNativeEventFilter

logger = logging.getLogger("solo_stream.ui")

_IS_WIN = sys.platform.startswith("win")

WDA_NONE = 0x0
WDA_EXCLUDEFROMCAPTURE = 0x11
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WM_HOTKEY = 0x0312
MOD_SHIFT = 0x0004
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000

if _IS_WIN:
    _user32 = ctypes.windll.user32
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]


def set_exclude_from_capture(hwnd: int, exclude: bool) -> bool:
    if not _IS_WIN or not hwnd:
        return False
    ok = bool(_user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE))
    if not ok:
        logger.warning(f"캡처 제외 설정 실패(hwnd={hwnd}, exclude={exclude}) — Windows 10 2004 이상 필요")
    return ok


def remove_dwm_border(hwnd: int) -> bool:
    """Windows 11 이 최상위 창에 그리는 1px 테두리·둥근 모서리를 끈다(투명 오버레이 가장자리 선 제거).
    Windows 10 에서는 해당 속성이 없어 조용히 실패한다."""
    if not _IS_WIN or not hwnd:
        return False
    try:
        dwm = ctypes.windll.dwmapi
        no_round = ctypes.c_int(1)            # DWMWCP_DONOTROUND
        no_color = ctypes.c_uint(0xFFFFFFFE)  # DWMWA_COLOR_NONE
        dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), 33, ctypes.byref(no_round), ctypes.sizeof(no_round))
        dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), 34, ctypes.byref(no_color), ctypes.sizeof(no_color))
        return True
    except Exception:
        return False


def set_click_through(hwnd: int, enabled: bool) -> bool:
    if not _IS_WIN or not hwnd:
        return False
    style = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    if enabled:
        style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
    else:
        style &= ~WS_EX_TRANSPARENT
    _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
    return True


# 가상 키 코드(영문 대문자 = ASCII)
def _vk(letter: str) -> int:
    return ord(letter.upper())


class GlobalHotkeys(QAbstractNativeEventFilter):
    """Ctrl+Shift+<키> 전역 단축키. register() 성공 여부를 돌려준다.

    QApplication.installNativeEventFilter(instance) 로 설치해야 동작한다.
    """

    def __init__(self):
        super().__init__()
        self._handlers: dict[int, Callable[[], None]] = {}
        self._next_id = 0xB100

    def register(self, letter: str, handler: Callable[[], None]) -> bool:
        if not _IS_WIN:
            return False
        hk_id = self._next_id
        self._next_id += 1
        ok = bool(_user32.RegisterHotKey(None, hk_id, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, _vk(letter)))
        if ok:
            self._handlers[hk_id] = handler
        else:
            logger.warning(f"전역 단축키 Ctrl+Shift+{letter.upper()} 등록 실패 — 다른 프로그램이 사용 중일 수 있음")
        return ok

    def unregister_all(self):
        if not _IS_WIN:
            return
        for hk_id in list(self._handlers):
            _user32.UnregisterHotKey(None, hk_id)
        self._handlers.clear()

    def nativeEventFilter(self, event_type, message):
        try:
            if event_type in (b"windows_dispatcher_MSG", b"windows_generic_MSG"):
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam in self._handlers:
                    self._handlers[msg.wParam]()
                    return True, 0
        except Exception:
            logger.exception("단축키 처리 실패")
        return False, 0
