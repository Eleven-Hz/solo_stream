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
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
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
    _COMBO_STYLE = """
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
    """

    _GEMINI_VISION_MODELS = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
    ]
    _CLAUDE_VISION_MODELS = [
        "claude-sonnet-4-6",
        "claude-opus-4-7",
        "claude-haiku-4-5-20251001",
    ]
    _GEMINI_CHAT_MODELS = [
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
    ]
    _CLAUDE_CHAT_MODELS = [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
        "claude-opus-4-7",
    ]

    def __init__(self, settings_queue: Queue, config: dict, parent=None):
        super().__init__(parent)
        self._settings_queue = settings_queue
        self._config = config
        self._excludes: list[str] = list(config.get("ui", {}).get("window_excludes", []))
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 6)
        outer.setSpacing(4)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #333;
                border-radius: 4px;
                background: transparent;
                top: -1px;
            }
            QTabBar::tab {
                background: #1a1a25;
                color: #888;
                padding: 6px 12px;
                border: 1px solid #333;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-size: 11px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #2a2a40;
                color: #ddd;
                font-weight: bold;
            }
            QTabBar::tab:hover:!selected { background: #222232; color: #bbb; }
        """)
        outer.addWidget(self._tabs)

        # 각 탭의 내용은 별도 메서드로 빌드
        self._tabs.addTab(self._build_tab_game(), "게임")
        self._tabs.addTab(self._build_tab_ai(), "AI 모델")
        self._tabs.addTab(self._build_tab_stt(), "음성")

        # 모든 위젯 초기화 후 캡처 창 목록 새로고침
        self._refresh_windows()

    def _make_scroll_tab(self, content_layout_fn):
        """탭 내용은 길어질 수 있어 스크롤 영역에 담는다. content_layout_fn(layout) 콜백으로 위젯 추가."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)
        content_layout_fn(layout)
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { background: #1a1a25; width: 8px; }
            QScrollBar::handle:vertical { background: #444; border-radius: 4px; min-height: 20px; }
            QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
        """)
        return scroll

    def _build_tab_game(self) -> QWidget:
        def _body(layout: QVBoxLayout):
            self._build_section_window(layout)
            self._build_section_excludes(layout)
            self._build_section_game_profile(layout)
        return self._make_scroll_tab(_body)

    def _build_tab_ai(self) -> QWidget:
        def _body(layout: QVBoxLayout):
            self._build_section_vision_model(layout)
            self._build_section_chat_model(layout)
            self._build_section_api_keys(layout)
        return self._make_scroll_tab(_body)

    def _build_tab_stt(self) -> QWidget:
        def _body(layout: QVBoxLayout):
            self._build_section_stt(layout)
        return self._make_scroll_tab(_body)

    # ── 게임 탭 섹션 ─────────────────────────────────────────────────────

    def _build_section_window(self, layout: QVBoxLayout):
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

    def _build_section_excludes(self, layout: QVBoxLayout):
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

    # ── AI 탭 섹션 ───────────────────────────────────────────────────────

    def _build_section_vision_model(self, layout: QVBoxLayout):
        # 이미지 분석 모델 선택
        ai_label = QLabel("이미지 분석 모델")
        ai_label.setStyleSheet("color: #bbb; font-size: 11px; font-weight: bold;")
        layout.addWidget(ai_label)

        # provider 행
        provider_row = QHBoxLayout()
        provider_lbl = QLabel("Provider")
        provider_lbl.setStyleSheet("color: #888; font-size: 10px;")
        provider_lbl.setFixedWidth(52)
        provider_row.addWidget(provider_lbl)

        self._provider_combo = QComboBox()
        self._provider_combo.setStyleSheet(self._COMBO_STYLE)
        self._provider_combo.addItems(["gemini", "claude"])
        current_provider = self._config.get("ai", {}).get("vision_provider", "gemini")
        idx = self._provider_combo.findText(current_provider)
        if idx >= 0:
            self._provider_combo.setCurrentIndex(idx)
        provider_row.addWidget(self._provider_combo)
        layout.addLayout(provider_row)

        # model 행
        model_row = QHBoxLayout()
        model_lbl = QLabel("Model")
        model_lbl.setStyleSheet("color: #888; font-size: 10px;")
        model_lbl.setFixedWidth(52)
        model_row.addWidget(model_lbl)

        self._vision_model_combo = QComboBox()
        self._vision_model_combo.setStyleSheet(self._COMBO_STYLE)
        model_row.addWidget(self._vision_model_combo)
        layout.addLayout(model_row)

        # provider 변경 시 모델 목록 교체
        def _on_provider_changed(provider: str):
            self._vision_model_combo.clear()
            if provider == "claude":
                self._vision_model_combo.addItems(self._CLAUDE_VISION_MODELS)
                cur = self._config.get("ai", {}).get("claude_vision_model", self._CLAUDE_VISION_MODELS[0])
            else:
                self._vision_model_combo.addItems(self._GEMINI_VISION_MODELS)
                cur = self._config.get("ai", {}).get("vision_model", self._GEMINI_VISION_MODELS[0])
            idx = self._vision_model_combo.findText(cur)
            self._vision_model_combo.setCurrentIndex(max(idx, 0))

        self._provider_combo.currentTextChanged.connect(_on_provider_changed)
        _on_provider_changed(current_provider)  # 초기 목록 채우기

        apply_ai_btn = QPushButton("분석 모델 적용")
        apply_ai_btn.setStyleSheet(self._btn_style("#1a3a5a", "#2a5a8a"))
        apply_ai_btn.clicked.connect(self._apply_vision_provider)
        layout.addWidget(apply_ai_btn)

    def _build_section_chat_model(self, layout: QVBoxLayout):
        # 구분선
        sep4 = QLabel()
        sep4.setFixedHeight(1)
        sep4.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep4)

        # 채팅 생성 모델 선택
        chat_model_label = QLabel("채팅 생성 모델")
        chat_model_label.setStyleSheet("color: #bbb; font-size: 11px; font-weight: bold;")
        layout.addWidget(chat_model_label)

        chat_provider_row = QHBoxLayout()
        chat_provider_lbl = QLabel("Provider")
        chat_provider_lbl.setStyleSheet("color: #888; font-size: 10px;")
        chat_provider_lbl.setFixedWidth(52)
        chat_provider_row.addWidget(chat_provider_lbl)

        self._chat_provider_combo = QComboBox()
        self._chat_provider_combo.setStyleSheet(self._COMBO_STYLE)
        self._chat_provider_combo.addItems(["gemini", "claude"])
        current_chat_provider = self._config.get("ai", {}).get("chat_provider", "gemini")
        chat_prov_idx = self._chat_provider_combo.findText(current_chat_provider)
        if chat_prov_idx >= 0:
            self._chat_provider_combo.setCurrentIndex(chat_prov_idx)
        chat_provider_row.addWidget(self._chat_provider_combo)
        layout.addLayout(chat_provider_row)

        chat_model_row = QHBoxLayout()
        chat_model_lbl = QLabel("Model")
        chat_model_lbl.setStyleSheet("color: #888; font-size: 10px;")
        chat_model_lbl.setFixedWidth(52)
        chat_model_row.addWidget(chat_model_lbl)

        self._chat_model_combo = QComboBox()
        self._chat_model_combo.setStyleSheet(self._COMBO_STYLE)
        chat_model_row.addWidget(self._chat_model_combo)
        layout.addLayout(chat_model_row)

        def _on_chat_provider_changed(provider: str):
            self._chat_model_combo.clear()
            if provider == "claude":
                self._chat_model_combo.addItems(self._CLAUDE_CHAT_MODELS)
                cur = self._config.get("ai", {}).get("claude_chat_model", self._CLAUDE_CHAT_MODELS[0])
            else:
                self._chat_model_combo.addItems(self._GEMINI_CHAT_MODELS)
                cur = self._config.get("ai", {}).get("chat_model", self._GEMINI_CHAT_MODELS[0])
            idx = self._chat_model_combo.findText(cur)
            self._chat_model_combo.setCurrentIndex(max(idx, 0))

        self._chat_provider_combo.currentTextChanged.connect(_on_chat_provider_changed)
        _on_chat_provider_changed(current_chat_provider)

        apply_chat_btn = QPushButton("채팅 모델 적용")
        apply_chat_btn.setStyleSheet(self._btn_style("#1a3a5a", "#2a5a8a"))
        apply_chat_btn.clicked.connect(self._apply_chat_model)
        layout.addWidget(apply_chat_btn)

    def _build_section_api_keys(self, layout: QVBoxLayout):
        # 구분선
        sep4b = QLabel()
        sep4b.setFixedHeight(1)
        sep4b.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep4b)

        # API 키 입력
        api_label = QLabel("API 키 설정")
        api_label.setStyleSheet("color: #bbb; font-size: 11px; font-weight: bold;")
        layout.addWidget(api_label)

        gemini_row = QHBoxLayout()
        gemini_lbl = QLabel("Gemini")
        gemini_lbl.setStyleSheet("color: #888; font-size: 10px;")
        gemini_lbl.setFixedWidth(42)
        gemini_row.addWidget(gemini_lbl)
        self._gemini_key_input = QLineEdit()
        self._gemini_key_input.setPlaceholderText("GEMINI_API_KEY")
        self._gemini_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._gemini_key_input.setStyleSheet(
            "QLineEdit { background: #1e1e2e; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 3px 6px; font-size: 10px; }"
        )
        gemini_row.addWidget(self._gemini_key_input)
        layout.addLayout(gemini_row)

        claude_row = QHBoxLayout()
        claude_lbl = QLabel("Claude")
        claude_lbl.setStyleSheet("color: #888; font-size: 10px;")
        claude_lbl.setFixedWidth(42)
        claude_row.addWidget(claude_lbl)
        self._claude_key_input = QLineEdit()
        self._claude_key_input.setPlaceholderText("CLAUDE_API_KEY")
        self._claude_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._claude_key_input.setStyleSheet(
            "QLineEdit { background: #1e1e2e; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 3px 6px; font-size: 10px; }"
        )
        claude_row.addWidget(self._claude_key_input)
        layout.addLayout(claude_row)

        save_keys_btn = QPushButton("API 키 저장 (.env.local)")
        save_keys_btn.setStyleSheet(self._btn_style("#1a3a1a", "#2d6a2d"))
        save_keys_btn.clicked.connect(self._save_api_keys)
        layout.addWidget(save_keys_btn)

    def _build_section_game_profile(self, layout: QVBoxLayout):
        # 구분선
        sep5 = QLabel()
        sep5.setFixedHeight(1)
        sep5.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep5)

        # 게임 프로필
        game_label = QLabel("게임 프로필")
        game_label.setStyleSheet("color: #bbb; font-size: 11px; font-weight: bold;")
        layout.addWidget(game_label)

        self._profile_combo = QComboBox()
        self._profile_combo.setStyleSheet("""
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
        layout.addWidget(self._profile_combo)
        self._refresh_profiles()

        profile_btns = QHBoxLayout()
        refresh_profile_btn = QPushButton("새로고침")
        refresh_profile_btn.setStyleSheet(self._btn_style("#333", "#555"))
        refresh_profile_btn.clicked.connect(self._refresh_profiles)
        profile_btns.addWidget(refresh_profile_btn)

        apply_profile_btn = QPushButton("프로필 적용")
        apply_profile_btn.setStyleSheet(self._btn_style("#1a3a5a", "#2a5a8a"))
        apply_profile_btn.clicked.connect(self._apply_profile)
        profile_btns.addWidget(apply_profile_btn)
        layout.addLayout(profile_btns)

        self._profile_status = QLabel("현재: 로딩 중...")
        self._profile_status.setStyleSheet("color: #888; font-size: 10px;")
        self._profile_status.setWordWrap(True)
        layout.addWidget(self._profile_status)

        regen_btn = QPushButton("선택 창 게임 프로필 재생성")
        regen_btn.setStyleSheet(self._btn_style("#2d1b69", "#4a2fa0"))
        regen_btn.clicked.connect(self._regenerate_profile)
        layout.addWidget(regen_btn)

    # ── 음성(STT) 탭 섹션 ───────────────────────────────────────────────

    def _build_section_stt(self, layout: QVBoxLayout):
        stt_cfg = self._config.get("stt", {}) or {}

        _input_style = (
            "QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit { "
            "background: #1e1e2e; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 3px 6px; font-size: 11px; }"
        )
        _combo_style = self._COMBO_STYLE

        # enabled 토글
        title = QLabel("음성 인식 (STT)")
        title.setStyleSheet("color: #bbb; font-size: 11px; font-weight: bold;")
        layout.addWidget(title)

        self._stt_enabled_chk = QCheckBox("마이크 음성을 채팅 컨텍스트로 사용")
        self._stt_enabled_chk.setStyleSheet(
            "QCheckBox { color: #ddd; font-size: 11px; spacing: 6px; }"
            "QCheckBox::indicator { width: 14px; height: 14px; }"
            "QCheckBox::indicator:unchecked { background: #1e1e2e; border: 1px solid #555; border-radius: 3px; }"
            "QCheckBox::indicator:checked { background: #2d6a4f; border: 1px solid #4f8a6f; border-radius: 3px; }"
        )
        self._stt_enabled_chk.setChecked(bool(stt_cfg.get("enabled", False)))
        layout.addWidget(self._stt_enabled_chk)

        hint = QLabel("변경 후 'STT 설정 저장'을 누르면 config.yaml에 기록됩니다.\n"
                      "enabled 변경은 다음 실행부터 적용됩니다.")
        hint.setStyleSheet("color: #666; font-size: 9px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 구분선
        sep = QLabel(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep)

        # default_weight
        weight_row = QHBoxLayout()
        weight_lbl = QLabel("기본 반응 강도")
        weight_lbl.setStyleSheet("color: #bbb; font-size: 11px;")
        weight_lbl.setFixedWidth(110)
        weight_row.addWidget(weight_lbl)
        self._stt_weight_combo = QComboBox()
        self._stt_weight_combo.setStyleSheet(_combo_style)
        self._stt_weight_combo.addItems(["off", "low", "medium", "high"])
        cur_w = str(stt_cfg.get("default_weight", "high")).lower()
        idx = self._stt_weight_combo.findText(cur_w)
        self._stt_weight_combo.setCurrentIndex(idx if idx >= 0 else 3)
        weight_row.addWidget(self._stt_weight_combo)
        layout.addLayout(weight_row)

        w_hint = QLabel("페르소나에 user_speech_weight가 없는 봇이 사용하는 기본값")
        w_hint.setStyleSheet("color: #666; font-size: 9px;")
        w_hint.setWordWrap(True)
        layout.addWidget(w_hint)

        # lookback_seconds
        lb_row = QHBoxLayout()
        lb_lbl = QLabel("lookback (초)")
        lb_lbl.setStyleSheet("color: #bbb; font-size: 11px;")
        lb_lbl.setFixedWidth(110)
        lb_row.addWidget(lb_lbl)
        self._stt_lookback_spin = QDoubleSpinBox()
        self._stt_lookback_spin.setStyleSheet(_input_style)
        self._stt_lookback_spin.setRange(1.0, 120.0)
        self._stt_lookback_spin.setSingleStep(1.0)
        self._stt_lookback_spin.setDecimals(1)
        self._stt_lookback_spin.setValue(float(stt_cfg.get("lookback_seconds", 20.0)))
        lb_row.addWidget(self._stt_lookback_spin)
        layout.addLayout(lb_row)

        lb_hint = QLabel("vision 라운드 직전 N초 안의 발화만 채팅 컨텍스트로 사용")
        lb_hint.setStyleSheet("color: #666; font-size: 9px;")
        lb_hint.setWordWrap(True)
        layout.addWidget(lb_hint)

        # min_transcript_chars
        mc_row = QHBoxLayout()
        mc_lbl = QLabel("최소 글자 수")
        mc_lbl.setStyleSheet("color: #bbb; font-size: 11px;")
        mc_lbl.setFixedWidth(110)
        mc_row.addWidget(mc_lbl)
        self._stt_min_chars_spin = QSpinBox()
        self._stt_min_chars_spin.setStyleSheet(_input_style)
        self._stt_min_chars_spin.setRange(0, 50)
        self._stt_min_chars_spin.setValue(int(stt_cfg.get("min_transcript_chars", 2)))
        mc_row.addWidget(self._stt_min_chars_spin)
        layout.addLayout(mc_row)

        # 구분선
        sep2 = QLabel(); sep2.setFixedHeight(1)
        sep2.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep2)

        # VAD 파라미터
        vad_label = QLabel("VAD 파라미터")
        vad_label.setStyleSheet("color: #bbb; font-size: 11px; font-weight: bold;")
        layout.addWidget(vad_label)

        # threshold
        th_row = QHBoxLayout()
        th_lbl = QLabel("threshold")
        th_lbl.setStyleSheet("color: #888; font-size: 10px;")
        th_lbl.setFixedWidth(110)
        th_row.addWidget(th_lbl)
        self._stt_threshold_spin = QDoubleSpinBox()
        self._stt_threshold_spin.setStyleSheet(_input_style)
        self._stt_threshold_spin.setRange(0.1, 0.9)
        self._stt_threshold_spin.setSingleStep(0.05)
        self._stt_threshold_spin.setDecimals(2)
        self._stt_threshold_spin.setValue(float(stt_cfg.get("vad_threshold", 0.5)))
        th_row.addWidget(self._stt_threshold_spin)
        layout.addLayout(th_row)

        # min_speech_ms / min_silence_ms
        for label_text, key, default in [
            ("min_speech (ms)", "min_speech_ms", 250),
            ("min_silence (ms)", "min_silence_ms", 500),
            ("speech_pad (ms)", "speech_pad_ms", 100),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #888; font-size: 10px;")
            lbl.setFixedWidth(110)
            row.addWidget(lbl)
            spin = QSpinBox()
            spin.setStyleSheet(_input_style)
            spin.setRange(0, 5000)
            spin.setSingleStep(50)
            spin.setValue(int(stt_cfg.get(key, default)))
            row.addWidget(spin)
            layout.addLayout(row)
            # 동적 속성으로 보관 (apply 시 일괄 수집)
            setattr(self, f"_stt_{key}_spin", spin)

        # max_utterance_seconds
        mu_row = QHBoxLayout()
        mu_lbl = QLabel("max 발화 (초)")
        mu_lbl.setStyleSheet("color: #888; font-size: 10px;")
        mu_lbl.setFixedWidth(110)
        mu_row.addWidget(mu_lbl)
        self._stt_max_utt_spin = QDoubleSpinBox()
        self._stt_max_utt_spin.setStyleSheet(_input_style)
        self._stt_max_utt_spin.setRange(5.0, 120.0)
        self._stt_max_utt_spin.setSingleStep(5.0)
        self._stt_max_utt_spin.setDecimals(1)
        self._stt_max_utt_spin.setValue(float(stt_cfg.get("max_utterance_seconds", 30.0)))
        mu_row.addWidget(self._stt_max_utt_spin)
        layout.addLayout(mu_row)

        # 구분선
        sep3 = QLabel(); sep3.setFixedHeight(1)
        sep3.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep3)

        # 모델 + 언어
        stt_model_label = QLabel("Transcribe 모델")
        stt_model_label.setStyleSheet("color: #bbb; font-size: 11px; font-weight: bold;")
        layout.addWidget(stt_model_label)

        model_row = QHBoxLayout()
        model_lbl = QLabel("model")
        model_lbl.setStyleSheet("color: #888; font-size: 10px;")
        model_lbl.setFixedWidth(110)
        model_row.addWidget(model_lbl)
        self._stt_model_combo = QComboBox()
        self._stt_model_combo.setStyleSheet(_combo_style)
        self._stt_model_combo.addItems([
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.5-pro",
        ])
        self._stt_model_combo.setEditable(True)
        cur_model = str(stt_cfg.get("model", "gemini-2.5-flash"))
        idx_m = self._stt_model_combo.findText(cur_model)
        if idx_m >= 0:
            self._stt_model_combo.setCurrentIndex(idx_m)
        else:
            self._stt_model_combo.setEditText(cur_model)
        model_row.addWidget(self._stt_model_combo)
        layout.addLayout(model_row)

        lang_row = QHBoxLayout()
        lang_lbl = QLabel("language")
        lang_lbl.setStyleSheet("color: #888; font-size: 10px;")
        lang_lbl.setFixedWidth(110)
        lang_row.addWidget(lang_lbl)
        self._stt_language_input = QLineEdit()
        self._stt_language_input.setStyleSheet(_input_style)
        self._stt_language_input.setText(str(stt_cfg.get("language", "ko")))
        lang_row.addWidget(self._stt_language_input)
        layout.addLayout(lang_row)

        device_row = QHBoxLayout()
        device_lbl = QLabel("device")
        device_lbl.setStyleSheet("color: #888; font-size: 10px;")
        device_lbl.setFixedWidth(110)
        device_row.addWidget(device_lbl)
        self._stt_device_input = QLineEdit()
        self._stt_device_input.setStyleSheet(_input_style)
        self._stt_device_input.setPlaceholderText("default 또는 장치 인덱스/이름")
        self._stt_device_input.setText(str(stt_cfg.get("device", "default")))
        device_row.addWidget(self._stt_device_input)
        layout.addLayout(device_row)

        # transcribe_prompt
        prompt_label = QLabel("Transcribe 프롬프트")
        prompt_label.setStyleSheet("color: #bbb; font-size: 11px;")
        layout.addWidget(prompt_label)

        self._stt_prompt_text = QPlainTextEdit()
        self._stt_prompt_text.setStyleSheet(_input_style)
        self._stt_prompt_text.setFixedHeight(70)
        self._stt_prompt_text.setPlainText(str(stt_cfg.get("transcribe_prompt", "")))
        layout.addWidget(self._stt_prompt_text)

        # 저장 버튼
        save_stt_btn = QPushButton("STT 설정 저장 (config.yaml)")
        save_stt_btn.setStyleSheet(self._btn_style("#1a3a5a", "#2a5a8a"))
        save_stt_btn.clicked.connect(self._save_stt_settings)
        layout.addWidget(save_stt_btn)

        self._stt_status = QLabel("")
        self._stt_status.setStyleSheet("color: #888; font-size: 10px;")
        self._stt_status.setWordWrap(True)
        layout.addWidget(self._stt_status)

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

    def _refresh_profiles(self):
        from pathlib import Path
        import re
        current = self._profile_combo.currentText()
        self._profile_combo.clear()
        self._profile_combo.addItem("자동 (창 타이틀에서 감지)")
        profile_dir = Path(__file__).parent.parent.parent / "game_profiles"
        if profile_dir.exists():
            for p in sorted(profile_dir.glob("*.yaml")):
                if p.stem == "default":
                    continue
                # yaml에서 name 필드 읽기 시도
                try:
                    import yaml as _yaml
                    with open(p, "r", encoding="utf-8") as f:
                        data = _yaml.safe_load(f)
                    display = data.get("name") or p.stem
                except Exception:
                    display = p.stem
                self._profile_combo.addItem(display, userData=p.stem)
        idx = self._profile_combo.findText(current)
        if idx >= 0:
            self._profile_combo.setCurrentIndex(idx)

    def _apply_profile(self):
        idx = self._profile_combo.currentIndex()
        if idx == 0:
            # 자동: 현재 선택된 창 타이틀에서 감지
            window_title = self._window_combo.currentText()
            game_name = _extract_game_name(window_title) if window_title else "Unknown Game"
        else:
            game_name = self._profile_combo.currentData() or self._profile_combo.currentText()
        self._settings_queue.put({"game_name": game_name})
        self._profile_status.setText(f"적용 중: {game_name}...")
        logger.info(f"게임 프로필 적용: {game_name}")

    def _apply_window(self):
        window_title = self._window_combo.currentText()
        if not window_title:
            return
        self._settings_queue.put({"target_window": window_title})
        # 프로필 콤보가 "자동"이면 창 타이틀에서 게임명 추출해서 적용
        if self._profile_combo.currentIndex() == 0:
            self._settings_queue.put({"game_name": _extract_game_name(window_title)})
            self._profile_status.setText(f"적용 중: {window_title}\n게임 프로필 자동 감지 중...")
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

    def _apply_vision_provider(self):
        provider = self._provider_combo.currentText()
        model = self._vision_model_combo.currentText()
        self._settings_queue.put({"vision_provider": provider, "vision_model": model})
        self._save_config_vision_provider(provider, model)
        logger.info(f"이미지 분석 모델 변경: {provider} / {model}")

    def _apply_chat_model(self):
        provider = self._chat_provider_combo.currentText()
        model = self._chat_model_combo.currentText()
        self._settings_queue.put({"chat_provider": provider, "chat_model": model})
        self._save_config_chat_model(provider, model)
        logger.info(f"채팅 생성 모델 변경: {provider} / {model}")

    def _save_config_vision_provider(self, provider: str, model: str):
        try:
            from pathlib import Path
            import yaml as _yaml
            config_path = Path(__file__).parent.parent.parent / "config.yaml"
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = _yaml.safe_load(f)
            ai = cfg.setdefault("ai", {})
            ai["vision_provider"] = provider
            if provider == "claude":
                ai["claude_vision_model"] = model
            else:
                ai["vision_model"] = model
            with open(config_path, "w", encoding="utf-8") as f:
                _yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as e:
            logger.error(f"vision_provider 저장 실패: {e}")

    def _save_config_chat_model(self, provider: str, model: str):
        try:
            from pathlib import Path
            import yaml as _yaml
            config_path = Path(__file__).parent.parent.parent / "config.yaml"
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = _yaml.safe_load(f)
            ai = cfg.setdefault("ai", {})
            ai["chat_provider"] = provider
            if provider == "claude":
                ai["claude_chat_model"] = model
            else:
                ai["chat_model"] = model
            with open(config_path, "w", encoding="utf-8") as f:
                _yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as e:
            logger.error(f"chat_model 저장 실패: {e}")

    def _save_api_keys(self):
        try:
            from pathlib import Path
            env_path = Path(__file__).parent.parent.parent / ".env.local"
            lines: list[str] = []
            if env_path.exists():
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

            def _set_key(lines: list[str], name: str, value: str) -> list[str]:
                for i, line in enumerate(lines):
                    if line.startswith(f"{name}="):
                        lines[i] = f"{name}={value}\n"
                        return lines
                lines.append(f"{name}={value}\n")
                return lines

            gemini_key = self._gemini_key_input.text().strip()
            claude_key = self._claude_key_input.text().strip()
            if gemini_key:
                lines = _set_key(lines, "GEMINI_API_KEY", gemini_key)
                self._gemini_key_input.clear()
            if claude_key:
                lines = _set_key(lines, "CLAUDE_API_KEY", claude_key)
                self._claude_key_input.clear()

            if gemini_key or claude_key:
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(lines)
                logger.info("API 키 저장 완료")
        except Exception as e:
            logger.error(f"API 키 저장 실패: {e}")

    def _save_stt_settings(self):
        """UI에서 변경한 STT 설정을 config.yaml에 저장 + 가능한 항목은 ai_process에 브로드캐스트."""
        try:
            from pathlib import Path
            import yaml as _yaml

            new_stt = {
                "enabled": self._stt_enabled_chk.isChecked(),
                "sample_rate": int(self._config.get("stt", {}).get("sample_rate", 16000)),
                "device": self._stt_device_input.text().strip() or "default",
                "vad_threshold": float(self._stt_threshold_spin.value()),
                "min_speech_ms": int(self._stt_min_speech_ms_spin.value()),
                "min_silence_ms": int(self._stt_min_silence_ms_spin.value()),
                "speech_pad_ms": int(self._stt_speech_pad_ms_spin.value()),
                "max_utterance_seconds": float(self._stt_max_utt_spin.value()),
                "model": self._stt_model_combo.currentText().strip(),
                "language": self._stt_language_input.text().strip() or "ko",
                "transcribe_prompt": self._stt_prompt_text.toPlainText().strip(),
                "lookback_seconds": float(self._stt_lookback_spin.value()),
                "min_transcript_chars": int(self._stt_min_chars_spin.value()),
                "default_weight": self._stt_weight_combo.currentText(),
            }

            config_path = Path(__file__).parent.parent.parent / "config.yaml"
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = _yaml.safe_load(f)
            old_enabled = bool((cfg.get("stt") or {}).get("enabled", False))
            cfg["stt"] = new_stt
            with open(config_path, "w", encoding="utf-8") as f:
                _yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

            # 인-메모리 config도 동기화 (다음 패널 오픈 시 일관성)
            self._config["stt"] = new_stt

            # 즉시 적용 가능한 항목: ai_process의 lookback/min_chars/default_weight
            # (브로드캐스트 큐로 전달 — main.py 의 broadcast_queue가 모든 프로세스에 분배)
            self._settings_queue.put({
                "stt_runtime": {
                    "lookback_seconds": new_stt["lookback_seconds"],
                    "min_transcript_chars": new_stt["min_transcript_chars"],
                    "default_weight": new_stt["default_weight"],
                },
            })

            new_enabled = new_stt["enabled"]
            if old_enabled != new_enabled:
                self._stt_status.setText(
                    f"저장 완료. enabled {old_enabled} → {new_enabled} 변경은 재시작 후 적용됩니다."
                )
                self._stt_status.setStyleSheet("color: #f4b400; font-size: 10px;")
            else:
                self._stt_status.setText("저장 완료. 런타임 항목은 즉시 적용됩니다.")
                self._stt_status.setStyleSheet("color: #6BCB77; font-size: 10px;")
            logger.info(f"STT 설정 저장: {new_stt}")
        except Exception as e:
            logger.error(f"STT 설정 저장 실패: {e}", exc_info=True)
            self._stt_status.setText(f"저장 실패: {e}")
            self._stt_status.setStyleSheet("color: #ff6b6b; font-size: 10px;")

    def set_profile_name(self, name: str):
        self._profile_status.setText(f"현재 프로필: {name}")
        # 콤보박스에서 해당 프로필 선택 (있으면)
        for i in range(self._profile_combo.count()):
            if self._profile_combo.itemText(i) == name or self._profile_combo.itemData(i) == name:
                self._profile_combo.setCurrentIndex(i)
                return


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
        self.setMinimumSize(220, 875)
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
