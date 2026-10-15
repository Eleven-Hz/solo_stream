"""
PyQt6 기반 투명 채팅 오버레이 (게임 위에 뜨는 채팅창).

- Always-on-top / 드래그 이동 / 우하단 리사이즈
- 상단바: LIVE 상태 · 시작/일시정지 · 설정 · 닫기
- 하단바: 투명도 슬라이더 · [배경 있음|글자만] · 클릭 통과
- 투명도 100% = 글자만 모드(배경 없음 + 글자 외곽 그림자). 이 모드에선 마우스를
  올렸을 때만 상단/하단바가 보인다.
- 클릭 통과: 채팅창 위 클릭이 게임으로 전달. 해제는 Ctrl+Shift+O (전역 단축키).
- 화면 캡처 제외: 채팅창이 Vision 캡처에 찍혀 봇이 자기 채팅을 읽는 것을 막는다.

설정 창은 별도 창(settings.py)이며, 오버레이의 공개 메서드(set_transparency 등)를
직접 호출해 즉시 반영한다.
"""
from __future__ import annotations

import html
import logging
from multiprocessing import Queue

from PyQt6.QtCore import QPoint, QPropertyAnimation, QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from src.ui import theme as T
from src.ui import winapi
from src.utils import paths

logger = logging.getLogger("solo_stream.ui")

# 투명도(%) 이상이면 글자에 외곽 그림자를 넣는다(밝은 게임 화면 위 가독성).
_SHADOW_FROM = 60
# 최신 메시지에서 몇 줄 떨어지면 흐리게 할지(구간별 밝기).
_FADE_STEPS = ((6, 1.0), (14, 0.8), (10 ** 9, 0.6))


def _fade_level(distance_from_bottom: int) -> float:
    for limit, level in _FADE_STEPS:
        if distance_from_bottom < limit:
            return level
    return _FADE_STEPS[-1][1]


def _mix(color: str, toward: str, amount: float) -> str:
    """color 를 toward 쪽으로 amount(0~1)만큼 섞은 hex."""
    a, b = QColor(color), QColor(toward)
    r = round(a.red() + (b.red() - a.red()) * amount)
    g = round(a.green() + (b.green() - a.green()) * amount)
    bl = round(a.blue() + (b.blue() - a.blue()) * amount)
    return QColor(r, g, bl).name()


def transparency_from_config(ui_cfg: dict) -> int:
    """ui.transparency(0~100). 없으면 옛 ui.opacity(배경 불투명도 0~1)에서 환산."""
    if "transparency" in ui_cfg:
        return int(max(0, min(100, int(ui_cfg["transparency"]))))
    opacity = float(ui_cfg.get("opacity", 0.85))
    return int(round((1.0 - opacity) * 100 / 5) * 5)


