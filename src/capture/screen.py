"""mss 기반 화면 캡처 모듈. 창모드 우선 지원."""
import io
from typing import Optional

import mss
import mss.tools
from PIL import Image


def list_windows() -> list[dict]:
    """현재 열린 윈도우 목록 반환 (win32 필요)."""
    try:
        import win32gui

        windows = []

        def callback(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title:
                    results.append({"hwnd": hwnd, "title": title})

        win32gui.EnumWindows(callback, windows)
        return windows
    except ImportError:
        return []


def get_window_rect(title_keyword: str) -> Optional[tuple[int, int, int, int]]:
    """
    타이틀에 keyword를 포함하는 창의 (left, top, right, bottom) 반환.
    찾지 못하면 None.
    """
    try:
        import win32gui

        result = None

        def callback(hwnd, _):
            nonlocal result
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title_keyword.lower() in title.lower():
                    rect = win32gui.GetWindowRect(hwnd)
                    result = rect

        win32gui.EnumWindows(callback, None)
        return result
    except ImportError:
        return None


def capture_screen(
    target_window: Optional[str] = None,
    resize_width: int = 1024,
) -> bytes:
    """
    화면(또는 특정 창)을 캡처하여 JPEG bytes로 반환.
    target_window가 None이면 주 모니터 전체 캡처.
    """
    with mss.mss() as sct:
        if target_window:
            rect = get_window_rect(target_window)
            if rect:
                left, top, right, bottom = rect
                monitor = {
                    "left": left,
                    "top": top,
                    "width": right - left,
                    "height": bottom - top,
                }
            else:
                monitor = sct.monitors[1]
        else:
            monitor = sct.monitors[1]  # 주 모니터

        screenshot = sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

    # 비율 유지하며 리사이즈
    orig_w, orig_h = img.size
    if orig_w > resize_width:
        ratio = resize_width / orig_w
        new_size = (resize_width, int(orig_h * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
