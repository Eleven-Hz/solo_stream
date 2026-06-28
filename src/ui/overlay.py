"""
PyQt6 기반 투명 채팅 오버레이.
- Always-on-top / 드래그 이동 / 우하단 리사이즈
- 상단 타이틀바: ⚙ 설정, ✕ 종료
- 설정 패널: 캡처 창 선택, 게임 프로필 자동 생성 트리거
"""
import logging
from multiprocessing import Queue

from PyQt6.QtCore import Qt, QTimer, QPoint, QSize, QObject, QThread, pyqtSignal
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
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src import __version__ as APP_VERSION
from src.utils import paths

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

        # 프로필 불일치 등 경고 아이콘. 기본 숨김, set_warning()으로 노출.
        # 채팅창을 어지럽히지 않도록 경고는 여기(타이틀바)에만 띄운다.
        # 클릭하면 on_warning_click 콜백 실행(설정의 '게임' 탭 열기 등).
        self.on_warning_click = None
        self._warning_icon = QLabel("⚠")
        self._warning_icon.setStyleSheet("color: #f4b400; font-size: 12px;")
        self._warning_icon.setCursor(Qt.CursorShape.PointingHandCursor)
        self._warning_icon.setVisible(False)
        self._warning_icon.mousePressEvent = self._on_warning_clicked
        layout.addWidget(self._warning_icon)

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

    def set_warning(self, text: str | None):
        """경고 아이콘 표시/숨김. text 가 있으면 ⚠ 를 띄우고 툴팁으로 상세 표시,
        None/빈 문자열이면 숨긴다."""
        if text:
            self._warning_icon.setToolTip(text)
            self._warning_icon.setVisible(True)
        else:
            self._warning_icon.setToolTip("")
            self._warning_icon.setVisible(False)

    def _on_warning_clicked(self, event):
        if callable(self.on_warning_click):
            self.on_warning_click()


def _parse_device(device: str):
    """STT device 설정 문자열을 sounddevice 인자로 변환.
    'default'/'' → None, 숫자 문자열 → int 인덱스, 그 외 → 이름 문자열."""
    d = (device or "").strip()
    if not d or d.lower() == "default":
        return None
    try:
        return int(d)
    except ValueError:
        return d


class _MicTestWorker(QObject):
    """마이크 입력 레벨을 측정하는 워커 (UI 프로세스 내 독립 스트림).

    STT 프로세스와 무관하게 sounddevice InputStream만 잠깐 열어 RMS 레벨을
    계산해 시그널로 흘린다. silero-vad 로딩 없이 가벼워서 '내 목소리가 잡히나'를
    시작 전에도 즉시 확인할 수 있다.
    """
    level = pyqtSignal(float)   # 0.0 ~ 1.0 정규화 레벨
    error = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, device, sample_rate: int = 16000):
        super().__init__()
        self._device = device
        self._sample_rate = sample_rate
        self._running = False

    def stop(self):
        self._running = False

    def run(self):
        try:
            import numpy as np
            import sounddevice as sd
        except Exception as e:
            self.error.emit(f"오디오 라이브러리 로드 실패: {e}")
            self.stopped.emit()
            return

        sr = self._sample_rate
        block = 1024
        try:
            stream = sd.InputStream(
                samplerate=sr, channels=1, dtype="float32",
                device=self._device, blocksize=block,
            )
        except Exception as e:
            self.error.emit(f"마이크 열기 실패: {e}")
            self.stopped.emit()
            return

        self._running = True
        try:
            with stream:
                while self._running:
                    try:
                        data, _ = stream.read(block)
                    except Exception as e:
                        self.error.emit(f"마이크 읽기 오류: {e}")
                        break
                    pcm = data.reshape(-1)
                    if pcm.size == 0:
                        continue
                    # RMS → 대략 0~1 레벨로 스케일 (말소리 RMS가 보통 0.02~0.2 범위라
                    # ×8 정도로 키워 미터가 의미 있게 움직이게 한다)
                    rms = float(np.sqrt(np.mean(np.square(pcm))))
                    self.level.emit(min(rms * 8.0, 1.0))
        except Exception as e:
            self.error.emit(f"마이크 스트림 오류: {e}")
        finally:
            self.stopped.emit()


class _STTTestWorker(QObject):
    """선택한 장치로 N초 녹음 후 현재 provider/model로 받아쓰기하는 워커.

    실제 STTPipeline/VAD 없이 단발 녹음 → create_transcriber().transcribe() 로
    '받아쓰기가 되는지'를 확인한다. API 키·요금이 실제로 사용된다.
    """
    progress = pyqtSignal(str)              # 진행 상태 메시지
    finished = pyqtSignal(str, float)       # (transcript, elapsed_s)
    error = pyqtSignal(str)

    def __init__(self, *, device, sample_rate, record_seconds,
                 provider, model, language, prompt,
                 gemini_key, openai_key,
                 normalize_target_level, normalize_percentile, normalize_max_gain):
        super().__init__()
        self._device = device
        self._sample_rate = sample_rate
        self._record_seconds = record_seconds
        self._provider = provider
        self._model = model
        self._language = language
        self._prompt = prompt
        self._gemini_key = gemini_key
        self._openai_key = openai_key
        self._norm = (normalize_target_level, normalize_percentile, normalize_max_gain)

    def run(self):
        try:
            import numpy as np
            import sounddevice as sd
            from src.stt.transcriber import create_transcriber
        except Exception as e:
            self.error.emit(f"라이브러리 로드 실패: {e}")
            return

        # 1) 녹음
        self.progress.emit("🔴 녹음 중... 지금 말하세요.")
        sr = self._sample_rate
        frames = int(sr * self._record_seconds)
        try:
            rec = sd.rec(frames, samplerate=sr, channels=1, dtype="float32",
                         device=self._device)
            sd.wait()
        except Exception as e:
            self.error.emit(f"녹음 실패: {e}")
            return
        pcm = np.asarray(rec, dtype=np.float32).reshape(-1)
        if pcm.size == 0:
            self.error.emit("녹음된 오디오가 없습니다. 입력 장치를 확인하세요.")
            return

        # 2) transcribe
        self.progress.emit("⏳ 받아쓰기 중...")
        try:
            transcriber = create_transcriber(
                self._provider,
                gemini_api_key=self._gemini_key,
                openai_api_key=self._openai_key,
                model=self._model,
                language=self._language,
                prompt=self._prompt or None,
                normalize_target_level=self._norm[0],
                normalize_percentile=self._norm[1],
                normalize_max_gain=self._norm[2],
            )
            text, elapsed = transcriber.transcribe(pcm, sr)
        except Exception as e:
            self.error.emit(f"받아쓰기 실패: {e}")
            return
        self.finished.emit(text, elapsed)


