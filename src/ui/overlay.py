"""
PyQt6 기반 투명 채팅 오버레이.
- Always-on-top / 드래그 이동 / 우하단 리사이즈
- 상단 타이틀바: ⚙ 설정, ✕ 종료
- 설정 패널: 캡처 창 선택, 게임 프로필 자동 생성 트리거
"""
import logging
from multiprocessing import Queue

from PyQt6.QtCore import Qt, QTimer, QPoint, QSize
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QAction
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("solo_stream.ui")

_NAME_COLORS = [
    "#FF6B6B", "#FFD93D", "#6BCB77", "#4D96FF",
    "#FF922B", "#CC5DE8", "#20C997", "#F06595",
    "#74C0FC", "#A9E34B",
]


def _name_color(name: str) -> str:
    idx = sum(ord(c) for c in name) % len(_NAME_COLORS)
    return _NAME_COLORS[idx]


def _match_exclude_pattern(title: str, pattern: str) -> bool:
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


def _list_windows(excludes: list[str] | None = None) -> list[str]:
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
                if not any(_match_exclude_pattern(w, pat) for pat in excludes)
            ]
        return result
    except Exception:
        return []


class TitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setStyleSheet("background: transparent;")
        self._active = False  # 초기: 정지 상태

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(4)

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet("color: #555; font-size: 10px;")
        layout.addWidget(self._status_dot)

        title = QLabel("Solo Stream")
        title.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(title)
        layout.addStretch()

        # 시작/종료 토글 버튼
        self.toggle_btn = QPushButton("▶ 시작")
        self.toggle_btn.setFixedHeight(20)
        self.toggle_btn.setStyleSheet(
            "QPushButton { color: #6BCB77; background: rgba(30,60,30,180); border: 1px solid #3a7a3a; "
            "border-radius: 3px; font-size: 10px; padding: 0 6px; }"
            "QPushButton:hover { background: rgba(50,100,50,200); }"
        )
        layout.addWidget(self.toggle_btn)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setFixedSize(22, 22)
        self.settings_btn.setStyleSheet(
            "QPushButton { color: #aaa; background: transparent; border: none; font-size: 14px; }"
            "QPushButton:hover { color: #fff; }"
        )
        layout.addWidget(self.settings_btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setStyleSheet(
            "QPushButton { color: #aaa; background: transparent; border: none; font-size: 12px; }"
            "QPushButton:hover { color: #ff6b6b; }"
        )
        self.close_btn.clicked.connect(QApplication.quit)
        layout.addWidget(self.close_btn)

    def set_active(self, active: bool):
        self._active = active
        if active:
            self._status_dot.setStyleSheet("color: #6BCB77; font-size: 10px;")
            self.toggle_btn.setText("■ 정지")
            self.toggle_btn.setStyleSheet(
                "QPushButton { color: #FF6B6B; background: rgba(60,20,20,180); border: 1px solid #7a3a3a; "
                "border-radius: 3px; font-size: 10px; padding: 0 6px; }"
                "QPushButton:hover { background: rgba(100,30,30,200); }"
            )
        else:
            self._status_dot.setStyleSheet("color: #555; font-size: 10px;")
            self.toggle_btn.setText("▶ 시작")
            self.toggle_btn.setStyleSheet(
                "QPushButton { color: #6BCB77; background: rgba(30,60,30,180); border: 1px solid #3a7a3a; "
                "border-radius: 3px; font-size: 10px; padding: 0 6px; }"
                "QPushButton:hover { background: rgba(50,100,50,200); }"
            )


class SettingsPanel(QWidget):
    def __init__(self, settings_queue: Queue, config: dict, parent=None):
        super().__init__(parent)
        self._settings_queue = settings_queue
        self._config = config
        self._excludes: list[str] = list(config.get("ui", {}).get("window_excludes", []))
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 10)
        layout.setSpacing(8)

        # 구분선
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,30);")
        layout.addWidget(sep)

        # 창 선택
        win_label = QLabel("캡처 창 선택")
        win_label.setStyleSheet("color: #bbb; font-size: 11px; font-weight: bold;")
        layout.addWidget(win_label)

        self._window_combo = QComboBox()
        self._window_combo.setStyleSheet("""
            QComboBox {
                background: #1e1e2e;
                color: #ddd;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #1e1e2e;
                color: #ddd;
                selection-background-color: #333;
            }
        """)
        layout.addWidget(self._window_combo)

        win_btns = QHBoxLayout()
        refresh_btn = QPushButton("새로고침")
        refresh_btn.setStyleSheet(self._btn_style("#333", "#555"))
        refresh_btn.clicked.connect(self._refresh_windows)
        win_btns.addWidget(refresh_btn)

        apply_win_btn = QPushButton("캡처 적용")
        apply_win_btn.setStyleSheet(self._btn_style("#1a472a", "#2d6a4f"))
        apply_win_btn.clicked.connect(self._apply_window)
        win_btns.addWidget(apply_win_btn)
        layout.addLayout(win_btns)

        # 구분선
        sep2 = QLabel()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep2)

        # 창 제외 패턴
        excl_label = QLabel("창 목록 제외 패턴")
        excl_label.setStyleSheet("color: #bbb; font-size: 11px; font-weight: bold;")
        layout.addWidget(excl_label)

        hint = QLabel("%단어% 포함  /  단어% 시작  /  %단어 끝")
        hint.setStyleSheet("color: #666; font-size: 9px;")
        layout.addWidget(hint)

        self._exclude_list = QListWidget()
        self._exclude_list.setFixedHeight(80)
        self._exclude_list.setStyleSheet("""
            QListWidget {
                background: #111118;
                color: #ccc;
                border: 1px solid #444;
                border-radius: 4px;
                font-size: 10px;
            }
            QListWidget::item:selected { background: #2a2a4a; }
        """)
        self._exclude_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for pat in self._excludes:
            self._exclude_list.addItem(pat)
        layout.addWidget(self._exclude_list)

        from PyQt6.QtWidgets import QLineEdit
        excl_input_row = QHBoxLayout()
        self._excl_input = QLineEdit()
        self._excl_input.setPlaceholderText("%Discord%  또는  C:\\Users%")
        self._excl_input.setStyleSheet(
            "QLineEdit { background: #1e1e2e; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 3px 6px; font-size: 10px; }"
        )
        self._excl_input.returnPressed.connect(self._add_exclude)
        excl_input_row.addWidget(self._excl_input)

        add_btn = QPushButton("+")
        add_btn.setFixedSize(26, 26)
        add_btn.setStyleSheet(self._btn_style("#1a3a5a", "#2a5a8a"))
        add_btn.clicked.connect(self._add_exclude)
        excl_input_row.addWidget(add_btn)

        del_btn = QPushButton("−")
        del_btn.setFixedSize(26, 26)
        del_btn.setStyleSheet(self._btn_style("#3a1a1a", "#6a2a2a"))
        del_btn.clicked.connect(self._del_exclude)
        excl_input_row.addWidget(del_btn)
        layout.addLayout(excl_input_row)

        # 구분선
        sep3 = QLabel()
        sep3.setFixedHeight(1)
        sep3.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep3)

        # 게임 프로필
        game_label = QLabel("게임 프로필")
        game_label.setStyleSheet("color: #bbb; font-size: 11px; font-weight: bold;")
        layout.addWidget(game_label)

        self._profile_status = QLabel("현재: 로딩 중...")
        self._profile_status.setStyleSheet("color: #888; font-size: 10px;")
        self._profile_status.setWordWrap(True)
        layout.addWidget(self._profile_status)

        regen_btn = QPushButton("선택 창 게임 프로필 재생성")
        regen_btn.setStyleSheet(self._btn_style("#2d1b69", "#4a2fa0"))
        regen_btn.clicked.connect(self._regenerate_profile)
        layout.addWidget(regen_btn)

        layout.addStretch()

        self._refresh_windows()

    def _btn_style(self, bg: str, hover: str) -> str:
        return (
            f"QPushButton {{ background: {bg}; color: #ddd; border: 1px solid #555; "
            f"border-radius: 4px; padding: 5px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {hover}; }}"
        )

    def _add_exclude(self):
        pat = self._excl_input.text().strip()
        if not pat:
            return
        if pat not in self._excludes:
            self._excludes.append(pat)
            self._exclude_list.addItem(pat)
            self._save_excludes()
        self._excl_input.clear()
        self._refresh_windows()

    def _del_exclude(self):
        row = self._exclude_list.currentRow()
        if row < 0:
            return
        pat = self._exclude_list.takeItem(row).text()
        if pat in self._excludes:
            self._excludes.remove(pat)
            self._save_excludes()
        self._refresh_windows()

    def _save_excludes(self):
        """제외 패턴을 config.yaml에 저장."""
        try:
            from pathlib import Path
            import yaml as _yaml
            config_path = Path(__file__).parent.parent.parent / "config.yaml"
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = _yaml.safe_load(f)
            cfg.setdefault("ui", {})["window_excludes"] = self._excludes
            with open(config_path, "w", encoding="utf-8") as f:
                _yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as e:
            logger.error(f"제외 패턴 저장 실패: {e}")

    def _refresh_windows(self):
        current = self._window_combo.currentText()
        self._window_combo.clear()
        windows = _list_windows(excludes=self._excludes)
        self._window_combo.addItems(windows)
        idx = self._window_combo.findText(current)
        if idx >= 0:
            self._window_combo.setCurrentIndex(idx)

    def _apply_window(self):
        window_title = self._window_combo.currentText()
        if not window_title:
            return
        self._settings_queue.put({"target_window": window_title})
        self._settings_queue.put({"game_name": _extract_game_name(window_title)})
        self._profile_status.setText(f"적용 중: {window_title}\n게임 프로필 생성 중...")
        logger.info(f"창 변경 적용: {window_title}")

    def _regenerate_profile(self):
        window_title = self._window_combo.currentText()
        if not window_title:
            return
        game_name = _extract_game_name(window_title)
        # 기존 파일 삭제 후 재생성 요청
        import re
        from pathlib import Path
        profile_dir = Path(__file__).parent.parent.parent / "game_profiles"
        safe = re.sub(r'[\\/:*?"<>|]', "", game_name).strip().lower().replace(" ", "_")[:64]
        profile_path = profile_dir / f"{safe}.yaml"
        if profile_path.exists():
            profile_path.unlink()
            logger.info(f"기존 프로필 삭제: {profile_path.name}")
        self._settings_queue.put({"game_name": game_name})
        self._profile_status.setText(f"재생성 중: {game_name}...")

    def set_profile_name(self, name: str):
        self._profile_status.setText(f"현재 프로필: {name}")