class _IconButton(QPushButton):
    def __init__(self, icon_name: str, tooltip: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setIconSize(QSize(15, 15))
        self.setStyleSheet(
            "QPushButton { border: none; border-radius: 6px; background: transparent; }"
            "QPushButton:hover { background: rgba(255,255,255,20); }"
        )
        self.set_icon(icon_name)

    def set_icon(self, icon_name: str, color: str = T.TEXT_2):
        self.setIcon(T.icon(icon_name, color, 15))


class ChatOverlay(QWidget):
    RESIZE_MARGIN = 14

    def __init__(self, config: dict, ui_queue: Queue, settings_queue: Queue,
                 onboarding: bool = False, hotkeys_ok: bool = True):
        super().__init__()
        self._config = config.setdefault("ui", {})
        self._config_full = config
        self._ui_queue = ui_queue
        self._settings_queue = settings_queue
        self._onboarding = onboarding
        self._hotkeys_ok = hotkeys_ok

        self._drag_pos = QPoint()
        self._resizing = False
        self._resize_start_pos = QPoint()
        self._resize_start_size = QSize()

        self._chat_active = False
        self._hovered = False
        self._click_through = False
        self._transparency = transparency_from_config(self._config)
        self._fade = bool(self._config.get("fade_old", True))
        self._show_stt = bool(self._config.get("show_stt_history", False))
        self._exclude_capture = bool(self._config.get("exclude_from_capture", True))

        # 슬라이더를 끄는 동안 매 틱마다 yaml 을 쓰지 않도록 저장은 잠깐 미룬다.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_transparency)
        # 리사이즈 중에는 메시지 줄바꿈 재계산을 묶어서 한 번만.
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(60)
        self._relayout_timer.timeout.connect(self._relayout_messages)

        self._setup_window()
        self._setup_ui()
        self._apply_transparency()
        self._update_chat_state_ui()
        self._setup_timer()

        # 설정 창(별도 창). 프로필 상태 통지가 시작 직후 오므로 미리 만들어 둔다.
        from src.ui.settings import SettingsWindow
        self._settings = SettingsWindow(settings_queue, config, overlay=self, onboarding=onboarding)

        if self._onboarding:
            # 첫 실행(키 없음)은 키 입력 화면으로 바로 연다.
            QTimer.singleShot(0, lambda: self.open_settings("ai"))

    # ── 창/레이아웃 ──────────────────────────────────────────────────────────

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(260, 240)
        self.setMouseTracking(True)
        self.resize(int(self._config.get("width", 380)), int(self._config.get("height", 640)))

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._container = QFrame()
        self._container.setObjectName("container")
        inner = QVBoxLayout(self._container)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        # ── 상단바
        self._head = QWidget()
        self._head.setObjectName("ovhead")
        self._head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._head.setFixedHeight(44)
        hl = QHBoxLayout(self._head)
        hl.setContentsMargins(12, 0, 8, 0)
        hl.setSpacing(8)

        self._live_chip = QLabel()
        self._live_chip.setFixedHeight(20)
        hl.addWidget(self._live_chip)
        title = QLabel("Solo Stream")
        title.setStyleSheet(f"color: {T.TEXT}; font-size: 13px; font-weight: 600; background: transparent;")
        title.setFont(T.font(13, 600))
        hl.addWidget(title)
        self._profile_lbl = QLabel("")
        self._profile_lbl.setStyleSheet(f"color: {T.MUTED}; background: transparent;")
        self._profile_lbl.setFont(T.font(11))
        self._profile_lbl.setMinimumWidth(0)
        hl.addWidget(self._profile_lbl, 1)

        self._warn_btn = _IconButton("warn", "")
        self._warn_btn.set_icon("warn", T.WARN)
        self._warn_btn.setVisible(False)
        self._warn_btn.clicked.connect(lambda: self.open_settings("game"))
        hl.addWidget(self._warn_btn)

        self._play_btn = _IconButton("play", "채팅 시작 (Ctrl+Shift+S)")
        self._play_btn.clicked.connect(self.toggle_chat)
        hl.addWidget(self._play_btn)
        gear = _IconButton("gear", "설정 열기")
        gear.clicked.connect(lambda: self.open_settings())
        hl.addWidget(gear)
        close = _IconButton("close", "종료")
        close.clicked.connect(QApplication.quit)
        hl.addWidget(close)
        inner.addWidget(self._head)

        # ── 클릭 통과 중 안내(상단바 자리를 대신함)
        self._ct_bar = QWidget()
        self._ct_bar.setFixedHeight(44)
        cl = QHBoxLayout(self._ct_bar)
        cl.setContentsMargins(10, 0, 10, 0)
        cl.addStretch(1)
        ct_chip = QLabel("클릭 통과 중 · Ctrl+Shift+O 로 해제")
        ct_chip.setFont(T.font(11, 500))
        ct_chip.setStyleSheet(
            f"color: {T.ACCENT_HI}; background: rgba(10,11,14,190); border: 1px solid {T.rgba(T.ACCENT, 0.35)}; "
            "border-radius: 13px; padding: 4px 10px;"
        )
        cl.addWidget(ct_chip)
        self._ct_bar.setVisible(False)
        inner.addWidget(self._ct_bar)

        # ── 채팅 목록
        self._list = QListWidget()
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setStyleSheet(
            "QListWidget { background: transparent; border: none; outline: none; padding: 6px 10px 8px 12px; }"
            "QListWidget::item { background: transparent; }"
            "QScrollBar:vertical { width: 4px; background: transparent; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,50); border-radius: 2px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { height: 0; background: none; }"
        )
        self._list.setWordWrap(True)
        self._list.setSpacing(2)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._list.viewport().setMouseTracking(True)
        inner.addWidget(self._list, 1)

        # ── 하단바
        self._foot = QWidget()
        self._foot.setObjectName("ovfoot")
        self._foot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fl = QVBoxLayout(self._foot)
        fl.setContentsMargins(14, 10, 12, 12)
        fl.setSpacing(10)

        r1 = QHBoxLayout()
        r1.setSpacing(12)
        op_lbl = QLabel("투명도")
        op_lbl.setFont(T.font(12))
        op_lbl.setStyleSheet("color: #A3A9B6; background: transparent;")
        r1.addWidget(op_lbl)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setSingleStep(5)
        self._slider.setPageStep(10)
        self._slider.setValue(self._transparency)
        self._slider.setAccessibleName("투명도")
        self._slider.setStyleSheet(
            "QSlider { background: transparent; }"
            "QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,40); border-radius: 2px; }"
            f"QSlider::sub-page:horizontal {{ background: {T.ACCENT}; border-radius: 2px; }}"
            "QSlider::handle:horizontal { width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: #fff; }"
        )
        self._slider.valueChanged.connect(lambda v: self.set_transparency(v, save=True))
        r1.addWidget(self._slider, 1)
        self._op_val = QLabel()
        self._op_val.setFont(T.font(12, 500, mono=True))
        self._op_val.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self._op_val.setFixedWidth(38)
        self._op_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        r1.addWidget(self._op_val)
        fl.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(8)
        self._mode_seg = T.Segmented([("panel", "배경 있음"), ("text", "글자만")], compact=True)
        self._mode_seg.changed.connect(self._on_mode_changed)
        r2.addWidget(self._mode_seg)
        r2.addStretch(1)
        self._ct_btn = QPushButton("  클릭 통과")
        self._ct_btn.setIcon(T.icon("cursor", T.TEXT_2, 13))
        self._ct_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ct_btn.setFixedHeight(32)
        self._ct_btn.setFont(T.font(12, 500))
        self._ct_btn.setStyleSheet(
            f"QPushButton {{ border: 1px solid rgba(255,255,255,30); border-radius: 8px; padding: 0 12px;"
            f" background: transparent; color: {T.TEXT_2}; }}"
            "QPushButton:hover { background: rgba(255,255,255,15); }"
        )
        self._ct_btn.setToolTip("채팅창 위 클릭을 게임으로 넘깁니다. Ctrl+Shift+O 로 켜고 끕니다."
                                if self._hotkeys_ok else
                                "Ctrl+Shift+O 단축키를 등록하지 못해 사용할 수 없습니다(해제 수단이 없음).")
        self._ct_btn.setEnabled(self._hotkeys_ok)
        self._ct_btn.clicked.connect(lambda: self.set_click_through(True))
        r2.addWidget(self._ct_btn)
        fl.addLayout(r2)
        inner.addWidget(self._foot)

        outer.addWidget(self._container)

        # 글자만 모드에서 마우스가 없으면 상/하단바를 숨기기 위한 투명도 효과
        self._head_fx = QGraphicsOpacityEffect(self._head)
        self._head_fx.setOpacity(1.0)
        self._head.setGraphicsEffect(self._head_fx)
        self._foot_fx = QGraphicsOpacityEffect(self._foot)
        self._foot_fx.setOpacity(1.0)
        self._foot.setGraphicsEffect(self._foot_fx)
        self._anims: list[QPropertyAnimation] = []

        # 글자 외곽 그림자(투명도 높을 때만 켬)
        self._shadow = QGraphicsDropShadowEffect(self._list.viewport())
        self._shadow.setBlurRadius(8)
        self._shadow.setOffset(0, 1)
        self._shadow.setColor(QColor(0, 0, 0, 235))
        self._shadow.setEnabled(self._transparency >= _SHADOW_FROM)
        self._list.viewport().setGraphicsEffect(self._shadow)

    # ── 투명도 / 모드 ────────────────────────────────────────────────────────

    def transparency(self) -> int:
        return self._transparency

    def set_transparency(self, value: int, save: bool = True):
        value = int(max(0, min(100, value)))
        if value == self._transparency and self._slider.value() == value:
            return
        self._transparency = value
        if self._slider.value() != value:
            self._slider.blockSignals(True)
            self._slider.setValue(value)
            self._slider.blockSignals(False)
        self._apply_transparency()
        if save:
            self._save_timer.start()
        settings = getattr(self, "_settings", None)
        if settings is not None:
            settings.sync_transparency(value)

    def _on_mode_changed(self, mode: str):
        if mode == "text":
            self._last_panel_transparency = self._transparency if self._transparency < 100 else 15
            self.set_transparency(100)
        else:
            self.set_transparency(getattr(self, "_last_panel_transparency", 15) if self._transparency >= 100
                                  else self._transparency)

    def _apply_transparency(self):
        t = self._transparency
        text_only = t >= 100
        bg_alpha = (1.0 - t / 100.0) * 0.94
        # 완전 투명(알파 0) 픽셀은 마우스가 통과해 버려 드래그/호버가 안 되므로 최소 알파를 둔다.
        bg_alpha_i = max(2, int(bg_alpha * 255))
        border = "none" if t >= 95 else "1px solid rgba(255,255,255,20)"
        self._container.setStyleSheet(
            f"QFrame#container {{ background-color: rgba(16,17,21,{bg_alpha_i}); "
            f"border-radius: 12px; border: {border}; }}"
        )
        bar_alpha = 0.55 if text_only else max(bg_alpha, 0.62)
        bar = f"rgba(10,11,14,{int(bar_alpha * 255)})"
        self._head.setStyleSheet(
            f"QWidget#ovhead {{ background: {bar}; border-top-left-radius: 12px; border-top-right-radius: 12px;"
            " border-bottom: 1px solid rgba(255,255,255,15); }"
        )
        self._foot.setStyleSheet(
            f"QWidget#ovfoot {{ background: {bar}; border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;"
            " border-top: 1px solid rgba(255,255,255,15); }"
        )
        self._op_val.setText(f"{t}%")
        self._mode_seg.set_value("text" if text_only else "panel")
        if self._shadow.isEnabled() != (t >= _SHADOW_FROM):
            self._shadow.setEnabled(t >= _SHADOW_FROM)
            self._restyle_all(refit=True)
        self._update_chrome(animate=False)

    def _save_transparency(self):
        try:
            paths.update_user_config("ui", {"transparency": self._transparency})
            self._config["transparency"] = self._transparency
        except Exception as e:
            logger.error(f"투명도 저장 실패: {e}")

    def _update_chrome(self, animate: bool = True):
        """글자만 모드에서는 마우스를 올렸을 때만 상/하단바를 보인다."""
        show = (not self._transparency >= 100) or self._hovered or not self._chat_active
        target = 1.0 if show else 0.0
        for fx in (self._head_fx, self._foot_fx):
            if abs(fx.opacity() - target) < 0.01:
                continue
            if not animate:
                fx.setOpacity(target)
                continue
            anim = QPropertyAnimation(fx, b"opacity", self)
            anim.setDuration(160)
            anim.setStartValue(fx.opacity())
            anim.setEndValue(target)
            anim.start()
            self._anims = [a for a in self._anims if a.state() == QPropertyAnimation.State.Running] + [anim]

    def enterEvent(self, event):
        self._hovered = True
        self._update_chrome()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._update_chrome()
        super().leaveEvent(event)

    # ── 클릭 통과 / 캡처 제외 ─────────────────────────────────────────────────

    def is_click_through(self) -> bool:
        return self._click_through

    def click_through_available(self) -> bool:
        return self._hotkeys_ok

    def set_click_through(self, enabled: bool):
        if enabled and not self._hotkeys_ok:
            self._add_entry({"kind": "sys", "text": "단축키(Ctrl+Shift+O)를 등록하지 못해 클릭 통과를 켤 수 없습니다."})
            return
        if enabled == self._click_through:
            return
        self._click_through = enabled
        winapi.set_click_through(int(self.winId()), enabled)
        self._head.setVisible(not enabled)
        self._foot.setVisible(not enabled)
        self._ct_bar.setVisible(enabled)
        if enabled:
            self._hovered = False
        settings = getattr(self, "_settings", None)
        if settings is not None:
            settings.sync_click_through(enabled)
        logger.info(f"클릭 통과 {'켬' if enabled else '끔'}")

    def toggle_click_through(self):
        self.set_click_through(not self._click_through)

    def set_capture_exclusion(self, exclude: bool):
        self._exclude_capture = bool(exclude)
        winapi.set_exclude_from_capture(int(self.winId()), self._exclude_capture)
        settings = getattr(self, "_settings", None)
        if settings is not None:
            winapi.set_exclude_from_capture(int(settings.winId()), self._exclude_capture)

    def capture_excluded(self) -> bool:
        return self._exclude_capture

    def showEvent(self, event):
        super().showEvent(event)
        winapi.remove_dwm_border(int(self.winId()))
        winapi.set_exclude_from_capture(int(self.winId()), self._exclude_capture)

    # ── 채팅 시작/정지 ───────────────────────────────────────────────────────

    def is_chat_active(self) -> bool:
        return self._chat_active

    def toggle_chat(self):
        self.set_chat_active(not self._chat_active)

    def set_chat_active(self, active: bool):
        self._chat_active = bool(active)
        self._settings_queue.put({"chat_active": self._chat_active})
        self._update_chat_state_ui()
        settings = getattr(self, "_settings", None)
        if settings is not None:
            settings.sync_chat_active(self._chat_active)

    def _update_chat_state_ui(self):
        if self._chat_active:
            self._live_chip.setText("● LIVE")
            self._live_chip.setStyleSheet(
                f"background: {T.rgba(T.LIVE, 0.16)}; color: #FF7A7E; border-radius: 10px; padding: 0 8px;"
            )
            self._play_btn.set_icon("pause")
            self._play_btn.setToolTip("채팅 일시정지 (Ctrl+Shift+S)")
        else:
            self._live_chip.setText("대기")
            self._live_chip.setStyleSheet(
                f"background: rgba(255,255,255,18); color: {T.MUTED}; border-radius: 10px; padding: 0 8px;"
            )
            self._play_btn.set_icon("play", T.ACCENT)
            self._play_btn.setToolTip("채팅 시작 (Ctrl+Shift+S)")
        self._live_chip.setFont(T.font(11, 600, mono=True))
        self._update_chrome(animate=False)

    # ── 설정 창 ──────────────────────────────────────────────────────────────

    def open_settings(self, page: str | None = None):
        self._settings.open_page(page)

    # ── 메시지 ───────────────────────────────────────────────────────────────

    def _setup_timer(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_queue)
        self._timer.start(100)

    def _poll_queue(self):
        while True:
            try:
                if self._ui_queue.empty():
                    return
                msg = self._ui_queue.get_nowait()
            except Exception:
                return
            try:
                self._handle_message(msg)
            except Exception:
                # 한 항목이 깨져도 나머지 메시지 처리는 계속한다.
                logger.exception(f"UI 메시지 처리 실패: {msg!r}")

    def _handle_message(self, msg):
        if not isinstance(msg, dict):
            return
        if "system" in msg:
            if msg.get("type") == "profile_loaded":
                name = msg.get("name", "")
                self._profile_name = name
                self._elide_profile()
                self._profile_lbl.setToolTip(name)
                self._settings.set_profile_name(
                    name, status=msg.get("status", "ok"), detail=msg.get("detail", ""),
                )
                warning = msg.get("warning")
                self._warn_btn.setVisible(bool(warning))
                self._warn_btn.setToolTip(warning or "")
            elif msg.get("type") == "usage":
                self._settings.set_usage(msg.get("source", ""), msg.get("data") or {})
            return
        if "stt_transcript" in msg:
            if self._show_stt:
                self._add_entry({"kind": "stt", "text": msg.get("text", "")})
            return
        name = msg.get("name", "")
        kind = "sys" if name == "시스템" else "chat"
        self._add_entry({"kind": kind, "name": name, "text": msg.get("message", "")})

    def _entry_html(self, entry: dict, level: float) -> str:
        dim = 1.0 - level
        text = html.escape((entry.get("text") or "").strip())
        if entry["kind"] == "chat":
            nc = _mix(T.name_color(entry["name"]), "#6E7380", dim)
            tc = _mix("#ECEDF1", "#6E7380", dim)
            return (f'<span style="color:{nc}; font-weight:700;">{html.escape(entry["name"])}</span>'
                    f'&nbsp;<span style="color:{tc};">{text}</span>')
        if entry["kind"] == "stt":
            bc = _mix("#A3A9B6", "#6E7380", dim)
            return (f'<span style="color:{bc}; background-color:#2A2D35;">&nbsp;나&nbsp;</span>'
                    f'&nbsp;<span style="color:{bc};">{text}</span>')
        raw = (entry.get("text") or "").strip()
        good = raw.startswith("✓")
        body = html.escape(raw.lstrip("⚠✓ ").strip())
        dot = T.ACCENT if good else T.WARN
        wc = _mix(T.ACCENT_HI if good else "#F5C27A", "#6E7380", dim)
        return f'<span style="color:{dot};">●</span>&nbsp;<span style="color:{wc};">{body}</span>'

    def _label_qss(self, entry: dict) -> str:
        size = int(self._config.get("font_size", 14))
        if entry["kind"] != "chat":
            size = max(9, size - 2)
        base = f"font-family: '{T.FONT_FAMILY}'; font-size: {size}pt;"
        if self._transparency >= _SHADOW_FROM:
            # 배경이 거의 없을 때는 줄마다 옅은 어두운 받침을 깔아 밝은 게임 화면에서도 읽히게 한다.
            return base + " background: rgba(8,9,12,120); border-radius: 6px; padding: 1px 6px;"
        return base + " background: transparent; padding: 0;"

    def _make_label(self, entry: dict, level: float) -> QLabel:
        lbl = QLabel()
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(self._label_qss(entry))
        lbl.setText(self._entry_html(entry, level))
        lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._apply_outline(lbl)
        return lbl

    def _apply_outline(self, lbl: QLabel):
        """투명도가 높으면 줄마다 진한 그림자를 붙여 밝은 게임 화면 위에서도 읽히게 한다.
        (뷰포트 전체 그림자와 겹쳐 외곽선처럼 보인다.)"""
        if self._transparency >= _SHADOW_FROM:
            if not isinstance(lbl.graphicsEffect(), QGraphicsDropShadowEffect):
                fx = QGraphicsDropShadowEffect(lbl)
                fx.setBlurRadius(3)
                fx.setOffset(0, 0)
                fx.setColor(QColor(0, 0, 0, 255))
                lbl.setGraphicsEffect(fx)
        elif lbl.graphicsEffect() is not None:
            lbl.setGraphicsEffect(None)

    def _label_width(self) -> int:
        return max(120, self._list.viewport().width() - 26)

    def _fit(self, item: QListWidgetItem, lbl: QLabel):
        w = self._label_width()
        lbl.ensurePolished()  # 스타일시트 폰트 크기가 반영된 뒤에 높이를 재야 줄이 겹치지 않는다
        lbl.setFixedWidth(w)
        item.setSizeHint(QSize(w, lbl.heightForWidth(w) + 4))

    def _add_entry(self, entry: dict):
        if not (entry.get("text") or "").strip():
            return
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setData(Qt.ItemDataRole.UserRole, entry)
        lbl = self._make_label(entry, 1.0)
        self._fit(item, lbl)
        self._list.addItem(item)
        self._list.setItemWidget(item, lbl)

        max_msgs = int(self._config.get("max_messages", 150))
        while self._list.count() > max_msgs:
            self._list.takeItem(0)

        if self._fade:
            # 구간 경계를 막 넘은 항목만 다시 칠한다(매번 전체를 다시 그리지 않음).
            n = self._list.count()
            for limit, _ in _FADE_STEPS[:-1]:
                self._restyle(n - 1 - limit)
        self._list.scrollToBottom()

    def _restyle(self, row: int):
        if row < 0 or row >= self._list.count():
            return
        item = self._list.item(row)
        lbl = self._list.itemWidget(item)
        entry = item.data(Qt.ItemDataRole.UserRole)
        if lbl is None or not entry:
            return
        level = _fade_level(self._list.count() - 1 - row) if self._fade else 1.0
        lbl.setText(self._entry_html(entry, level))

    def _restyle_all(self, refit: bool = False):
        for row in range(self._list.count()):
            item = self._list.item(row)
            lbl = self._list.itemWidget(item)
            entry = item.data(Qt.ItemDataRole.UserRole)
            if lbl is None or not entry:
                continue
            if refit:
                lbl.setStyleSheet(self._label_qss(entry))
                self._apply_outline(lbl)
            level = _fade_level(self._list.count() - 1 - row) if self._fade else 1.0
            lbl.setText(self._entry_html(entry, level))
            if refit:
                self._fit(item, lbl)
        self._list.scrollToBottom()

    def _relayout_messages(self):
        for row in range(self._list.count()):
            item = self._list.item(row)
            lbl = self._list.itemWidget(item)
            if lbl is not None:
                self._fit(item, lbl)
        self._list.scrollToBottom()

    # 설정 창에서 호출하는 표시 옵션들 (즉시 반영)
    def set_fade(self, on: bool):
        self._fade = bool(on)
        self._restyle_all()

    def set_show_stt(self, show: bool):
        self._show_stt = bool(show)

    def show_stt(self) -> bool:
        return self._show_stt

    def set_font_size(self, size: int):
        self._config["font_size"] = int(size)
        self._restyle_all(refit=True)

    def set_max_messages(self, value: int):
        self._config["max_messages"] = int(value)
        while self._list.count() > int(value):
            self._list.takeItem(0)

    def _clear_messages(self):
        self._list.clear()

    # ── 드래그 & 리사이즈 ───────────────────────────────────────────────────

    def _in_resize_zone(self, pos: QPoint) -> bool:
        return (pos.x() >= self.width() - self.RESIZE_MARGIN
                and pos.y() >= self.height() - self.RESIZE_MARGIN)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._in_resize_zone(event.position().toPoint()):
                self._resizing = True
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_size = self.size()
            else:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if self._in_resize_zone(pos)
                       else Qt.CursorShape.ArrowCursor)
        if event.buttons() & Qt.MouseButton.LeftButton:
            if self._resizing:
                delta = event.globalPosition().toPoint() - self._resize_start_pos
                self.resize(max(self.minimumWidth(), self._resize_start_size.width() + delta.x()),
                            max(self.minimumHeight(), self._resize_start_size.height() + delta.y()))
            elif not self._drag_pos.isNull():
                self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            try:
                paths.update_user_config("ui", {"width": self.width(), "height": self.height()})
            except Exception as e:
                logger.error(f"창 크기 저장 실패: {e}")
        self._resizing = False
        self._drag_pos = QPoint()
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_timer.start()
        self._elide_profile()

    def _elide_profile(self):
        name = getattr(self, "_profile_name", "")
        width = max(0, self._profile_lbl.width() - 4)
        self._profile_lbl.setText(self._profile_lbl.fontMetrics().elidedText(
            name, Qt.TextElideMode.ElideRight, width))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._click_through:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(150, 155, 165, 150), 1))
        w, h = self.width(), self.height()
        for i in range(3):
            o = 4 + i * 4
            painter.drawLine(w - o, h - 3, w - 3, h - o)

    # ── 우클릭 메뉴 ──────────────────────────────────────────────────────────

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: #1A1C22; color: {T.TEXT}; border: 1px solid {T.LINE_2}; padding: 4px; }}"
            "QMenu::item { padding: 6px 18px; border-radius: 4px; }"
            "QMenu::item:selected { background: #2A2D36; }"
            f"QMenu::separator {{ height: 1px; background: {T.LINE}; margin: 4px 6px; }}"
        )
        op_menu = menu.addMenu("투명도")
        for pct in (0, 25, 50, 75, 100):
            act = QAction(f"{pct}%" + ("  (글자만)" if pct == 100 else ""), self)
            act.setCheckable(True)
            act.setChecked(self._transparency == pct)
            act.triggered.connect(lambda _=False, p=pct: self.set_transparency(p))
            op_menu.addAction(act)

        stt_act = QAction("내 말(받아쓰기) 보기", self)
        stt_act.setCheckable(True)
        stt_act.setChecked(self._show_stt)
        stt_act.triggered.connect(self._toggle_show_stt)
        menu.addAction(stt_act)

        ct_act = QAction("클릭 통과 켜기  (Ctrl+Shift+O)", self)
        ct_act.setEnabled(self._hotkeys_ok)
        ct_act.triggered.connect(lambda: self.set_click_through(True))
        menu.addAction(ct_act)

        clear_act = QAction("채팅 지우기", self)
        clear_act.triggered.connect(self._clear_messages)
        menu.addAction(clear_act)

        menu.addSeparator()
        settings_act = QAction("설정…", self)
        settings_act.triggered.connect(lambda: self.open_settings())
        menu.addAction(settings_act)
        quit_act = QAction("종료", self)
        quit_act.triggered.connect(QApplication.quit)
        menu.addAction(quit_act)
        menu.exec(event.globalPos())

    def _toggle_show_stt(self, checked: bool):
        self._show_stt = bool(checked)
        self._settings.sync_show_stt(self._show_stt)
        try:
            paths.update_user_config("ui", {"show_stt_history": self._show_stt})
            self._config["show_stt_history"] = self._show_stt
        except Exception:
            logger.exception("show_stt_history 저장 실패")


def run_overlay(config: dict, ui_queue: Queue, settings_queue: Queue, onboarding: bool = False):
    import sys
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(T.font(13))

    hotkeys = winapi.GlobalHotkeys()
    app.installNativeEventFilter(hotkeys)
    holder: dict = {}
    ok_ct = hotkeys.register("O", lambda: holder["overlay"].toggle_click_through())
    hotkeys.register("S", lambda: holder["overlay"].toggle_chat())

    overlay = ChatOverlay(config, ui_queue, settings_queue, onboarding=onboarding, hotkeys_ok=ok_ct)
    holder["overlay"] = overlay
    overlay.show()

    code = app.exec()
    hotkeys.unregister_all()
    sys.exit(code)