class SettingsPanel(QWidget):
    _COMBO_STYLE = """
        QComboBox {
            background: #1e1e2e;
            color: #ddd;
            border: 1px solid #444;
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 13px;
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
    # STT provider별 transcribe 모델 후보.
    # gemini는 generate_content(오디오 이해)로 받아쓰기가 되는 멀티모달 모델만 포함.
    # (live/tts/native-audio 변형은 다른 API라 GeminiTranscriber로 동작하지 않아 제외)
    _STT_MODELS = {
        "gemini": [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.5-pro",
            "gemini-3-flash-preview",
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash",
        ],
        "openai": [
            "gpt-4o-mini-transcribe",
            "gpt-4o-transcribe",
            "whisper-1",
        ],
    }

    def __init__(self, settings_queue: Queue, config: dict, parent=None, onboarding: bool = False):
        super().__init__(parent)
        self._settings_queue = settings_queue
        self._config = config
        self._onboarding = onboarding
        # STT 인라인 표시 토글 변경을 ChatOverlay로 알리는 콜백(런타임 즉시 반영).
        self.on_show_stt_changed = None
        self._excludes: list[str] = list(config.get("ui", {}).get("window_excludes", []))
        # 가시성: 패널 전체에 기준 폰트를 키워 둔다. 개별 위젯의 인라인 font-size가
        # 이를 덮어쓰는 경우가 많아, 인라인 px 값들도 한 단계씩 상향했다.
        base_font = QFont()
        base_font.setPointSize(11)
        self.setFont(base_font)
        # 글자를 키운 만큼 버튼/콤보가 잘리지 않도록 설정 패널 최소 폭을 보장한다.
        self.setMinimumWidth(420)
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
                font-size: 13px;
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

        # 각 탭의 내용은 별도 메서드로 빌드.
        # 시작하기 탭은 기존 탭들의 핸들러를 재사용하므로, 기존 탭을 먼저 빌드해
        # 핸들러/위젯이 준비된 뒤 맨 앞(index 0)에 끼워 넣는다.
        self._tabs.addTab(self._build_tab_game(), "게임")
        self._tabs.addTab(self._build_tab_ai(), "AI 모델")
        self._tabs.addTab(self._build_tab_stt(), "음성")
        # 비용 탭은 위 탭들의 모델 콤보를 참조하므로 마지막에 빌드한다.
        self._tabs.addTab(self._build_tab_cost(), "비용")
        # 시작하기 탭을 맨 앞에 삽입 (처음 쓰는 사람을 1→2→3 순서로 유도)
        self._tabs.insertTab(0, self._build_tab_start(), "시작하기")

        # 모든 위젯 초기화 후 캡처 창 목록 새로고침
        self._refresh_windows()
        self._refresh_start_status()

        # 첫 실행 온보딩: '시작하기' 탭을 기본으로 연다.
        self._tabs.setCurrentIndex(0)

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
            self._build_section_logging(layout)
            self._build_section_about(layout)
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

    def _build_tab_cost(self) -> QWidget:
        def _body(layout: QVBoxLayout):
            self._build_section_cost(layout)
        return self._make_scroll_tab(_body)

    # ── 시작하기 탭 (온보딩 1→2→3) ──────────────────────────────────────────

    def _build_tab_start(self) -> QWidget:
        def _body(layout: QVBoxLayout):
            intro = QLabel("처음 사용하시나요? 아래 순서대로 진행하면 됩니다.")
            intro.setStyleSheet("color: #ddd; font-size: 13px;")
            intro.setWordWrap(True)
            layout.addWidget(intro)

            self._build_start_step_keys(layout)
            self._build_start_step_window(layout)
            self._build_start_step_profile(layout)

            # 마무리 안내
            sep = QLabel(); sep.setFixedHeight(1)
            sep.setStyleSheet("background: rgba(255,255,255,20);")
            layout.addWidget(sep)
            done = QLabel("준비가 끝나면 우측 상단의 ▶ 시작 버튼을 누르세요.")
            done.setStyleSheet("color: #6BCB77; font-size: 12px;")
            done.setWordWrap(True)
            layout.addWidget(done)
        return self._make_scroll_tab(_body)

    def _start_step_header(self, layout: QVBoxLayout, title: str) -> QLabel:
        """'① 제목                [상태]' 형태의 스텝 헤더. 상태 QLabel을 반환."""
        row = QHBoxLayout()
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color: #ddd; font-size: 13px; font-weight: bold;")
        row.addWidget(title_lbl)
        row.addStretch()
        status = QLabel("—")
        status.setStyleSheet("color: #888; font-size: 12px;")
        row.addWidget(status)
        layout.addLayout(row)
        return status

    def _build_start_step_keys(self, layout: QVBoxLayout):
        self._start_key_status = self._start_step_header(layout, "① API 키 입력")
        desc = QLabel("Gemini 키를 입력하고 저장하세요. 저장 후에는 다시 시작해야 적용됩니다.\n"
                      "키 발급: https://aistudio.google.com/apikey")
        desc.setStyleSheet("color: #888; font-size: 12px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        row = QHBoxLayout()
        self._start_gemini_input = QLineEdit()
        self._start_gemini_input.setPlaceholderText("GEMINI_API_KEY")
        self._start_gemini_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._start_gemini_input.setStyleSheet(
            "QLineEdit { background: #1e1e2e; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 3px 6px; font-size: 12px; }"
        )
        self._start_gemini_input.returnPressed.connect(self._start_save_key)
        row.addWidget(self._start_gemini_input)
        save_btn = QPushButton("저장")
        save_btn.setStyleSheet(self._btn_style("#1a3a1a", "#2d6a2d"))
        save_btn.clicked.connect(self._start_save_key)
        row.addWidget(save_btn)
        layout.addLayout(row)

        # 키 저장 후 재시작 버튼 (저장 성공 시에만 노출)
        self._start_restart_btn = QPushButton("지금 다시 시작")
        self._start_restart_btn.setStyleSheet(self._btn_style("#1a3a5a", "#2a5a8a"))
        self._start_restart_btn.clicked.connect(self._restart_app)
        self._start_restart_btn.setVisible(False)
        layout.addWidget(self._start_restart_btn)

        sep = QLabel(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep)

    def _build_start_step_window(self, layout: QVBoxLayout):
        self._start_window_status = self._start_step_header(layout, "② 게임 창 선택 (창모드)")
        desc = QLabel("게임을 창모드로 켠 뒤, 목록에서 선택하고 '캡처 적용'을 누르세요.")
        desc.setStyleSheet("color: #888; font-size: 12px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._start_window_combo = QComboBox()
        self._start_window_combo.setStyleSheet(self._COMBO_STYLE)
        self._constrain_combo(self._start_window_combo)
        layout.addWidget(self._start_window_combo)

        row = QHBoxLayout()
        refresh_btn = QPushButton("새로고침")
        refresh_btn.setStyleSheet(self._btn_style("#333", "#555"))
        refresh_btn.clicked.connect(self._refresh_windows)
        row.addWidget(refresh_btn)
        apply_btn = QPushButton("캡처 적용")
        apply_btn.setStyleSheet(self._btn_style("#1a472a", "#2d6a4f"))
        apply_btn.clicked.connect(self._start_apply_window)
        row.addWidget(apply_btn)
        layout.addLayout(row)

        sep = QLabel(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep)

    def _build_start_step_profile(self, layout: QVBoxLayout):
        self._start_profile_status_dot = self._start_step_header(layout, "③ 게임 프로필 생성")
        desc = QLabel("선택한 게임의 공략·용어 컨텍스트를 자동 생성합니다 (수십 초 소요).")
        desc.setStyleSheet("color: #888; font-size: 12px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        regen_btn = QPushButton("선택 창 게임 프로필 생성/재생성")
        regen_btn.setStyleSheet(self._btn_style("#2d1b69", "#4a2fa0"))
        regen_btn.clicked.connect(self._start_regenerate_profile)
        layout.addWidget(regen_btn)

        self._start_profile_status = QLabel("현재: 로딩 중...")
        self._start_profile_status.setStyleSheet("color: #888; font-size: 12px;")
        self._start_profile_status.setWordWrap(True)
        layout.addWidget(self._start_profile_status)

    # ── 시작하기 탭 핸들러 (기존 로직 재사용) ───────────────────────────────

    def _start_save_key(self):
        """시작 탭의 Gemini 키를 AI 탭 입력란에 옮겨 기존 저장 로직을 재사용한다."""
        key = self._start_gemini_input.text().strip()
        if not key:
            self._start_key_status.setText("⚠ 키 입력 필요")
            self._start_key_status.setStyleSheet("color: #f4b400; font-size: 12px;")
            return
        self._gemini_key_input.setText(key)
        self._save_api_keys()  # .env.local 저장 + 상태/재시작 버튼 처리(AI 탭)
        self._start_gemini_input.clear()
        self._start_key_status.setText("✓ 저장됨")
        self._start_key_status.setStyleSheet("color: #6BCB77; font-size: 12px;")
        if getattr(self, "_start_restart_btn", None) is not None:
            self._start_restart_btn.setVisible(True)

    def _start_apply_window(self):
        title = self._start_window_combo.currentText()
        if not title:
            return
        self._settings_queue.put({"target_window": title})
        # 게임 프로필이 자동 감지되도록 게임명도 함께 전달
        game_name = _extract_game_name(title)
        self._settings_queue.put({"game_name": game_name})
        # 재시작 시 복원되도록 마지막 선택을 디스크에 저장.
        paths.save_last_session(target_window=title, game_name=game_name)
        self._start_window_status.setText("✓ 적용됨")
        self._start_window_status.setStyleSheet("color: #6BCB77; font-size: 12px;")
        self._start_profile_status.setText(f"적용 중: {title}\n게임 프로필 자동 감지 중...")
        logger.info(f"[시작탭] 창 변경 적용: {title}")

    def _start_regenerate_profile(self):
        title = self._start_window_combo.currentText()
        if not title:
            self._start_profile_status.setText("먼저 ② 에서 게임 창을 선택하세요.")
            self._start_profile_status.setStyleSheet("color: #f4b400; font-size: 12px;")
            return
        game_name = _extract_game_name(title)
        import re
        profile_dir = paths.user_data_dir() / "game_profiles"
        safe = re.sub(r'[\\/:*?"<>|]', "", game_name).strip().lower().replace(" ", "_")[:64]
        profile_path = profile_dir / f"{safe}.yaml"
        if profile_path.exists():
            profile_path.unlink()
            logger.info(f"[시작탭] 기존 프로필 삭제: {profile_path.name}")
        self._settings_queue.put({"game_name": game_name})
        paths.save_last_session(game_name=game_name)
        self._start_profile_status.setText(f"재생성 중: {game_name}...")
        self._start_profile_status.setStyleSheet("color: #f4b400; font-size: 12px;")

    def _refresh_start_status(self):
        """시작 탭 ①②③ 상태 표시 갱신. 위젯이 아직 없으면 무시."""
        # ① 키: 실행 중 저장한 키는 os.getenv에 안 잡히므로 .env.local 파일을 직접 본다.
        if getattr(self, "_start_key_status", None) is not None:
            has_key = self._env_has_gemini_key()
            if has_key:
                self._start_key_status.setText("✓ Gemini 키 감지됨")
                self._start_key_status.setStyleSheet("color: #6BCB77; font-size: 12px;")
            else:
                self._start_key_status.setText("⚠ 미설정")
                self._start_key_status.setStyleSheet("color: #f4b400; font-size: 12px;")
        # ② 창: 적용 여부는 사용자가 누른 뒤에만 ✓ 로 바뀐다(여기선 초기 상태만).
        if getattr(self, "_start_window_status", None) is not None:
            if not self._start_window_status.text().startswith("✓"):
                self._start_window_status.setText("⚠ 미선택")
                self._start_window_status.setStyleSheet("color: #f4b400; font-size: 12px;")

    def _env_has_gemini_key(self) -> bool:
        try:
            env_path = paths.user_data_dir() / ".env.local"
            if not env_path.exists():
                import os
                return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API"))
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GEMINI_API_KEY=") or line.startswith("GEMINI_API="):
                        return bool(line.split("=", 1)[1].strip())
            return False
        except Exception:
            return False

    # ── 게임 탭 섹션 ─────────────────────────────────────────────────────

    def _build_section_window(self, layout: QVBoxLayout):
        # 창 선택
        win_label = QLabel("캡처 창 선택")
        win_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(win_label)

        self._window_combo = QComboBox()
        self._window_combo.setStyleSheet("""
            QComboBox {
                background: #1e1e2e;
                color: #ddd;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #1e1e2e;
                color: #ddd;
                selection-background-color: #333;
            }
        """)
        self._constrain_combo(self._window_combo)
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
        excl_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(excl_label)

        hint = QLabel("%단어% 포함  /  단어% 시작  /  %단어 끝")
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        self._exclude_list = QListWidget()
        self._exclude_list.setFixedHeight(80)
        self._exclude_list.setStyleSheet("""
            QListWidget {
                background: #111118;
                color: #ccc;
                border: 1px solid #444;
                border-radius: 4px;
                font-size: 12px;
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
            "border-radius: 4px; padding: 3px 6px; font-size: 12px; }"
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
        ai_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(ai_label)

        # provider 행
        provider_row = QHBoxLayout()
        provider_lbl = QLabel("Provider")
        provider_lbl.setStyleSheet("color: #888; font-size: 12px;")
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
        model_lbl.setStyleSheet("color: #888; font-size: 12px;")
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
        chat_model_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(chat_model_label)

        chat_provider_row = QHBoxLayout()
        chat_provider_lbl = QLabel("Provider")
        chat_provider_lbl.setStyleSheet("color: #888; font-size: 12px;")
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
        chat_model_lbl.setStyleSheet("color: #888; font-size: 12px;")
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
        api_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(api_label)

        # 첫 실행 온보딩 안내 배너 (키 미설정으로 AI/STT 비활성 상태).
        if getattr(self, "_onboarding", False):
            onboard = QLabel(
                "⚠ Gemini API 키가 없어 AI·음성 인식이 꺼져 있습니다.\n"
                "아래에 Gemini 키를 입력하고 '저장'을 누른 뒤 프로그램을 다시 실행하세요.\n"
                "키 발급: https://aistudio.google.com/apikey"
            )
            onboard.setWordWrap(True)
            onboard.setStyleSheet(
                "QLabel { background: #3a2a1a; color: #ffcf8a; border: 1px solid #6a4a2a; "
                "border-radius: 4px; padding: 6px 8px; font-size: 12px; }"
            )
            layout.addWidget(onboard)

        gemini_row = QHBoxLayout()
        gemini_lbl = QLabel("Gemini")
        gemini_lbl.setStyleSheet("color: #888; font-size: 12px;")
        gemini_lbl.setFixedWidth(42)
        gemini_row.addWidget(gemini_lbl)
        self._gemini_key_input = QLineEdit()
        self._gemini_key_input.setPlaceholderText("GEMINI_API_KEY")
        self._gemini_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._gemini_key_input.setStyleSheet(
            "QLineEdit { background: #1e1e2e; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 3px 6px; font-size: 12px; }"
        )
        gemini_row.addWidget(self._gemini_key_input)
        layout.addLayout(gemini_row)

        claude_row = QHBoxLayout()
        claude_lbl = QLabel("Claude")
        claude_lbl.setStyleSheet("color: #888; font-size: 12px;")
        claude_lbl.setFixedWidth(42)
        claude_row.addWidget(claude_lbl)
        self._claude_key_input = QLineEdit()
        self._claude_key_input.setPlaceholderText("CLAUDE_API_KEY")
        self._claude_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._claude_key_input.setStyleSheet(
            "QLineEdit { background: #1e1e2e; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 3px 6px; font-size: 12px; }"
        )
        claude_row.addWidget(self._claude_key_input)
        layout.addLayout(claude_row)

        openai_row = QHBoxLayout()
        openai_lbl = QLabel("OpenAI")
        openai_lbl.setStyleSheet("color: #888; font-size: 12px;")
        openai_lbl.setFixedWidth(42)
        openai_row.addWidget(openai_lbl)
        self._openai_key_input = QLineEdit()
        self._openai_key_input.setPlaceholderText("OPENAI_API_KEY")
        self._openai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._openai_key_input.setStyleSheet(
            "QLineEdit { background: #1e1e2e; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 3px 6px; font-size: 12px; }"
        )
        openai_row.addWidget(self._openai_key_input)
        layout.addLayout(openai_row)

        save_keys_btn = QPushButton("API 키 저장 (.env.local)")
        save_keys_btn.setStyleSheet(self._btn_style("#1a3a1a", "#2d6a2d"))
        save_keys_btn.clicked.connect(self._save_api_keys)
        layout.addWidget(save_keys_btn)

        self._api_key_status = QLabel("")
        self._api_key_status.setWordWrap(True)
        self._api_key_status.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self._api_key_status)

        # 키 저장 후 적용하려면 재시작이 필요하다. 저장 성공 시에만 노출.
        self._restart_btn = QPushButton("지금 다시 시작")
        self._restart_btn.setStyleSheet(self._btn_style("#1a3a5a", "#2a5a8a"))
        self._restart_btn.clicked.connect(self._restart_app)
        self._restart_btn.setVisible(False)
        layout.addWidget(self._restart_btn)

    def _build_section_game_profile(self, layout: QVBoxLayout):
        # 구분선
        sep5 = QLabel()
        sep5.setFixedHeight(1)
        sep5.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep5)

        # 게임 프로필
        game_label = QLabel("게임 프로필")
        game_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(game_label)

        self._profile_combo = QComboBox()
        self._profile_combo.setStyleSheet("""
            QComboBox {
                background: #1e1e2e;
                color: #ddd;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #1e1e2e;
                color: #ddd;
                selection-background-color: #333;
            }
        """)
        self._constrain_combo(self._profile_combo)
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
        self._profile_status.setStyleSheet("color: #888; font-size: 12px;")
        self._profile_status.setWordWrap(True)
        layout.addWidget(self._profile_status)

        regen_btn = QPushButton("선택 창 게임 프로필 재생성")
        regen_btn.setStyleSheet(self._btn_style("#2d1b69", "#4a2fa0"))
        regen_btn.clicked.connect(self._regenerate_profile)
        layout.addWidget(regen_btn)

    def _build_section_logging(self, layout: QVBoxLayout):
        # 구분선
        sep = QLabel(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep)

        log_label = QLabel("로그")
        log_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(log_label)

        self._log_save_chk = QCheckBox("로그 파일 저장 (logs/session_날짜.log · chat_날짜.log)")
        self._log_save_chk.setStyleSheet(
            "QCheckBox { color: #ddd; font-size: 13px; spacing: 6px; }"
            "QCheckBox::indicator { width: 14px; height: 14px; }"
            "QCheckBox::indicator:unchecked { background: #1e1e2e; border: 1px solid #555; border-radius: 3px; }"
            "QCheckBox::indicator:checked { background: #2d6a4f; border: 1px solid #4f8a6f; border-radius: 3px; }"
        )
        cur = bool((self._config.get("logging") or {}).get("save_to_file", True))
        self._log_save_chk.setChecked(cur)
        self._log_save_chk.toggled.connect(self._save_logging_setting)
        layout.addWidget(self._log_save_chk)

        hint = QLabel("하루 1개 파일에 append, 7일 지난 로그는 실행 시 자동 삭제. 끄면 콘솔에만 출력.\n"
                      "변경은 다음 실행부터 적용됩니다.")
        hint.setStyleSheet("color: #666; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._log_status = QLabel("")
        self._log_status.setStyleSheet("color: #888; font-size: 12px;")
        self._log_status.setWordWrap(True)
        layout.addWidget(self._log_status)

        # 데이터 폴더(설정·키·로그) 탐색기에서 열기
        open_data_btn = QPushButton("데이터 폴더 열기 (설정·키·로그)")
        open_data_btn.setStyleSheet(
            "QPushButton { color: #ddd; background: rgba(40,40,55,180); border: 1px solid #555; "
            "border-radius: 4px; padding: 5px 10px; font-size: 13px; }"
            "QPushButton:hover { background: rgba(60,60,80,200); }"
        )
        open_data_btn.clicked.connect(self._open_user_data_dir)
        layout.addWidget(open_data_btn)

        data_hint = QLabel(f"위치: {paths.user_data_dir()}")
        data_hint.setStyleSheet("color: #666; font-size: 11px;")
        data_hint.setWordWrap(True)
        layout.addWidget(data_hint)

    def _open_user_data_dir(self):
        """사용자 데이터 폴더(%APPDATA%\\SoloStream)를 탐색기에서 연다."""
        import os
        import subprocess
        import sys
        try:
            udir = paths.ensure_user_data()
            if sys.platform.startswith("win"):
                os.startfile(str(udir))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(udir)])
            else:
                subprocess.Popen(["xdg-open", str(udir)])
            logger.info(f"데이터 폴더 열기: {udir}")
            self._log_status.setText(f"데이터 폴더를 열었습니다: {udir}")
            self._log_status.setStyleSheet("color: #888; font-size: 12px;")
        except Exception as e:
            logger.error(f"데이터 폴더 열기 실패: {e}")
            self._log_status.setText(f"폴더 열기 실패: {e}")
            self._log_status.setStyleSheet("color: #ff6b6b; font-size: 12px;")

    def _save_logging_setting(self, checked: bool):
        try:
            paths.update_user_config("logging", {"save_to_file": bool(checked)})
            self._config.setdefault("logging", {})["save_to_file"] = bool(checked)
            state = "저장 ON" if checked else "저장 OFF"
            self._log_status.setText(f"로그 파일 {state} — 다음 실행부터 적용됩니다.")
            self._log_status.setStyleSheet("color: #f4b400; font-size: 12px;")
            logger.info(f"로그 파일 저장 설정 변경: save_to_file={checked}")
        except Exception as e:
            logger.error(f"로그 설정 저장 실패: {e}")
            self._log_status.setText(f"저장 실패: {e}")
            self._log_status.setStyleSheet("color: #ff6b6b; font-size: 12px;")

    def _save_show_stt_history(self, checked: bool):
        """STT 인라인 표시 토글 저장(ui.show_stt_history) + 오버레이 즉시 반영."""
        try:
            paths.update_user_config("ui", {"show_stt_history": bool(checked)})
            self._config.setdefault("ui", {})["show_stt_history"] = bool(checked)
        except Exception as e:
            logger.error(f"show_stt_history 저장 실패: {e}")
        # 런타임 즉시 반영(재시작 불필요).
        if callable(self.on_show_stt_changed):
            self.on_show_stt_changed(bool(checked))

    def set_show_stt_checked(self, checked: bool):
        """우클릭 메뉴에서 토글했을 때 패널 체크박스를 맞춰주기 위한 동기화용.
        시그널 재발화로 무한 루프가 나지 않도록 blockSignals로 감싼다."""
        chk = getattr(self, "_stt_show_history_chk", None)
        if chk is not None:
            chk.blockSignals(True)
            chk.setChecked(bool(checked))
            chk.blockSignals(False)

    def _build_section_about(self, layout: QVBoxLayout):
        # 구분선
        sep = QLabel(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep)

        about = QLabel(f"Solo Stream  ·  v{APP_VERSION}")
        about.setStyleSheet("color: #777; font-size: 12px;")
        layout.addWidget(about)

        contact = QLabel("문의: contact.team8889@gmail.com")
        contact.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(contact)

    # ── 음성(STT) 탭 섹션 ───────────────────────────────────────────────

    def _build_section_stt(self, layout: QVBoxLayout):
        # 배치 순서: ① enabled → ② 입력 장치 → ③ 마이크 테스트 → ④ STT 테스트
        #            → ⑤ Transcribe 모델 → ⑥ 나머지(반응강도/lookback/VAD/프롬프트)
        stt_cfg = self._config.get("stt", {}) or {}
        self._stt_input_style = (
            "QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit { "
            "background: #1e1e2e; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 3px 6px; font-size: 13px; }"
        )

        self._build_stt_enabled(layout, stt_cfg)
        self._build_stt_device(layout, stt_cfg)
        self._build_stt_mic_test(layout)
        self._build_stt_stt_test(layout)
        self._build_stt_model(layout, stt_cfg)
        self._build_stt_rest(layout, stt_cfg)

        # 저장 버튼
        save_stt_btn = QPushButton("STT 설정 저장 (config.local.yaml)")
        save_stt_btn.setStyleSheet(self._btn_style("#1a3a5a", "#2a5a8a"))
        save_stt_btn.clicked.connect(self._save_stt_settings)
        layout.addWidget(save_stt_btn)

        self._stt_status = QLabel("")
        self._stt_status.setStyleSheet("color: #888; font-size: 12px;")
        self._stt_status.setWordWrap(True)
        layout.addWidget(self._stt_status)

    # ── 음성 탭 하위 섹션 ───────────────────────────────────────────────────

    def _stt_sep(self, layout: QVBoxLayout):
        sep = QLabel(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep)

    def _build_stt_enabled(self, layout: QVBoxLayout, stt_cfg: dict):
        title = QLabel("음성 인식 (STT)")
        title.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(title)

        self._stt_enabled_chk = QCheckBox("마이크 음성을 채팅 컨텍스트로 사용")
        self._stt_enabled_chk.setStyleSheet(
            "QCheckBox { color: #ddd; font-size: 13px; spacing: 6px; }"
            "QCheckBox::indicator { width: 14px; height: 14px; }"
            "QCheckBox::indicator:unchecked { background: #1e1e2e; border: 1px solid #555; border-radius: 3px; }"
            "QCheckBox::indicator:checked { background: #2d6a4f; border: 1px solid #4f8a6f; border-radius: 3px; }"
        )
        self._stt_enabled_chk.setChecked(bool(stt_cfg.get("enabled", False)))
        layout.addWidget(self._stt_enabled_chk)

        hint = QLabel("변경 후 'STT 설정 저장'을 누르면 config.local.yaml에 기록됩니다.\n"
                      "enabled 변경은 다음 실행부터 적용됩니다.")
        hint.setStyleSheet("color: #666; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def _build_stt_device(self, layout: QVBoxLayout, stt_cfg: dict):
        self._stt_sep(layout)
        dev_label = QLabel("입력 장치 (마이크)")
        dev_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(dev_label)

        self._stt_device_combo = QComboBox()
        self._stt_device_combo.setStyleSheet(self._COMBO_STYLE)
        self._constrain_combo(self._stt_device_combo)
        layout.addWidget(self._stt_device_combo)
        self._populate_input_devices(str(stt_cfg.get("device", "default")))

        dev_btns = QHBoxLayout()
        refresh_dev_btn = QPushButton("새로고침")
        refresh_dev_btn.setStyleSheet(self._btn_style("#333", "#555"))
        refresh_dev_btn.clicked.connect(
            lambda: self._populate_input_devices(self._selected_device_str())
        )
        dev_btns.addWidget(refresh_dev_btn)
        dev_btns.addStretch()
        layout.addLayout(dev_btns)

    def _build_stt_mic_test(self, layout: QVBoxLayout):
        self._stt_sep(layout)
        mic_label = QLabel("마이크 테스트")
        mic_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(mic_label)

        mic_hint = QLabel("선택한 입력 장치로 마이크를 잠깐 열어 입력 레벨을 봅니다. "
                          "말했을 때 막대가 움직이면 정상입니다 (▶ 시작과 무관).")
        mic_hint.setStyleSheet("color: #666; font-size: 11px;")
        mic_hint.setWordWrap(True)
        layout.addWidget(mic_hint)

        self._mic_test_btn = QPushButton("🎤 마이크 테스트 시작")
        self._mic_test_btn.setStyleSheet(self._btn_style("#1a472a", "#2d6a4f"))
        self._mic_test_btn.clicked.connect(self._toggle_mic_test)
        layout.addWidget(self._mic_test_btn)

        self._mic_level_bar = QProgressBar()
        self._mic_level_bar.setRange(0, 100)
        self._mic_level_bar.setValue(0)
        self._mic_level_bar.setTextVisible(False)
        self._mic_level_bar.setFixedHeight(14)
        self._mic_level_bar.setStyleSheet(
            "QProgressBar { background: #111118; border: 1px solid #444; border-radius: 4px; }"
            "QProgressBar::chunk { background: #6BCB77; border-radius: 3px; }"
        )
        layout.addWidget(self._mic_level_bar)

        self._mic_test_status = QLabel("")
        self._mic_test_status.setStyleSheet("color: #888; font-size: 11px;")
        self._mic_test_status.setWordWrap(True)
        layout.addWidget(self._mic_test_status)

        # 마이크 테스트 상태 보관용
        self._mic_thread = None
        self._mic_worker = None
        self._mic_peak = 0.0

    def _build_stt_stt_test(self, layout: QVBoxLayout):
        self._stt_sep(layout)
        stt_test_label = QLabel("STT 테스트 (받아쓰기)")
        stt_test_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(stt_test_label)

        stt_test_hint = QLabel("아래 버튼을 누르고 약 4초간 말하면, 현재 provider/model로 "
                               "받아쓰기한 결과를 보여줍니다. (API 키·요금 사용)")
        stt_test_hint.setStyleSheet("color: #666; font-size: 11px;")
        stt_test_hint.setWordWrap(True)
        layout.addWidget(stt_test_hint)

        self._stt_test_btn = QPushButton("📝 STT 테스트 (4초 녹음)")
        self._stt_test_btn.setStyleSheet(self._btn_style("#1a3a5a", "#2a5a8a"))
        self._stt_test_btn.clicked.connect(self._start_stt_test)
        layout.addWidget(self._stt_test_btn)

        self._stt_test_result = QLabel("")
        self._stt_test_result.setStyleSheet("color: #888; font-size: 12px;")
        self._stt_test_result.setWordWrap(True)
        layout.addWidget(self._stt_test_result)

        # STT 테스트 상태 보관용
        self._stt_test_thread = None
        self._stt_test_worker = None

    def _build_stt_model(self, layout: QVBoxLayout, stt_cfg: dict):
        _input_style = self._stt_input_style
        _combo_style = self._COMBO_STYLE

        self._stt_sep(layout)
        stt_model_label = QLabel("Transcribe 모델")
        stt_model_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(stt_model_label)

        # provider 행 (gemini | openai) — 변경 시 모델 목록 교체
        provider_row = QHBoxLayout()
        provider_lbl = QLabel("provider")
        provider_lbl.setStyleSheet("color: #888; font-size: 12px;")
        provider_lbl.setFixedWidth(110)
        provider_row.addWidget(provider_lbl)
        self._stt_provider_combo = QComboBox()
        self._stt_provider_combo.setStyleSheet(_combo_style)
        self._stt_provider_combo.addItems(["gemini", "openai"])
        cur_provider = str(stt_cfg.get("provider", "gemini")).lower()
        idx_p = self._stt_provider_combo.findText(cur_provider)
        if idx_p >= 0:
            self._stt_provider_combo.setCurrentIndex(idx_p)
        provider_row.addWidget(self._stt_provider_combo)
        layout.addLayout(provider_row)

        model_row = QHBoxLayout()
        model_lbl = QLabel("model")
        model_lbl.setStyleSheet("color: #888; font-size: 12px;")
        model_lbl.setFixedWidth(110)
        model_row.addWidget(model_lbl)
        self._stt_model_combo = QComboBox()
        self._stt_model_combo.setStyleSheet(_combo_style)
        model_row.addWidget(self._stt_model_combo)
        layout.addLayout(model_row)

        cur_model = str(stt_cfg.get("model", "gemini-2.5-flash"))

        def _on_stt_provider_changed(provider: str):
            self._stt_model_combo.clear()
            models = list(self._STT_MODELS.get(provider, []))
            # 같은 provider면 저장된 모델을, provider가 바뀌면 목록 첫 항목을 선택
            target = cur_model if provider == cur_provider else (models[0] if models else "")
            # 목록에 없는(예: config에 직접 적은) 모델도 선택지로 노출해 드롭다운만으로 고르게 함
            if target and target not in models:
                models.append(target)
            self._stt_model_combo.addItems(models)
            idx_m = self._stt_model_combo.findText(target)
            if idx_m >= 0:
                self._stt_model_combo.setCurrentIndex(idx_m)

        self._stt_provider_combo.currentTextChanged.connect(_on_stt_provider_changed)
        _on_stt_provider_changed(cur_provider)  # 초기 목록 채우기

        lang_row = QHBoxLayout()
        lang_lbl = QLabel("language")
        lang_lbl.setStyleSheet("color: #888; font-size: 12px;")
        lang_lbl.setFixedWidth(110)
        lang_row.addWidget(lang_lbl)
        self._stt_language_input = QLineEdit()
        self._stt_language_input.setStyleSheet(_input_style)
        self._stt_language_input.setText(str(stt_cfg.get("language", "ko")))
        lang_row.addWidget(self._stt_language_input)
        layout.addLayout(lang_row)

    def _build_stt_rest(self, layout: QVBoxLayout, stt_cfg: dict):
        _input_style = self._stt_input_style
        _combo_style = self._COMBO_STYLE

        self._stt_sep(layout)
        rest_label = QLabel("반응/감지 설정")
        rest_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(rest_label)

        # default_weight
        weight_row = QHBoxLayout()
        weight_lbl = QLabel("기본 반응 강도")
        weight_lbl.setStyleSheet("color: #bbb; font-size: 13px;")
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
        w_hint.setStyleSheet("color: #666; font-size: 11px;")
        w_hint.setWordWrap(True)
        layout.addWidget(w_hint)

        # lookback_seconds
        lb_row = QHBoxLayout()
        lb_lbl = QLabel("lookback (초)")
        lb_lbl.setStyleSheet("color: #bbb; font-size: 13px;")
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
        lb_hint.setStyleSheet("color: #666; font-size: 11px;")
        lb_hint.setWordWrap(True)
        layout.addWidget(lb_hint)

        # min_transcript_chars
        mc_row = QHBoxLayout()
        mc_lbl = QLabel("최소 글자 수")
        mc_lbl.setStyleSheet("color: #bbb; font-size: 13px;")
        mc_lbl.setFixedWidth(110)
        mc_row.addWidget(mc_lbl)
        self._stt_min_chars_spin = QSpinBox()
        self._stt_min_chars_spin.setStyleSheet(_input_style)
        self._stt_min_chars_spin.setRange(0, 50)
        self._stt_min_chars_spin.setValue(int(stt_cfg.get("min_transcript_chars", 2)))
        mc_row.addWidget(self._stt_min_chars_spin)
        layout.addLayout(mc_row)

        # 구분선
        self._stt_sep(layout)

        # VAD 파라미터
        vad_label = QLabel("VAD 파라미터")
        vad_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(vad_label)

        # threshold
        th_row = QHBoxLayout()
        th_lbl = QLabel("threshold")
        th_lbl.setStyleSheet("color: #888; font-size: 12px;")
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

        # min_speech_ms / min_silence_ms / speech_pad_ms
        for label_text, key, default in [
            ("min_speech (ms)", "min_speech_ms", 250),
            ("min_silence (ms)", "min_silence_ms", 500),
            ("speech_pad (ms)", "speech_pad_ms", 100),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #888; font-size: 12px;")
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
        mu_lbl.setStyleSheet("color: #888; font-size: 12px;")
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

        # transcribe_prompt
        self._stt_sep(layout)
        prompt_label = QLabel("Transcribe 프롬프트")
        prompt_label.setStyleSheet("color: #bbb; font-size: 13px;")
        layout.addWidget(prompt_label)

        self._stt_prompt_text = QPlainTextEdit()
        self._stt_prompt_text.setStyleSheet(_input_style)
        self._stt_prompt_text.setFixedHeight(70)
        self._stt_prompt_text.setPlainText(str(stt_cfg.get("transcribe_prompt", "")))
        layout.addWidget(self._stt_prompt_text)

        # 부가기능: STT 받아쓰기를 채팅창에 항상 회색 인라인으로 표시.
        # 표시는 UI 설정이라 ui.show_stt_history 에 저장한다(STT 파이프라인과 무관).
        self._stt_sep(layout)
        addon_label = QLabel("부가기능")
        addon_label.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(addon_label)

        self._stt_show_history_chk = QCheckBox("STT 받아쓰기 항상 보기")
        self._stt_show_history_chk.setStyleSheet(
            "QCheckBox { color: #ddd; font-size: 13px; spacing: 6px; }"
            "QCheckBox::indicator { width: 14px; height: 14px; }"
            "QCheckBox::indicator:unchecked { background: #1e1e2e; border: 1px solid #555; border-radius: 3px; }"
            "QCheckBox::indicator:checked { background: #2d6a4f; border: 1px solid #4f8a6f; border-radius: 3px; }"
        )
        cur_show = bool((self._config.get("ui") or {}).get("show_stt_history", False))
        self._stt_show_history_chk.setChecked(cur_show)
        self._stt_show_history_chk.toggled.connect(self._save_show_stt_history)
        layout.addWidget(self._stt_show_history_chk)

        sh_hint = QLabel("불안정한 받아쓰기를 즉시 보고 잘못 들렸는지 확인할 때 켜세요.\n"
                         "채팅창 우클릭 메뉴에서도 켜고 끌 수 있습니다.")
        sh_hint.setStyleSheet("color: #666; font-size: 11px;")
        sh_hint.setWordWrap(True)
        layout.addWidget(sh_hint)

    # ── 입력 장치 콤보 ──────────────────────────────────────────────────────

    def _populate_input_devices(self, want: str):
        """입력 장치 목록을 콤보에 채운다. want(저장된 device 문자열)를 선택.
        항목 userData에 device 값 문자열('default'/'3' 등)을 담는다."""
        combo = self._stt_device_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("기본 장치 (시스템 설정)", userData="default")
        try:
            import sounddevice as sd
            for idx, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    combo.addItem(f"[{idx}] {dev['name']}", userData=str(idx))
        except Exception as e:
            logger.warning(f"입력 장치 조회 실패: {e}")
        # want 와 일치하는 항목 선택 (userData 우선, 없으면 default)
        target_idx = 0
        for i in range(combo.count()):
            if str(combo.itemData(i)) == str(want):
                target_idx = i
                break
        combo.setCurrentIndex(target_idx)
        combo.blockSignals(False)

    def _selected_device_str(self) -> str:
        """현재 선택된 입력 장치의 device 설정 문자열('default'/'3' 등)."""
        combo = getattr(self, "_stt_device_combo", None)
        if combo is None:
            return "default"
        data = combo.currentData()
        return str(data) if data is not None else "default"

    # ── 비용 탭 섹션 ─────────────────────────────────────────────────────

    def _build_section_cost(self, layout: QVBoxLayout):
        from src.utils import pricing

        _input_style = (
            "QSpinBox, QDoubleSpinBox { background: #1e1e2e; color: #ddd; "
            "border: 1px solid #444; border-radius: 4px; padding: 3px 6px; font-size: 13px; }"
        )

        title = QLabel("예상 비용 (현재 선택 모델 기준)")
        title.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(title)

        note = QLabel(
            f"AI/음성 탭에서 고른 모델 기준으로 대략 비용을 추정합니다(저장 안 해도 반영). "
            f"단가 {pricing.PRICING_AS_OF} 공개가, 토큰량은 추정치라 실제 청구액과 다를 수 있습니다."
        )
        note.setStyleSheet("color: #666; font-size: 11px;")
        note.setWordWrap(True)
        layout.addWidget(note)

        # 사용량 가정 입력
        sep = QLabel(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep)

        usage_lbl = QLabel("사용량 가정")
        usage_lbl.setStyleSheet("color: #bbb; font-size: 13px; font-weight: bold;")
        layout.addWidget(usage_lbl)

        d = pricing.USAGE_DEFAULTS

        def _spin(label_text, default, lo, hi, step, decimals, hint):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #888; font-size: 12px;")
            lbl.setFixedWidth(130)
            row.addWidget(lbl)
            spin = QDoubleSpinBox()
            spin.setStyleSheet(_input_style)
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
            spin.setValue(default)
            spin.valueChanged.connect(self._recompute_cost)
            row.addWidget(spin)
            layout.addLayout(row)
            if hint:
                h = QLabel(hint)
                h.setStyleSheet("color: #666; font-size: 11px;")
                h.setWordWrap(True)
                layout.addWidget(h)
            return spin

        self._cost_hours_spin = _spin(
            "세션 길이 (시간)", float(d["hours"]), 0.5, 24.0, 0.5, 1, None)
        self._cost_speech_spin = _spin(
            "발화량 (분/시간)", float(d["speech_min_per_hour"]), 0.0, 60.0, 5.0, 0,
            "시간당 실제 마이크 발화 분량 → STT 비용")
        self._cost_change_spin = _spin(
            "화면 변화율 (%)", float(d["screen_change_pct"]), 0.0, 100.0, 5.0, 0,
            "캡처 주기 중 화면이 바뀌어 이미지 분석이 실제로 호출되는 비율")
        self._cost_chat_spin = _spin(
            "채팅 생성 (회/시간)", float(d["chat_calls_per_hour"]), 0.0, 1000.0, 10.0, 0,
            "시간당 봇 채팅 생성 횟수")

        # 결과 출력
        sep2 = QLabel(); sep2.setFixedHeight(1)
        sep2.setStyleSheet("background: rgba(255,255,255,20);")
        layout.addWidget(sep2)

        self._cost_output = QLabel()
        self._cost_output.setTextFormat(Qt.TextFormat.RichText)
        self._cost_output.setWordWrap(True)
        self._cost_output.setStyleSheet("color: #ddd; font-size: 13px;")
        layout.addWidget(self._cost_output)

        # 모델 콤보가 바뀌면 비용도 갱신 (AI/음성 탭에서 선택만 바꿔도 반영)
        for combo in (self._vision_model_combo, self._chat_model_combo,
                      self._stt_model_combo, self._provider_combo,
                      self._chat_provider_combo, self._stt_provider_combo):
            combo.currentTextChanged.connect(self._recompute_cost)

        self._recompute_cost()

    def _recompute_cost(self, *args):
        """현재 선택 모델 + 사용량 가정으로 비용 분해를 다시 그린다."""
        if not hasattr(self, "_cost_output"):
            return
        from src.utils import pricing

        vision_model = self._vision_model_combo.currentText()
        chat_model = self._chat_model_combo.currentText()
        stt_model = self._stt_model_combo.currentText()
        interval = float(self._config.get("capture", {}).get("interval_seconds", 15) or 15)

        est = pricing.estimate_costs(
            vision_model=vision_model,
            chat_model=chat_model,
            stt_model=stt_model,
            capture_interval_s=interval,
            hours=self._cost_hours_spin.value(),
            speech_min_per_hour=self._cost_speech_spin.value(),
            screen_change_pct=self._cost_change_spin.value(),
            chat_calls_per_hour=self._cost_chat_spin.value(),
        )

        f = pricing.format_usd

        def _row(name, block, rate_text):
            model = block["model"] or "—"
            if block["known"]:
                per_hr = f(block["per_hour"])
                per_sess = f(block["per_session"])
            else:
                per_hr = per_sess = "<span style='color:#f4b400;'>?</span>"
            return (
                f"<tr>"
                f"<td style='padding:3px 8px 3px 0;color:#bbb;'>{name}</td>"
                f"<td style='padding:3px 8px;color:#9aa;'>{model}</td>"
                f"<td style='padding:3px 8px;color:#888;'>{rate_text}</td>"
                f"<td style='padding:3px 8px;text-align:right;color:#6BCB77;'>{per_hr}</td>"
                f"<td style='padding:3px 0 3px 8px;text-align:right;color:#9aa;'>{per_sess}</td>"
                f"</tr>"
            )

        v, c, s = est["vision"], est["chat"], est["stt"]
        rows = (
            _row("이미지 분석", v, f"{v['calls_per_hour']:.0f}회/시간")
            + _row("채팅 생성", c, f"{c['calls_per_hour']:.0f}회/시간")
            + _row("음성 STT", s, f"{s['min_per_hour']:.0f}분/시간")
        )

        total = (
            f"<tr style='border-top:1px solid #444;'>"
            f"<td colspan='3' style='padding:6px 8px 3px 0;color:#ddd;font-weight:bold;'>합계</td>"
            f"<td style='padding:6px 8px 3px 8px;text-align:right;color:#FFD93D;font-weight:bold;'>"
            f"{f(est['total_per_hour'])}</td>"
            f"<td style='padding:6px 0 3px 8px;text-align:right;color:#FFD93D;font-weight:bold;'>"
            f"{f(est['total_session'])}</td></tr>"
        )

        warn = ""
        if est["unknown"]:
            models = ", ".join(sorted(set(est["unknown"])))
            warn = (
                f"<p style='color:#f4b400;font-size:12px;margin-top:8px;'>"
                f"⚠ 공개 단가가 없는 모델: {models} — 합계에서 제외됨</p>"
            )

        html = (
            "<table style='border-collapse:collapse;width:100%;'>"
            "<tr style='color:#777;font-size:12px;'>"
            "<td style='padding:0 8px 4px 0;'>항목</td>"
            "<td style='padding:0 8px 4px;'>모델</td>"
            "<td style='padding:0 8px 4px;'>사용량</td>"
            "<td style='padding:0 8px 4px;text-align:right;'>시간당</td>"
            "<td style='padding:0 0 4px 8px;text-align:right;'>"
            f"세션({est['hours']:.1f}h)</td></tr>"
            f"{rows}{total}</table>{warn}"
        )
        self._cost_output.setText(html)

    def _btn_style(self, bg: str, hover: str) -> str:
        return (
            f"QPushButton {{ background: {bg}; color: #ddd; border: 1px solid #555; "
            f"border-radius: 4px; padding: 5px; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {hover}; }}"
        )

    @staticmethod
    def _constrain_combo(combo: QComboBox):
        """긴 창/게임 이름이 콤보를 좌우로 늘려 가로 스크롤이 생기는 것을 막는다.
        - 콤보 폭을 패널 폭에 맞추되 최장 항목에 맞춰 늘어나지 않게 한다.
        - 닫힌 상태의 현재 항목 텍스트는 폭에 맞춰 자동으로 '...' 말줄임된다.
        - 전체 이름은 드롭다운 목록과 툴팁에서 확인할 수 있다.
        """
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(10)
        # 가로로 남는 공간은 채우되, 내용 길이로 폭을 강제하지 않는다.
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        # 현재 선택 항목 전체 이름을 툴팁으로 (말줄임돼도 마우스 올리면 확인 가능)
        combo.currentTextChanged.connect(lambda t, c=combo: c.setToolTip(t))
        combo.setToolTip(combo.currentText())

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
        """제외 패턴을 config.local.yaml에 저장."""
        try:
            paths.update_user_config("ui", {"window_excludes": self._excludes})
        except Exception as e:
            logger.error(f"제외 패턴 저장 실패: {e}")

    def _refresh_windows(self):
        windows = _list_windows(excludes=self._excludes)
        # 게임 탭 콤보 + 시작 탭 콤보를 함께 갱신 (선택값은 각각 보존)
        for combo in (getattr(self, "_window_combo", None),
                      getattr(self, "_start_window_combo", None)):
            if combo is None:
                continue
            current = combo.currentText()
            combo.clear()
            combo.addItems(windows)
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)

    def _refresh_profiles(self):
        from pathlib import Path
        import re
        current = self._profile_combo.currentText()
        self._profile_combo.clear()
        self._profile_combo.addItem("자동 (창 타이틀에서 감지)")
        profile_dir = paths.user_data_dir() / "game_profiles"
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
        # 재시작 시 복원되도록 마지막 선택을 디스크에 저장.
        paths.save_last_session(game_name=game_name)
        self._profile_status.setText(f"적용 중: {game_name}...")
        logger.info(f"게임 프로필 적용: {game_name}")

    def _apply_window(self):
        window_title = self._window_combo.currentText()
        if not window_title:
            return
        self._settings_queue.put({"target_window": window_title})
        paths.save_last_session(target_window=window_title)
        # 프로필 콤보가 "자동"이면 창 타이틀에서 게임명 추출해서 적용
        if self._profile_combo.currentIndex() == 0:
            game_name = _extract_game_name(window_title)
            self._settings_queue.put({"game_name": game_name})
            paths.save_last_session(game_name=game_name)
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
        profile_dir = paths.user_data_dir() / "game_profiles"
        safe = re.sub(r'[\\/:*?"<>|]', "", game_name).strip().lower().replace(" ", "_")[:64]
        profile_path = profile_dir / f"{safe}.yaml"
        if profile_path.exists():
            profile_path.unlink()
            logger.info(f"기존 프로필 삭제: {profile_path.name}")
        self._settings_queue.put({"game_name": game_name})
        paths.save_last_session(game_name=game_name)
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
            values = {"vision_provider": provider}
            if provider == "claude":
                values["claude_vision_model"] = model
            else:
                values["vision_model"] = model
            paths.update_user_config("ai", values)
        except Exception as e:
            logger.error(f"vision_provider 저장 실패: {e}")

    def _save_config_chat_model(self, provider: str, model: str):
        try:
            values = {"chat_provider": provider}
            if provider == "claude":
                values["claude_chat_model"] = model
            else:
                values["chat_model"] = model
            paths.update_user_config("ai", values)
        except Exception as e:
            logger.error(f"chat_model 저장 실패: {e}")

    def _save_api_keys(self):
        try:
            from pathlib import Path
            env_path = paths.user_data_dir() / ".env.local"
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
            openai_key = self._openai_key_input.text().strip()
            if gemini_key:
                lines = _set_key(lines, "GEMINI_API_KEY", gemini_key)
                self._gemini_key_input.clear()
            if claude_key:
                lines = _set_key(lines, "CLAUDE_API_KEY", claude_key)
                self._claude_key_input.clear()
            if openai_key:
                lines = _set_key(lines, "OPENAI_API_KEY", openai_key)
                self._openai_key_input.clear()

            if gemini_key or claude_key or openai_key:
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(lines)
                logger.info("API 키 저장 완료")
                # 키는 프로세스 시작 시점에만 읽히므로(특히 온보딩이면 AI/STT 미기동)
                # 적용하려면 재시작이 필요하다. '지금 다시 시작' 버튼을 노출한다.
                self._set_api_key_status(
                    "✓ 저장 완료. 아래 '지금 다시 시작'을 누르면 AI·음성 인식이 적용됩니다.",
                    ok=True,
                )
                if getattr(self, "_restart_btn", None) is not None:
                    self._restart_btn.setVisible(True)
            else:
                self._set_api_key_status("입력된 키가 없습니다. 키를 입력 후 저장하세요.", ok=False)
        except Exception as e:
            logger.error(f"API 키 저장 실패: {e}")
            self._set_api_key_status(f"저장 실패: {e}", ok=False)

    def _set_api_key_status(self, text: str, ok: bool):
        status = getattr(self, "_api_key_status", None)
        if status is None:
            return
        color = "#6bcb77" if ok else "#f4b400"
        status.setStyleSheet(f"color: {color}; font-size: 12px;")
        status.setText(text)

    def _restart_app(self):
        """저장한 키를 적용하기 위해 앱을 새 프로세스로 재실행하고 현재 인스턴스를 종료."""
        from PyQt6.QtWidgets import QApplication
        try:
            paths.restart_app()
            logger.info("앱 재시작 요청 — 새 인스턴스 실행 후 현재 종료")
        except Exception as e:
            logger.error(f"재시작 실패: {e}")
            self._set_api_key_status(
                f"자동 재시작 실패: {e}\n프로그램을 직접 종료 후 다시 실행하세요.", ok=False
            )
            return
        QApplication.quit()

    def _save_stt_settings(self):
        """UI에서 변경한 STT 설정을 config.local.yaml에 저장 + 가능한 항목은 ai_process에 브로드캐스트."""
        try:
            old_stt = self._config.get("stt", {}) or {}
            new_stt = {
                "enabled": self._stt_enabled_chk.isChecked(),
                "sample_rate": int(old_stt.get("sample_rate", 16000)),
                "device": self._selected_device_str(),
                "vad_threshold": float(self._stt_threshold_spin.value()),
                "min_speech_ms": int(self._stt_min_speech_ms_spin.value()),
                "min_silence_ms": int(self._stt_min_silence_ms_spin.value()),
                "speech_pad_ms": int(self._stt_speech_pad_ms_spin.value()),
                "max_utterance_seconds": float(self._stt_max_utt_spin.value()),
                "provider": self._stt_provider_combo.currentText().strip() or "gemini",
                "openai_api_key_env": str(old_stt.get("openai_api_key_env", "OPENAI_API_KEY")),
                "model": self._stt_model_combo.currentText().strip(),
                "language": self._stt_language_input.text().strip() or "ko",
                "transcribe_prompt": self._stt_prompt_text.toPlainText().strip(),
                "lookback_seconds": float(self._stt_lookback_spin.value()),
                "min_transcript_chars": int(self._stt_min_chars_spin.value()),
                "default_weight": self._stt_weight_combo.currentText(),
            }
            # 정규화 등 UI에 없는 기존 항목은 보존
            for k in ("input_gain", "normalize_target_level", "normalize_percentile",
                      "normalize_max_gain"):
                if k in old_stt:
                    new_stt[k] = old_stt[k]

            # 변경 판정 기준은 현재 적용 중인(merged) 설정 = self._config["stt"].
            old_enabled = bool(old_stt.get("enabled", False))
            transcriber_changed = (
                str(old_stt.get("provider", "gemini")) != new_stt["provider"]
                or str(old_stt.get("model", "")) != new_stt["model"]
            )
            # stt 섹션 전체를 config.local.yaml 에 저장(다른 사용자 설정은 보존).
            paths.update_user_config("stt", new_stt)

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
                self._stt_status.setStyleSheet("color: #f4b400; font-size: 12px;")
            elif transcriber_changed:
                self._stt_status.setText(
                    f"저장 완료. provider/model({new_stt['provider']} · {new_stt['model']}) "
                    "변경은 재시작 후 적용됩니다."
                )
                self._stt_status.setStyleSheet("color: #f4b400; font-size: 12px;")
            else:
                self._stt_status.setText("저장 완료. 런타임 항목은 즉시 적용됩니다.")
                self._stt_status.setStyleSheet("color: #6BCB77; font-size: 12px;")
            logger.info(f"STT 설정 저장: {new_stt}")
        except Exception as e:
            logger.error(f"STT 설정 저장 실패: {e}", exc_info=True)
            self._stt_status.setText(f"저장 실패: {e}")
            self._stt_status.setStyleSheet("color: #ff6b6b; font-size: 12px;")

    # ── 마이크 테스트 ───────────────────────────────────────────────────────

    def _toggle_mic_test(self):
        if self._mic_thread is not None:
            self._stop_mic_test()
        else:
            self._start_mic_test()

    def _start_mic_test(self):
        device = _parse_device(self._selected_device_str())
        sr = int((self._config.get("stt") or {}).get("sample_rate", 16000))
        self._mic_peak = 0.0

        self._mic_worker = _MicTestWorker(device, sample_rate=sr)
        self._mic_thread = QThread(self)
        self._mic_worker.moveToThread(self._mic_thread)
        self._mic_thread.started.connect(self._mic_worker.run)
        self._mic_worker.level.connect(self._on_mic_level)
        self._mic_worker.error.connect(self._on_mic_error)
        self._mic_worker.stopped.connect(self._on_mic_stopped)
        self._mic_thread.start()

        self._mic_test_btn.setText("⏹ 마이크 테스트 중지")
        self._mic_test_btn.setStyleSheet(self._btn_style("#5a1a1a", "#7a2a2a"))
        self._mic_test_status.setText("측정 중... 마이크에 대고 말해보세요.")
        self._mic_test_status.setStyleSheet("color: #888; font-size: 11px;")

    def _stop_mic_test(self):
        if self._mic_worker is not None:
            self._mic_worker.stop()
        if self._mic_thread is not None:
            self._mic_thread.quit()
            self._mic_thread.wait(1500)
        self._mic_thread = None
        self._mic_worker = None
        self._mic_level_bar.setValue(0)
        self._mic_test_btn.setText("🎤 마이크 테스트 시작")
        self._mic_test_btn.setStyleSheet(self._btn_style("#1a472a", "#2d6a4f"))
        # 피크 결과를 남겨 '잡혔는지' 판단을 돕는다.
        if self._mic_peak >= 0.05:
            self._mic_test_status.setText(
                f"✓ 음성이 감지되었습니다 (피크 {int(self._mic_peak * 100)}%)."
            )
            self._mic_test_status.setStyleSheet("color: #6BCB77; font-size: 11px;")
        elif self._mic_peak > 0:
            self._mic_test_status.setText(
                f"입력이 약합니다 (피크 {int(self._mic_peak * 100)}%). "
                "마이크 볼륨/장치를 확인하세요."
            )
            self._mic_test_status.setStyleSheet("color: #f4b400; font-size: 11px;")
        else:
            self._mic_test_status.setText("입력이 감지되지 않았습니다. device 설정을 확인하세요.")
            self._mic_test_status.setStyleSheet("color: #f4b400; font-size: 11px;")

    def _on_mic_level(self, level: float):
        self._mic_peak = max(self._mic_peak, level)
        self._mic_level_bar.setValue(int(level * 100))

    def _on_mic_error(self, msg: str):
        logger.warning(f"마이크 테스트 오류: {msg}")
        self._mic_test_status.setText(f"오류: {msg}")
        self._mic_test_status.setStyleSheet("color: #ff6b6b; font-size: 11px;")

    def _on_mic_stopped(self):
        # 워커 run()이 (오류 등으로) 자체 종료한 경우에도 버튼/스레드 상태를 정리.
        if self._mic_thread is not None and not self._mic_worker_running():
            self._mic_thread.quit()
            self._mic_thread.wait(1500)
            self._mic_thread = None
            self._mic_worker = None
            self._mic_level_bar.setValue(0)
            self._mic_test_btn.setText("🎤 마이크 테스트 시작")
            self._mic_test_btn.setStyleSheet(self._btn_style("#1a472a", "#2d6a4f"))

    def _mic_worker_running(self) -> bool:
        return bool(self._mic_worker and getattr(self._mic_worker, "_running", False))

    # ── STT 테스트 ──────────────────────────────────────────────────────────

    def _start_stt_test(self):
        if self._stt_test_thread is not None:
            return  # 이미 진행 중
        from src.utils.config_loader import get_api_key, get_openai_api_key

        provider = self._stt_provider_combo.currentText().strip() or "gemini"
        gemini_key = get_api_key(self._config)
        openai_key = get_openai_api_key(self._config)
        # 키 사전 점검 (워커에서도 잡지만 빠른 피드백)
        if provider == "openai" and not openai_key:
            self._set_stt_test_result("OpenAI API 키가 없습니다. AI 모델 탭에서 저장 후 재시작하세요.", "#f4b400")
            return
        if provider == "gemini" and not gemini_key:
            self._set_stt_test_result("Gemini API 키가 없습니다. AI 모델 탭에서 저장 후 재시작하세요.", "#f4b400")
            return

        stt_cfg = self._config.get("stt", {}) or {}
        self._stt_test_worker = _STTTestWorker(
            device=_parse_device(self._selected_device_str()),
            sample_rate=int(stt_cfg.get("sample_rate", 16000)),
            record_seconds=4.0,
            provider=provider,
            model=self._stt_model_combo.currentText().strip(),
            language=self._stt_language_input.text().strip() or "ko",
            prompt=self._stt_prompt_text.toPlainText().strip(),
            gemini_key=gemini_key,
            openai_key=openai_key,
            normalize_target_level=float(stt_cfg.get("normalize_target_level", 0.1)),
            normalize_percentile=float(stt_cfg.get("normalize_percentile", 95.0)),
            normalize_max_gain=float(stt_cfg.get("normalize_max_gain", 20.0)),
        )
        self._stt_test_thread = QThread(self)
        self._stt_test_worker.moveToThread(self._stt_test_thread)
        self._stt_test_thread.started.connect(self._stt_test_worker.run)
        self._stt_test_worker.progress.connect(self._on_stt_test_progress)
        self._stt_test_worker.finished.connect(self._on_stt_test_finished)
        self._stt_test_worker.error.connect(self._on_stt_test_error)
        self._stt_test_thread.start()

        self._stt_test_btn.setEnabled(False)
        self._stt_test_btn.setText("STT 테스트 진행 중...")

    def _set_stt_test_result(self, text: str, color: str):
        self._stt_test_result.setText(text)
        self._stt_test_result.setStyleSheet(f"color: {color}; font-size: 12px;")

    def _on_stt_test_progress(self, msg: str):
        self._set_stt_test_result(msg, "#888")

    def _on_stt_test_finished(self, text: str, elapsed: float):
        if text:
            self._set_stt_test_result(f"✓ 인식 결과 ({elapsed:.1f}s): {text}", "#6BCB77")
        else:
            self._set_stt_test_result(
                f"인식된 텍스트가 없습니다 ({elapsed:.1f}s). 더 또렷하게 말하거나 "
                "마이크 볼륨/장치를 확인하세요.",
                "#f4b400",
            )
        self._cleanup_stt_test()

    def _on_stt_test_error(self, msg: str):
        logger.warning(f"STT 테스트 오류: {msg}")
        self._set_stt_test_result(f"오류: {msg}", "#ff6b6b")
        self._cleanup_stt_test()

    def _cleanup_stt_test(self):
        if self._stt_test_thread is not None:
            self._stt_test_thread.quit()
            self._stt_test_thread.wait(2000)
        self._stt_test_thread = None
        self._stt_test_worker = None
        self._stt_test_btn.setEnabled(True)
        self._stt_test_btn.setText("📝 STT 테스트 (4초 녹음)")

    def show_game_tab(self):
        """설정의 '게임' 탭을 선택한다(탭 라벨로 찾아 인덱스 변화에 견고하게)."""
        for i in range(self._tabs.count()):
            if self._tabs.tabText(i) == "게임":
                self._tabs.setCurrentIndex(i)
                return

    def set_profile_name(self, name: str):
        self._profile_status.setText(f"현재 프로필: {name}")
        # 시작 탭 ③ 상태도 갱신 (프로필 로드 완료 통지로 들어옴)
        if getattr(self, "_start_profile_status", None) is not None:
            self._start_profile_status.setText(f"현재 프로필: {name}")
            self._start_profile_status.setStyleSheet("color: #6BCB77; font-size: 12px;")
        if getattr(self, "_start_profile_status_dot", None) is not None:
            self._start_profile_status_dot.setText("✓ 생성됨")
            self._start_profile_status_dot.setStyleSheet("color: #6BCB77; font-size: 12px;")
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

    def __init__(self, config: dict, ui_queue: Queue, settings_queue: Queue, onboarding: bool = False):
        super().__init__()
        self._config = config["ui"]
        self._config_full = config
        self._ui_queue = ui_queue
        self._settings_queue = settings_queue
        self._onboarding = onboarding
        self._drag_pos = QPoint()
        self._resizing = False
        self._resize_start_pos = QPoint()
        self._resize_start_size = QSize()
        self._opacity = self._config.get("opacity", 0.85)
        self._settings_visible = False
        # STT 받아쓰기를 채팅창에 인라인 회색으로 항상 보일지 여부.
        # 불안정한 한국어 STT가 '실제로 뭐라고 들렸는지' 확인용. 설정/우클릭으로 토글.
        self._show_stt = bool(self._config.get("show_stt_history", False))

        self._setup_window()
        self._setup_ui()
        self._setup_timer()

        # 첫 실행 온보딩: 키 미설정이면 설정 패널을 자동으로 열어 키 입력을 유도.
        if self._onboarding:
            self._toggle_settings()

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
        # 경고 아이콘 클릭 → 설정의 '게임' 탭 열기
        self._titlebar.on_warning_click = self._open_game_settings
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
        self._settings_panel = SettingsPanel(self._settings_queue, self._config_full, onboarding=self._onboarding)
        self._settings_panel.setStyleSheet("background: transparent;")
        # STT 인라인 표시 토글을 설정 패널 → 오버레이로 즉시 반영하는 콜백.
        self._settings_panel.on_show_stt_changed = self.set_show_stt

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

    def _open_game_settings(self):
        """경고 아이콘 클릭 시: 설정 패널을 열고 '게임' 탭으로 이동."""
        if not self._settings_visible:
            self._toggle_settings()
        self._settings_panel.show_game_tab()

    def _toggle_settings(self):
        self._settings_visible = not self._settings_visible
        # 기존 사용자의 저장된 폭(392)이 좁아 설정 버튼이 잘리는 걸 방지:
        # 설정을 열 때 창이 너무 좁으면 자동으로 넓힌다.
        if self._settings_visible and self.width() < 450:
            self.resize(450, self.height())
            self._position_resize_hint()
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
                            # 캡처 창↔프로필 불일치 경고는 채팅창이 아니라
                            # 상단 타이틀바 ⚠ 아이콘으로 표시(없으면 자동 해제).
                            self._titlebar.set_warning(chat.get("warning"))
                    elif "stt_transcript" in chat:
                        # STT 받아쓰기: 설정이 켜져 있을 때만 인라인 회색으로 표시.
                        if self._show_stt:
                            self._add_stt_message(chat.get("text", ""))
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

    def _add_stt_message(self, text: str):
        """STT 받아쓰기를 채팅창에 흐린 회색 인라인으로 추가('🎤 나: ...').
        봇 메시지(_add_message)와 같은 리스트/트리밍을 공유하되 시각적으로 죽여
        실제 채팅과 구분되게 한다."""
        text = (text or "").strip()
        if not text:
            return
        font_size = self._config.get("font_size", 14)

        label = QLabel()
        label.setText(
            '<span style="color:#777;">🎤 나: </span>'
            f'<span style="color:#888; font-style:italic;">{text}</span>'
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

    def set_show_stt(self, show: bool):
        """STT 인라인 표시 on/off (설정 패널·우클릭 메뉴에서 호출)."""
        self._show_stt = bool(show)

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
        stt_action = QAction("🎤 STT 받아쓰기 보기", self)
        stt_action.setCheckable(True)
        stt_action.setChecked(self._show_stt)
        stt_action.triggered.connect(self._toggle_show_stt)
        menu.addAction(stt_action)

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

    def _toggle_show_stt(self, checked: bool):
        """우클릭 메뉴 토글: 즉석 on/off + 설정 패널 체크박스 동기화 + 디스크 저장."""
        self._show_stt = bool(checked)
        # 설정 패널 체크박스도 맞춰준다(다음에 패널 열었을 때 일관성).
        if getattr(self, "_settings_panel", None) is not None:
            self._settings_panel.set_show_stt_checked(self._show_stt)
        try:
            paths.update_user_config("ui", {"show_stt_history": self._show_stt})
            self._config["show_stt_history"] = self._show_stt
        except Exception:
            logger.exception("show_stt_history 저장 실패")

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


def run_overlay(config: dict, ui_queue: Queue, settings_queue: Queue, onboarding: bool = False):
    import sys
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    overlay = ChatOverlay(config, ui_queue, settings_queue, onboarding=onboarding)
    overlay.show()

    sys.exit(app.exec())