def _extract_game_name(window_title: str) -> str:
    import re
    name = window_title or ""
    name = re.sub(r"\s*[-–]\s*Steam.*", "", name)
    name = re.sub(r"\bMin\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\bv\d[\d.]*[-\w]*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "Unknown Game"


class ChatOverlay(QWidget):
    RESIZE_MARGIN = 12

    def __init__(self, config: dict, ui_queue: Queue, settings_queue: Queue):
        super().__init__()
        self._config = config["ui"]
        self._config_full = config
        self._ui_queue = ui_queue
        self._settings_queue = settings_queue
        self._drag_pos = QPoint()
        self._resizing = False
        self._resize_start_pos = QPoint()
        self._resize_start_size = QSize()
        self._opacity = self._config.get("opacity", 0.85)
        self._settings_visible = False

        self._setup_window()
        self._setup_ui()
        self._setup_timer()

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(220, 180)
        self.resize(self._config["width"], self._config["height"])

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._container = QWidget()
        self._container.setObjectName("container")
        self._apply_style()

        inner = QVBoxLayout(self._container)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        # 타이틀바
        self._titlebar = TitleBar()
        self._titlebar.settings_btn.clicked.connect(self._toggle_settings)
        self._titlebar.toggle_btn.clicked.connect(self._toggle_chat)
        inner.addWidget(self._titlebar)

        # 스택: 0=채팅, 1=설정
        self._stack = QStackedWidget()

        # 채팅 페이지
        chat_page = QWidget()
        chat_page.setStyleSheet("background: transparent;")
        chat_layout = QVBoxLayout(chat_page)
        chat_layout.setContentsMargins(6, 2, 6, 6)
        chat_layout.setSpacing(0)

        self._list = QListWidget()
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item {
                background: transparent;
                padding: 1px 0px;
            }
            QScrollBar:vertical {
                width: 4px;
                background: #222;
            }
            QScrollBar::handle:vertical {
                background: #666;
                border-radius: 2px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        self._list.setWordWrap(True)
        self._list.setSpacing(1)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        chat_layout.addWidget(self._list)

        # 설정 페이지
        self._settings_panel = SettingsPanel(self._settings_queue, self._config_full)
        self._settings_panel.setStyleSheet("background: transparent;")

        self._stack.addWidget(chat_page)       # index 0
        self._stack.addWidget(self._settings_panel)  # index 1

        inner.addWidget(self._stack)
        outer.addWidget(self._container)

        # 리사이즈 핸들
        self._resize_hint = QLabel("◢", self)
        self._resize_hint.setStyleSheet("color: #555; font-size: 12px; background: transparent;")
        self._resize_hint.setFixedSize(14, 14)
        self._position_resize_hint()

    def _apply_style(self):
        alpha = int(self._opacity * 255)
        self._container.setStyleSheet(
            f"QWidget#container {{ background-color: rgba(15, 15, 20, {alpha}); "
            f"border-radius: 8px; border: 1px solid rgba(255,255,255,30); }}"
        )

    def _position_resize_hint(self):
        self._resize_hint.move(self.width() - 14, self.height() - 14)

    def _toggle_chat(self):
        new_state = not self._titlebar._active
        self._titlebar.set_active(new_state)
        self._settings_queue.put({"chat_active": new_state})

    def _toggle_settings(self):
        self._settings_visible = not self._settings_visible
        self._stack.setCurrentIndex(1 if self._settings_visible else 0)
        self._titlebar.settings_btn.setStyleSheet(
            "QPushButton { color: %s; background: transparent; border: none; font-size: 14px; }"
            "QPushButton:hover { color: #fff; }" % ("#fff" if self._settings_visible else "#aaa")
        )

    def _setup_timer(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_queue)
        self._timer.start(100)

    def _poll_queue(self):
        try:
            while not self._ui_queue.empty():
                chat = self._ui_queue.get_nowait()
                if isinstance(chat, dict):
                    if "system" in chat:
                        # 시스템 메시지 (프로필 로드 완료 등)
                        if chat.get("type") == "profile_loaded":
                            self._settings_panel.set_profile_name(chat["name"])
                    else:
                        self._add_message(chat["name"], chat["message"])
        except Exception:
            pass

    def _add_message(self, name: str, message: str):
        font_size = self._config.get("font_size", 14)
        color = _name_color(name)

        label = QLabel()
        label.setText(
            f'<span style="color:{color}; font-weight:bold;">{name}</span>'
            f'<span style="color:#DDDDDD;">: {message}</span>'
        )
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setStyleSheet(f"font-size: {font_size}pt; background: transparent; padding: 1px 4px;")
        label.setContentsMargins(0, 0, 0, 0)

        available_w = self._list.viewport().width() - 8
        label.setFixedWidth(max(available_w, 100))
        label.adjustSize()

        item = QListWidgetItem()
        item.setSizeHint(label.sizeHint())
        item.setFlags(Qt.ItemFlag.NoItemFlags)

        self._list.addItem(item)
        self._list.setItemWidget(item, label)

        max_msgs = self._config.get("max_messages", 50)
        while self._list.count() > max_msgs:
            self._list.takeItem(0)

        self._list.scrollToBottom()

    # ── 드래그 & 리사이즈 ───────────────────────────────────────────────────

    def _in_resize_zone(self, pos: QPoint) -> bool:
        return (
            pos.x() >= self.width() - self.RESIZE_MARGIN
            and pos.y() >= self.height() - self.RESIZE_MARGIN
        )

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
        if self._in_resize_zone(pos):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

        if event.buttons() & Qt.MouseButton.LeftButton:
            if self._resizing:
                delta = event.globalPosition().toPoint() - self._resize_start_pos
                new_w = max(220, self._resize_start_size.width() + delta.x())
                new_h = max(180, self._resize_start_size.height() + delta.y())
                self.resize(new_w, new_h)
                self._position_resize_hint()
            elif not self._drag_pos.isNull():
                self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resizing = False
        self._drag_pos = QPoint()
        super().mouseReleaseEvent(event)

    # ── 우클릭 메뉴 ──────────────────────────────────────────────────────────

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #1a1a2e; color: #eee; border: 1px solid #444; }"
            "QMenu::item:selected { background: #333; }"
        )

        opacity_menu = menu.addMenu("투명도")
        for pct in [30, 50, 70, 85, 100]:
            action = QAction(f"{pct}%", self)
            action.triggered.connect(lambda _, p=pct: self._set_opacity(p / 100))
            opacity_menu.addAction(action)

        menu.addSeparator()
        clear_action = QAction("채팅 지우기", self)
        clear_action.triggered.connect(self._clear_messages)
        menu.addAction(clear_action)

        menu.addSeparator()
        quit_action = QAction("종료", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

        menu.exec(event.globalPos())

    def _set_opacity(self, value: float):
        self._opacity = max(0.1, min(1.0, value))
        self._apply_style()

    def _clear_messages(self):
        self._list.clear()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_resize_hint()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(100, 100, 100, 180), 1)
        painter.setPen(pen)
        m = self.RESIZE_MARGIN
        w, h = self.width(), self.height()
        for i in range(3):
            offset = i * 4
            painter.drawLine(w - m + offset, h - 2, w - 2, h - m + offset)


def run_overlay(config: dict, ui_queue: Queue, settings_queue: Queue):
    import sys
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    overlay = ChatOverlay(config, ui_queue, settings_queue)
    overlay.show()

    sys.exit(app.exec())
