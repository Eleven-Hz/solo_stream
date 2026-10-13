"""
설정 창 (오버레이와 분리된 별도 창).

왼쪽 메뉴: 방송 준비 · 오버레이 · 음성 인식 · AI 모델·키 · 게임·프로필 · 사용량.
모든 항목은 바꾸는 즉시 config.local.yaml 에 저장되고(적용 버튼 없음), 실행 중 프로세스에
바로 반영할 수 있는 값은 settings_queue 로 브로드캐스트한다. 재시작해야 적용되는 값은
하단 바에 '재시작하면 적용' 으로 모아 보여 준다.

새 설정 항목을 추가할 때:
  1. 해당 페이지 빌더(_build_page_*)에 위젯 + 저장 핸들러를 만든다(paths.update_user_config).
  2. 실행 중 반영이 필요하면 self._queue.put({...}) 로 브로드캐스트하고,
     src/main.py 의 받는 프로세스 settings_queue 루프에 elif 를 추가한다.
  3. 재시작이 필요하면 self._need_restart("이유") 를 호출한다.
"""
from __future__ import annotations

import logging
from multiprocessing import Queue

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from src import __version__ as APP_VERSION
from src.ui import theme as T
from src.ui import winapi
from src.ui.helpers import extract_game_name, list_windows, parse_device, profile_filename
from src.ui.workers import (
    CapturePreviewWorker,
    KeyCheckWorker,
    MicLevelWorker,
    STTTestWorker,
    start_worker,
)
from src.utils import paths

logger = logging.getLogger("solo_stream.ui")

# ── 모델 후보 ────────────────────────────────────────────────────────────────
_VISION_MODELS = [
    ("gemini", "gemini-2.5-flash"), ("gemini", "gemini-2.5-flash-lite"),
    ("gemini", "gemini-2.5-pro"), ("gemini", "gemini-2.0-flash"),
    ("claude", "claude-sonnet-4-6"), ("claude", "claude-opus-4-7"), ("claude", "claude-haiku-4-5-20251001"),
]
_CHAT_MODELS = [
    ("gemini", "gemini-2.5-flash-lite"), ("gemini", "gemini-2.5-flash"),
    ("gemini", "gemini-2.5-pro"), ("gemini", "gemini-2.0-flash"),
    ("claude", "claude-haiku-4-5-20251001"), ("claude", "claude-sonnet-4-6"), ("claude", "claude-opus-4-7"),
]
# 게임 프로필 생성은 Google Search 그라운딩이 필요해 Gemini 전용.
_PROFILE_MODELS = ["gemini-2.5-flash", "gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"]
# STT: gemini 는 generate_content(오디오 이해)로 받아쓰기가 되는 모델만.
_STT_MODELS = [
    ("openai", "gpt-transcribe"), ("openai", "gpt-4o-transcribe"),
    ("openai", "gpt-4o-mini-transcribe"), ("openai", "whisper-1"),
    ("gemini", "gemini-2.5-flash"), ("gemini", "gemini-2.5-flash-lite"), ("gemini", "gemini-2.5-pro"),
    ("gemini", "gemini-3-flash-preview"), ("gemini", "gemini-3.1-flash-lite"), ("gemini", "gemini-3.5-flash"),
]
_STT_RECOMMENDED = "gpt-transcribe"
# 게임 혼잣말 40문장 실측(2026-10, tests/stt groundtruth): (정확도 %, 응답 초)
_STT_MEASURED = {
    "gpt-transcribe": (91, 0.7),
    "gpt-4o-transcribe": (87, 0.9),
    "gpt-4o-mini-transcribe": (83, 0.9),
    "gemini-2.5-flash": (65, 2.5),
}
_PROVIDER_LABEL = {"gemini": "Gemini", "claude": "Claude", "openai": "OpenAI"}
_KEY_ENV = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "claude": "CLAUDE_API_KEY"}

_PAGES = [
    ("ready", "방송 준비"),
    ("overlay", "오버레이"),
    ("voice", "음성 인식"),
    ("ai", "AI 모델 · 키"),
    ("game", "게임 · 프로필"),
    ("usage", "사용량"),
]


def _model_text(provider: str, model: str, recommended: str | None = None) -> str:
    text = f"{_PROVIDER_LABEL.get(provider, provider)} · {model}"
    return text + "  (추천)" if model == recommended else text


def _pm(provider: str, model: str) -> str:
    return f"{provider}|{model}"


def _read_env_keys() -> dict[str, str]:
    """사용자 .env.local 의 키 값(실행 중 저장한 키도 반영되도록 파일을 직접 읽는다)."""
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(paths.user_data_dir() / ".env.local")
    except Exception:
        vals = {}
    return {p: (vals.get(env) or "").strip() for p, env in _KEY_ENV.items()}


def _write_env_key(env_name: str, value: str):
    env_path = paths.user_data_dir() / ".env.local"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith(f"{env_name}="):
            lines[i] = f"{env_name}={value}\n"
            break
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{env_name}={value}\n")
    env_path.write_text("".join(lines), encoding="utf-8")


# ── 작은 위젯들 ──────────────────────────────────────────────────────────────

class _NavButton(QAbstractButton):
    """왼쪽 메뉴 항목: 이름 + 오른쪽 작은 상태 문구."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(38)
        self._meta = ""
        self._meta_color = T.FAINT
        self._hover = False

    def set_meta(self, text: str, color: str = T.FAINT):
        self._meta, self._meta_color = text, color
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.isChecked() or self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#1C1E24" if self.isChecked() else "#16171B"))
            p.drawRoundedRect(r, 8, 8)
        p.setFont(T.font(13, 600 if self.isChecked() else 500))
        p.setPen(QColor("#FFFFFF" if self.isChecked() else "#A3A9B6"))
        p.drawText(r.adjusted(12, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        if self._meta:
            p.setFont(T.font(11, 400, mono=True))
            p.setPen(QColor(self._meta_color))
            p.drawText(r.adjusted(12, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       self._meta)


class _FlowLayout(QLayout):
    """칩을 가로로 채우다 넘치면 줄바꿈하는 레이아웃."""

    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self._items: list = []
        self._sp = spacing

    def addItem(self, item):
        self._items.append(item)

    def addWidget(self, w):
        self.addItem(QWidgetItem(w))
        self.addChildWidget(w)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, dry=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, dry: bool) -> int:
        m = self.contentsMargins()
        x, y = rect.x() + m.left(), rect.y() + m.top()
        right = rect.right() - m.right()
        line_h = 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > right and line_h > 0:
                x = rect.x() + m.left()
                y += line_h + self._sp
                line_h = 0
            if not dry:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._sp
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


class _TransparencyPreview(QWidget):
    """설정의 투명도 미리보기: 게임 화면(격자) 위 채팅 두 줄."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(220, 92)
        self._t = 15

    def set_transparency(self, t: int):
        self._t = t
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#22301f"))
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        p.setPen(QColor(0, 0, 0, 46))
        for x in range(0, self.width(), 16):
            p.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 16):
            p.drawLine(0, y, self.width(), y)
        panel = QRectF(20, 20, 180, 52)
        alpha = int((1 - self._t / 100) * 0.94 * 255)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(16, 17, 21, alpha))
        p.drawRoundedRect(panel, 6, 6)
        lines = [("신입생", "#8EC9FF", "이거 무슨 몹이야?"), ("겜린이", "#FFB36B", "와 개많아..!")]
        for i, (name, color, msg) in enumerate(lines):
            y = 40 + i * 20
            for dx, dy in ((0, 1), (1, 0), (-1, 0), (0, -1)) if self._t >= 60 else ():
                p.setPen(QColor(0, 0, 0, 220))
                p.setFont(T.font(12, 700))
                p.drawText(30 + dx, y + dy, name)
                p.setFont(T.font(12))
                p.drawText(30 + p.fontMetrics().horizontalAdvance(name) + 12 + dx, y + dy, msg)
            p.setFont(T.font(12, 700))
            p.setPen(QColor(color))
            p.drawText(30, y, name)
            nw = p.fontMetrics().horizontalAdvance(name)
            p.setFont(T.font(12))
            p.setPen(QColor("#ECEDF1"))
            p.drawText(30 + nw + 6, y, msg)


class _PromptDialog(QDialog):
    """화면 분석 프롬프트 편집(고급)."""

    def __init__(self, text: str, default_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("화면 분석 프롬프트 편집")
        self.setModal(True)
        self.resize(640, 480)
        self.setStyleSheet(f"QDialog {{ background: {T.BG}; }}" + T.settings_qss())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(T.label("화면 분석 프롬프트", "lbl"))
        lay.addWidget(T.label(
            "묘사·위치·상황·이벤트의 형식과 분량을 정합니다. [게임 정보]/[이전 위치]는 자동으로 앞에 붙으니 "
            "넣지 마세요. {vision_focus_hint} 자리는 게임 프로필의 '주목할 항목'으로 바뀝니다.", "sub", wrap=True))
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(text)
        lay.addWidget(self.editor, 1)
        row = QHBoxLayout()
        reset = T.button("기본값으로")
        reset.clicked.connect(lambda: self.editor.setPlainText(default_text))
        row.addWidget(reset)
        row.addStretch(1)
        cancel = T.button("취소")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = T.button("적용")
        ok.setStyleSheet(f"QPushButton {{ background: {T.ACCENT}; color: {T.ACCENT_FG}; border: none; "
                         "border-radius: 7px; height: 32px; padding: 0 16px; font-weight: 700; }")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)


# ── 설정 창 ──────────────────────────────────────────────────────────────────

class SettingsWindow(QWidget):

    def __init__(self, settings_queue: Queue, config: dict, overlay=None, onboarding: bool = False):
        super().__init__(None)
        self._queue = settings_queue
        self._config = config
        self._overlay = overlay
        self._onboarding = onboarding
        self._restart_reasons: list[str] = []
        self._threads: list = []           # (thread, worker) — GC 방지
        self._mic_worker: MicLevelWorker | None = None
        self._mic_thread = None
        self._mic_peak = 0.0
        self._mic_error = ""
        self._stt_test_running = False
        self._profile_state = ("", "pending", "")
        self._drag_pos: QPoint | None = None

        self.setWindowTitle("Solo Stream 설정")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._build()
        self._fit_to_screen()
        self._nav_buttons[1].set_meta(f"{self._slider.value()}%")
        self.open_page("ready", show=False)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)

    def _shutdown(self):
        """앱 종료 전에 백그라운드 스레드를 정리한다(실행 중 QThread 파괴 크래시 방지)."""
        self._stop_mic()
        for thread, _ in list(self._threads):
            thread.quit()
            thread.wait(1500)

    # ── 공개 API (오버레이가 호출) ────────────────────────────────────────────

    def open_page(self, page: str | None = None, show: bool = True):
        if page:
            keys = [k for k, _ in _PAGES]
            if page in keys:
                idx = keys.index(page)
                self._stack.setCurrentIndex(idx)
                for i, b in enumerate(self._nav_buttons):
                    b.setChecked(i == idx)
        self._on_page_changed()
        if show:
            self.show()
            self.raise_()
            self.activateWindow()

    def sync_transparency(self, t: int):
        if self._slider.value() != t:
            self._slider.blockSignals(True)
            self._slider.setValue(t)
            self._slider.blockSignals(False)
        self._op_val.setText(f"{t}%")
        self._preview.set_transparency(t)
        self._nav_buttons[1].set_meta(f"{t}%")

    def sync_click_through(self, on: bool):
        self._ct_switch.blockSignals(True)
        self._ct_switch.setChecked(on)
        self._ct_switch.blockSignals(False)

    def sync_show_stt(self, on: bool):
        self._show_stt_switch.blockSignals(True)
        self._show_stt_switch.setChecked(on)
        self._show_stt_switch.blockSignals(False)

    def sync_chat_active(self, active: bool):
        if active:
            self._start_btn.setText("  방송 정지")
            self._start_btn.setObjectName("danger")
            self._start_btn.setIcon(T.icon("pause", "#2A0A0B", 12, stroke=2.2))
        else:
            self._start_btn.setText("  방송 시작")
            self._start_btn.setObjectName("primary")
            self._start_btn.setIcon(T.icon("play", T.ACCENT_FG, 12))
        self._start_btn.style().unpolish(self._start_btn)
        self._start_btn.style().polish(self._start_btn)

    def set_profile_name(self, name: str, status: str = "ok", detail: str = ""):
        """프로필 로드/생성 결과. status: ok | aborted(503 등으로 중단) | partial(일부 실패)."""
        self._profile_state = (name, status, detail)
        if status == "aborted":
            text = f"{name} · 서버 혼잡으로 생성 중단됨 — 잠시 후 다시 만들기"
            badge, color = "error", T.ERROR
        elif status == "partial":
            text = f"{name} · 일부 정보 누락 — 필요하면 다시 만들기"
            badge, color = "warn", T.WARN
        else:
            text = f"{name} · 정상"
            badge, color = "ok", T.MUTED
        self._ready_profile_sub.setText(text)
        self._ready_profile_badge.set_state(badge)
        self._game_profile_status.setText(text + (f"\n{detail}" if detail and status != "ok" else ""))
        self._game_profile_status.setStyleSheet(f"color: {color}; font-size: 12px;")
        for i in range(self._profile_combo.count()):
            if self._profile_combo.itemText(i) == name or self._profile_combo.itemData(i) == name:
                self._profile_combo.setCurrentIndex(i)
                break
        self._refresh_ready()

    # ── 골격 ─────────────────────────────────────────────────────────────────

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = QFrame()
        root.setObjectName("root")
        root.setStyleSheet(T.settings_qss() + f"QFrame#root {{ background: {T.BG}; border: 1px solid {T.LINE};"
                                              " border-radius: 12px; }")
        outer.addWidget(root)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(1, 1, 1, 1)
        rl.setSpacing(0)

        # 제목줄 (드래그로 창 이동)
        self._titlebar = QWidget()
        self._titlebar.setFixedHeight(48)
        tl = QHBoxLayout(self._titlebar)
        tl.setContentsMargins(18, 0, 10, 0)
        tl.setSpacing(10)
        t1 = QLabel("설정")
        t1.setStyleSheet("font-size: 14px; font-weight: 600;")
        tl.addWidget(t1)
        tl.addWidget(T.label("Solo Stream", "sub"))
        tl.addStretch(1)
        close = QPushButton()
        close.setIcon(T.icon("close", "#A3A9B6", 14))
        close.setFixedSize(32, 32)
        close.setToolTip("설정 닫기 (Esc)")
        close.setAccessibleName("설정 닫기")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet("QPushButton { border: none; border-radius: 7px; background: transparent; }"
                            "QPushButton:hover { background: #22242B; }")
        close.clicked.connect(self.hide)
        tl.addWidget(close)
        rl.addWidget(self._titlebar)
        sep = QFrame()
        sep.setStyleSheet("background: #1F2127; max-height: 1px; min-height: 1px;")
        rl.addWidget(sep)

        body = QHBoxLayout()
        body.setSpacing(0)
        rl.addLayout(body, 1)

        # 왼쪽 메뉴
        nav = QWidget()
        nav.setObjectName("nav")
        nav.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        nav.setFixedWidth(216)
        nav.setStyleSheet("QWidget#nav { border-right: 1px solid #1F2127; }")
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(10, 14, 10, 14)
        nl.setSpacing(2)
        self._nav_buttons: list[_NavButton] = []
        for i, (_, text) in enumerate(_PAGES):
            b = _NavButton(text)
            b.setStyleSheet("border: none;")
            b.clicked.connect(lambda _=False, idx=i: self.open_page(_PAGES[idx][0], show=False))
            nl.addWidget(b)
            self._nav_buttons.append(b)
        nl.addStretch(1)
        ver = T.label(f"v{APP_VERSION}" + (" · 개발 빌드" if paths.is_dev() else ""), "sub")
        ver.setStyleSheet(f"color: {T.FAINT}; font-size: 11px; padding: 0 12px;")
        nl.addWidget(ver)
        body.addWidget(nav)

        # 오른쪽: 페이지 + 하단 바
        right = QVBoxLayout()
        right.setSpacing(0)
        self._stack = QStackedWidget()
        right.addWidget(self._stack, 1)
        self._stack.addWidget(self._build_page_ready())
        self._stack.addWidget(self._build_page_overlay())
        self._stack.addWidget(self._build_page_voice())
        self._stack.addWidget(self._build_page_ai())
        self._stack.addWidget(self._build_page_game())
        self._stack.addWidget(self._build_page_usage())
        right.addWidget(self._build_footer())
        body.addLayout(right, 1)

        self._refresh_windows()
        self._refresh_keys()
        self._refresh_ready()

    def _build_footer(self) -> QWidget:
        foot = QWidget()
        foot.setFixedHeight(48)
        foot.setStyleSheet("QWidget#foot { border-top: 1px solid #1F2127; }")
        foot.setObjectName("foot")
        foot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(28, 0, 16, 0)
        fl.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(T.icon("check", T.ACCENT, 12, stroke=2.2).pixmap(12, 12))
        fl.addWidget(ic)
        self._save_lbl = T.label("변경 사항은 자동 저장됩니다", "sub")
        fl.addWidget(self._save_lbl)
        fl.addStretch(1)
        self._restart_lbl = T.label("", "sub")
        self._restart_lbl.setStyleSheet(f"color: {T.WARN}; font-size: 12px;")
        fl.addWidget(self._restart_lbl)
        self._restart_btn = T.button("지금 다시 시작")
        self._restart_btn.clicked.connect(self._restart_app)
        self._restart_btn.setVisible(False)
        fl.addWidget(self._restart_btn)
        return foot

    def _page(self, title: str, desc: str, header_right: QWidget | None = None) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setObjectName("page")
        inner.setStyleSheet("QWidget#page { background: transparent; }")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(28, 24, 24, 24)
        lay.setSpacing(16)
        head = QHBoxLayout()
        tb = QVBoxLayout()
        tb.setSpacing(6)
        tb.addWidget(T.label(title, "h1"))
        tb.addWidget(T.label(desc, "desc", wrap=True))
        head.addLayout(tb, 1)
        if header_right is not None:
            head.addWidget(header_right, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        scroll.setWidget(inner)
        return scroll, lay

    def _fit_to_screen(self):
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        w, h = min(960, int(avail.width() * 0.92)), min(720, int(avail.height() * 0.92))
        self.resize(w, h)
        self.move(avail.center().x() - w // 2, avail.center().y() - h // 2)

    def _on_page_changed(self):
        page = _PAGES[self._stack.currentIndex()][0]
        if page in ("ready", "voice") and self.isVisible():
            self._start_mic()
        else:
            self._stop_mic()
        if page == "usage":
            self._recompute_cost()
        if page == "ready":
            self._refresh_ready()

    # ── 공통 저장/재시작 ──────────────────────────────────────────────────────

    def _save(self, section: str, values: dict):
        try:
            paths.update_user_config(section, values)
            self._config.setdefault(section, {}).update(values)
            self._flash_saved()
        except Exception as e:
            logger.error(f"설정 저장 실패({section}): {e}")
            self._save_lbl.setText(f"저장 실패: {e}")
            self._save_lbl.setStyleSheet(f"color: {T.ERROR}; font-size: 12px;")

    def _flash_saved(self):
        self._save_lbl.setText("저장됨")
        self._save_lbl.setStyleSheet(f"color: {T.ACCENT_HI}; font-size: 12px;")
        QTimer.singleShot(1400, lambda: (
            self._save_lbl.setText("변경 사항은 자동 저장됩니다"),
            self._save_lbl.setStyleSheet(f"color: {T.MUTED}; font-size: 12px;"),
        ))

    def _need_restart(self, reason: str):
        if reason not in self._restart_reasons:
            self._restart_reasons.append(reason)
        self._restart_lbl.setText("재시작하면 적용: " + ", ".join(self._restart_reasons))
        self._restart_btn.setVisible(True)

    def _restart_app(self):
        try:
            paths.restart_app()
            logger.info("앱 재시작 요청 — 새 인스턴스 실행 후 현재 종료")
        except Exception as e:
            logger.error(f"재시작 실패: {e}")
            self._restart_lbl.setText(f"자동 재시작 실패 — 직접 종료 후 다시 실행하세요 ({e})")
            return
        QApplication.quit()

    def _run(self, worker, **signals):
        """worker 를 백그라운드에서 실행. signals: 시그널 이름 → 슬롯."""
        for name, slot in signals.items():
            getattr(worker, name).connect(slot)
        thread = start_worker(self, worker)
        pair = (thread, worker)
        self._threads.append(pair)
        thread.finished.connect(lambda: self._threads.remove(pair) if pair in self._threads else None)
        return thread

    # ── 1. 방송 준비 ─────────────────────────────────────────────────────────

    def _build_page_ready(self) -> QWidget:
        page, lay = self._page("방송 준비", "시작 전에 네 가지만 확인하세요. 모두 초록이면 바로 시작할 수 있어요.")
        card = T.Card()

        # API 키
        self._ready_key_badge = T.StatusBadge()
        self._ready_key_sub = T.label("", "sub")
        keys_btn = T.button("키 관리")
        keys_btn.clicked.connect(lambda: self.open_page("ai"))
        w, h = T.row_box(vertical=True, spacing=10)
        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(self._ready_key_badge)
        tb = QVBoxLayout()
        tb.setSpacing(3)
        tb.addWidget(T.label("API 키", "lbl"))
        tb.addWidget(self._ready_key_sub)
        top.addLayout(tb, 1)
        top.addWidget(keys_btn)
        h.addLayout(top)
        # 키가 없을 때만 보이는 빠른 입력(첫 실행 온보딩)
        self._quick_key_row = QWidget()
        qk = QHBoxLayout(self._quick_key_row)
        qk.setContentsMargins(38, 0, 0, 0)
        qk.setSpacing(8)
        self._quick_key_input = QLineEdit()
        self._quick_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._quick_key_input.setPlaceholderText("Gemini API 키 붙여넣기 — aistudio.google.com/apikey")
        self._quick_key_input.returnPressed.connect(self._save_quick_key)
        qk.addWidget(self._quick_key_input, 1)
        qs = T.button("저장")
        qs.clicked.connect(self._save_quick_key)
        qk.addWidget(qs)
        h.addWidget(self._quick_key_row)
        card.add_row(w)

        # 게임 창
        self._ready_win_badge = T.StatusBadge()
        self._ready_win_sub = T.label("", "sub")
        w, h = T.row_box(vertical=True, spacing=10)
        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(self._ready_win_badge)
        tb = QVBoxLayout()
        tb.setSpacing(3)
        tb.addWidget(T.label("게임 창", "lbl"))
        tb.addWidget(self._ready_win_sub)
        top.addLayout(tb, 1)
        h.addLayout(top)
        pick = QHBoxLayout()
        pick.setContentsMargins(38, 0, 0, 0)
        pick.setSpacing(8)
        self._window_combo = T.combo()
        self._window_combo.setMinimumWidth(240)
        pick.addWidget(self._window_combo, 1)
        refresh = QPushButton()
        refresh.setObjectName("btn")
        refresh.setIcon(T.icon("refresh", T.TEXT_2, 14))
        refresh.setToolTip("창 목록 새로고침")
        refresh.setAccessibleName("창 목록 새로고침")
        refresh.setFixedWidth(36)
        refresh.clicked.connect(self._refresh_windows)
        pick.addWidget(refresh)
        self._apply_win_btn = T.button("이 창으로 캡처")
        self._apply_win_btn.clicked.connect(self._apply_window)
        pick.addWidget(self._apply_win_btn)
        h.addLayout(pick)
        card.add_row(w)

        # 게임 프로필
        self._ready_profile_badge = T.StatusBadge()
        self._ready_profile_sub = T.label("불러오는 중…", "sub", wrap=True)
        regen = T.button("다시 만들기")
        regen.setToolTip("선택한 창의 게임 정보를 다시 검색해 프로필을 새로 만듭니다(수십 초)")
        regen.clicked.connect(self._regenerate_profile)
        card.add_row(left=self._ready_profile_badge, title="게임 프로필", right=regen)
        # add_row 의 sub 라벨 대신 상태 라벨을 직접 붙이기 위해 마지막 행에 끼워 넣는다
        last = card.layout().itemAt(card.layout().count() - 1).widget()
        last.layout().itemAt(1).layout().addWidget(self._ready_profile_sub)

        # 마이크
        self._ready_mic_badge = T.StatusBadge()
        self._ready_mic_sub = T.label("", "sub")
        self._ready_meter = T.LevelMeter()
        self._ready_meter.setMaximumWidth(220)
        voice_btn = T.button("음성 설정")
        voice_btn.clicked.connect(lambda: self.open_page("voice"))
        w, h = T.row_box(spacing=14)
        h.addWidget(self._ready_mic_badge)
        tb = QVBoxLayout()
        tb.setSpacing(6)
        tb.addWidget(T.label("마이크", "lbl"))
        mrow = QHBoxLayout()
        mrow.setSpacing(10)
        mrow.addWidget(self._ready_mic_sub)
        mrow.addWidget(self._ready_meter, 1)
        mrow.addStretch(0)
        tb.addLayout(mrow)
        h.addLayout(tb, 1)
        h.addWidget(voice_btn)
        card.add_row(w)
        lay.addWidget(card)

        # 시작 버튼
        srow = QHBoxLayout()
        srow.setSpacing(14)
        self._start_btn = T.button("  방송 시작", "primary")
        self._start_btn.setIcon(T.icon("play", T.ACCENT_FG, 12))
        self._start_btn.clicked.connect(lambda: self._overlay.toggle_chat() if self._overlay else None)
        self._start_btn.setEnabled(not self._onboarding)
        srow.addWidget(self._start_btn)
        hint = T.label("단축키 Ctrl+Shift+S", "sub")
        srow.addWidget(hint)
        srow.addStretch(1)
        lay.addLayout(srow)

        # 캡처 미리보기
        cap = T.Card("최근 캡처")
        w, h = T.row_box(spacing=16)
        self._cap_preview = QLabel("[ 캡처 미리보기 ]")
        self._cap_preview.setFixedSize(192, 108)
        self._cap_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cap_preview.setStyleSheet("background: #22301f; border-radius: 6px; color: rgba(255,255,255,140);"
                                        " font-size: 11px;")
        h.addWidget(self._cap_preview)
        cv = QVBoxLayout()
        cv.setSpacing(8)
        self._cap_desc = T.label("15초마다 게임 창을 찍고, 화면이 거의 그대로면 건너뛰어 비용을 아낍니다. "
                                 "채팅창이 찍히지 않는지 여기서 확인할 수 있어요.", "sub", wrap=True)
        cv.addWidget(self._cap_desc)
        cap_btn = T.button("지금 찍어보기")
        cap_btn.clicked.connect(self._capture_preview)
        cv.addWidget(cap_btn, 0, Qt.AlignmentFlag.AlignLeft)
        cv.addStretch(1)
        h.addLayout(cv, 1)
        cap.add_row(w)
        lay.addWidget(cap)
        lay.addStretch(1)
        return page

    def _refresh_ready(self):
        keys = _read_env_keys()
        stt = self._config.get("stt") or {}
        stt_on = bool(stt.get("enabled", False))
        need_openai = stt_on and str(stt.get("provider", "gemini")) == "openai"
        done = 0
        # 키
        if not keys["gemini"]:
            self._ready_key_badge.set_state("warn")
            self._ready_key_sub.setText("Gemini 키가 필요해요 — 아래에 붙여넣고 저장")
            self._quick_key_row.setVisible(True)
        elif need_openai and not keys["openai"]:
            self._ready_key_badge.set_state("warn")
            self._ready_key_sub.setText("음성 인식(OpenAI)에 쓸 OpenAI 키가 없어요")
            self._quick_key_row.setVisible(False)
        else:
            self._ready_key_badge.set_state("ok")
            parts = [f"{_PROVIDER_LABEL[p]} 있음" for p in ("gemini", "openai", "claude") if keys[p]]
            self._ready_key_sub.setText(" · ".join(parts))
            self._quick_key_row.setVisible(False)
            done += 1
        # 창
        target = (self._config.get("capture") or {}).get("target_window") or ""
        open_titles = [self._window_combo.itemText(i) for i in range(self._window_combo.count())]
        if not target:
            self._ready_win_badge.set_state("pending")
            self._ready_win_sub.setText("게임을 창 모드로 켜고 아래에서 고르세요")
        elif target in open_titles or any(target.lower() in t.lower() for t in open_titles):
            self._ready_win_badge.set_state("ok")
            self._ready_win_sub.setText(f"{target} · 캡처 중")
            done += 1
        else:
            self._ready_win_badge.set_state("warn")
            self._ready_win_sub.setText(f"'{target}' 창을 찾을 수 없어요 — 게임을 켰는지 확인하세요")
        # 프로필
        name, status, _ = self._profile_state
        if self._onboarding:
            self._ready_profile_badge.set_state("pending")
            self._ready_profile_sub.setText("키를 저장하고 다시 시작하면 만들어집니다")
        elif status == "ok" and name:
            done += 1
        # 마이크
        if not stt_on:
            self._ready_mic_badge.set_state("pending")
            self._ready_mic_sub.setText("음성 인식 꺼짐")
            done += 1  # 선택 기능이라 준비 완료로 친다
        elif self._mic_error:
            self._ready_mic_badge.set_state("error")
            self._ready_mic_sub.setText(self._mic_error[:40])
        elif self._mic_peak >= 0.05:
            self._ready_mic_badge.set_state("ok")
            self._ready_mic_sub.setText(self._device_short_name())
            done += 1
        else:
            self._ready_mic_badge.set_state("pending")
            self._ready_mic_sub.setText(f"{self._device_short_name()} · 말해 보세요")
        self._nav_buttons[0].set_meta(f"{done}/4", T.ACCENT if done == 4 else T.WARN)

    def _save_quick_key(self):
        key = self._quick_key_input.text().strip()
        if not key:
            return
        _write_env_key("GEMINI_API_KEY", key)
        self._quick_key_input.clear()
        logger.info("Gemini API 키 저장(방송 준비)")
        self._need_restart("API 키")
        self._refresh_keys()
        self._refresh_ready()

    def _device_short_name(self) -> str:
        text = self._device_combo.currentText() if hasattr(self, "_device_combo") else ""
        if text.startswith("["):
            text = text.split("]", 1)[-1].strip()
        return (text[:24] + "…") if len(text) > 25 else (text or "기본 장치")

    def _refresh_windows(self):
        excludes = (self._config.get("ui") or {}).get("window_excludes", []) or []
        windows = list_windows(excludes=excludes)
        target = (self._config.get("capture") or {}).get("target_window") or ""
        current = self._window_combo.currentText() or target
        self._window_combo.blockSignals(True)
        self._window_combo.clear()
        for t in windows:
            self._window_combo.addItem(t, userData=t)
        if current and current not in windows:
            self._window_combo.insertItem(0, current, userData=current)
        idx = self._window_combo.findText(current)
        self._window_combo.setCurrentIndex(max(idx, 0))
        self._window_combo.setToolTip(self._window_combo.currentText())
        self._window_combo.blockSignals(False)
        if hasattr(self, "_ready_win_badge"):
            self._refresh_ready()

    def _apply_window(self):
        title = self._window_combo.currentText()
        if not title:
            return
        game_name = extract_game_name(title)
        self._queue.put({"target_window": title})
        self._queue.put({"game_name": game_name})
        paths.save_last_session(target_window=title, game_name=game_name)
        self._config.setdefault("capture", {})["target_window"] = title
        self._profile_state = (game_name, "pending", "")
        self._ready_profile_badge.set_state("pending")
        self._ready_profile_sub.setText(f"{game_name} · 불러오는 중… (처음이면 수십 초)")
        logger.info(f"캡처 창 적용: {title}")
        self._refresh_ready()

    def _regenerate_profile(self):
        title = self._window_combo.currentText() or (self._config.get("capture") or {}).get("target_window")
        if not title:
            self._ready_profile_sub.setText("먼저 게임 창을 고르세요")
            return
        game_name = extract_game_name(title)
        profile_path = paths.user_data_dir() / "game_profiles" / f"{profile_filename(game_name)}.yaml"
        if profile_path.exists():
            profile_path.unlink()
            logger.info(f"기존 프로필 삭제: {profile_path.name}")
        self._queue.put({"game_name": game_name})
        paths.save_last_session(game_name=game_name)
        self._profile_state = (game_name, "pending", "")
        self._ready_profile_badge.set_state("pending")
        self._ready_profile_sub.setText(f"{game_name} · 다시 만드는 중…")
        self._game_profile_status.setText(f"{game_name} · 다시 만드는 중…")
        self._game_profile_status.setStyleSheet(f"color: {T.WARN}; font-size: 12px;")
        self._refresh_ready()

    def _capture_preview(self):
        target = (self._config.get("capture") or {}).get("target_window")
        self._cap_preview.setText("찍는 중…")
        self._run(CapturePreviewWorker(target), done=self._on_capture, error=self._on_capture_error)

    def _on_capture(self, data: bytes, desc: str):
        pm = QPixmap()
        pm.loadFromData(data)
        self._cap_preview.setPixmap(pm.scaled(192, 108, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
        self._cap_desc.setText(f"{desc}\n채팅창이 보이지 않으면 정상이에요(봇이 자기 채팅을 읽지 않음).")

    def _on_capture_error(self, msg: str):
        self._cap_preview.setText("실패")
        self._cap_desc.setText(msg)

    # ── 2. 오버레이 ───────────────────────────────────────────────────────────

    def _build_page_overlay(self) -> QWidget:
        page, lay = self._page("오버레이", "게임 위에 뜨는 채팅창의 모양. 바꾸면 바로 반영됩니다.")
        ui = self._config.get("ui") or {}
        ov = self._overlay
        t = ov.transparency() if ov else 15

        card = T.Card()
        w, h = T.row_box(spacing=16)
        left = QVBoxLayout()
        left.setSpacing(3)
        left.addWidget(T.label("투명도", "lbl"))
        left.addWidget(T.label("100%면 배경 없이 글자만 남고, 글자에 외곽선이 붙어요.", "sub", wrap=True))
        srow = QHBoxLayout()
        srow.setContentsMargins(0, 10, 0, 0)
        srow.setSpacing(12)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setSingleStep(5)
        self._slider.setPageStep(10)
        self._slider.setValue(t)
        self._slider.setAccessibleName("투명도")
        self._slider.valueChanged.connect(lambda v: ov.set_transparency(v) if ov else self.sync_transparency(v))
        srow.addWidget(self._slider, 1)
        self._op_val = T.label(f"{t}%", "mono")
        self._op_val.setFixedWidth(40)
        srow.addWidget(self._op_val)
        left.addLayout(srow)
        h.addLayout(left, 1)
        self._preview = _TransparencyPreview()
        self._preview.set_transparency(t)
        h.addWidget(self._preview)
        card.add_row(w)

        self._ct_switch = T.Switch(False)
        self._ct_switch.setAccessibleName("클릭 통과")
        if ov is not None and not ov.click_through_available():
            self._ct_switch.setEnabled(False)
            ct_sub = "Ctrl+Shift+O 단축키를 등록하지 못해 쓸 수 없어요(다른 프로그램이 사용 중)."
        else:
            ct_sub = "채팅창 위를 클릭해도 게임으로 전달돼요. Ctrl+Shift+O 로 켜고 끕니다."
        self._ct_switch.toggled.connect(lambda on: ov.set_click_through(on) if ov else None)
        card.add_row(title="클릭 통과", sub=ct_sub, right=self._ct_switch)

        fade = T.Switch(bool(ui.get("fade_old", True)))
        fade.setAccessibleName("오래된 메시지 흐리게")
        fade.toggled.connect(self._on_fade)
        card.add_row(title="오래된 메시지 흐리게", sub="새 채팅이 눈에 먼저 들어오게 합니다.", right=fade)

        self._show_stt_switch = T.Switch(bool(ui.get("show_stt_history", False)))
        self._show_stt_switch.setAccessibleName("내 말 표시")
        self._show_stt_switch.toggled.connect(self._on_show_stt)
        card.add_row(title="내 말(받아쓰기) 표시", sub="음성 인식 결과를 채팅 사이에 회색으로 보여줘요. 잘못 들렸는지 확인할 때 켜세요.",
                     right=self._show_stt_switch)

        excl = T.Switch(bool(ui.get("exclude_from_capture", True)))
        excl.setAccessibleName("화면 분석에서 채팅창 숨기기")
        excl.toggled.connect(self._on_capture_exclusion)
        card.add_row(title="화면 분석에서 채팅창 숨기기",
                     sub="켜두면 봇이 채팅창 글자를 화면으로 읽지 않아요. 대신 OBS 같은 녹화 프로그램에도 채팅창이 찍히지 않습니다.",
                     right=excl)
        lay.addWidget(card)

        card2 = T.Card("글자")
        fs = T.combo([("작게 · 12", 12), ("보통 · 14", 14), ("크게 · 16", 16), ("아주 크게 · 18", 18)],
                     current=int(ui.get("font_size", 14)), width=180)
        fs.currentIndexChanged.connect(lambda _: self._on_font_size(fs.currentData()))
        card2.add_row(title="글자 크기", right=fs)
        mm = QSpinBox()
        mm.setRange(50, 500)
        mm.setSingleStep(10)
        mm.setValue(int(ui.get("max_messages", 150)))
        mm.setFixedWidth(84)
        mm.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        mm.valueChanged.connect(self._on_max_messages)
        card2.add_row(title="보관할 메시지 수", sub="넘으면 오래된 것부터 지워져요(내 말 표시도 한 줄씩 차지).", right=mm)
        lay.addWidget(card2)
        lay.addStretch(1)
        return page

    def _on_fade(self, on: bool):
        self._save("ui", {"fade_old": bool(on)})
        if self._overlay:
            self._overlay.set_fade(on)

    def _on_show_stt(self, on: bool):
        self._save("ui", {"show_stt_history": bool(on)})
        if self._overlay:
            self._overlay.set_show_stt(on)

    def _on_capture_exclusion(self, on: bool):
        self._save("ui", {"exclude_from_capture": bool(on)})
        if self._overlay:
            self._overlay.set_capture_exclusion(on)

    def _on_font_size(self, size):
        if size is None:
            return
        self._save("ui", {"font_size": int(size)})
        if self._overlay:
            self._overlay.set_font_size(int(size))

    def _on_max_messages(self, value: int):
        self._save("ui", {"max_messages": int(value)})
        if self._overlay:
            self._overlay.set_max_messages(int(value))

    def showEvent(self, event):
        super().showEvent(event)
        winapi.set_exclude_from_capture(
            int(self.winId()), bool((self._config.get("ui") or {}).get("exclude_from_capture", True)))
        self._refresh_keys()
        self._on_page_changed()

    def hideEvent(self, event):
        self._stop_mic()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= self._titlebar.height():
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ── 3. 음성 인식 ─────────────────────────────────────────────────────────

    def _build_page_voice(self) -> QWidget:
        stt = self._config.get("stt") or {}
        enabled = T.Switch(bool(stt.get("enabled", False)))
        enabled.setAccessibleName("음성 인식 사용")
        enabled.toggled.connect(lambda on: self._save_stt({"enabled": bool(on)}, restart=True))
        page, lay = self._page("음성 인식", "혼잣말을 받아 적어 봇들이 반응하게 합니다.", header_right=enabled)

        # 마이크
        card = T.Card("마이크")
        dev_box = QHBoxLayout()
        dev_box.setSpacing(8)
        self._device_combo = T.combo(width=320)
        self._populate_devices(str(stt.get("device", "default")))
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        dev_box.addWidget(self._device_combo)
        dref = QPushButton()
        dref.setObjectName("btn")
        dref.setIcon(T.icon("refresh", T.TEXT_2, 14))
        dref.setFixedWidth(36)
        dref.setToolTip("장치 목록 새로고침")
        dref.setAccessibleName("장치 목록 새로고침")
        dref.clicked.connect(lambda: self._populate_devices(self._selected_device()))
        dev_box.addWidget(dref)
        dw = QWidget()
        dw.setLayout(dev_box)
        dev_box.setContentsMargins(0, 0, 0, 0)
        card.add_row(title="입력 장치", right=dw)

        w, h = T.row_box(spacing=14)
        lv = QVBoxLayout()
        lv.setSpacing(8)
        lv.addWidget(T.label("입력 레벨", "lbl"))
        mrow = QHBoxLayout()
        mrow.setSpacing(10)
        self._voice_meter = T.LevelMeter()
        mrow.addWidget(self._voice_meter, 1)
        self._voice_meter_lbl = T.label("", "sub")
        mrow.addWidget(self._voice_meter_lbl)
        lv.addLayout(mrow)
        h.addLayout(lv, 1)
        self._stt_test_btn = T.button("4초 받아쓰기 테스트")
        self._stt_test_btn.clicked.connect(self._start_stt_test)
        h.addWidget(self._stt_test_btn)
        card.add_row(w)

        self._stt_result_row, rl = T.row_box(spacing=10)
        self._stt_result_row.setObjectName("sttres")
        self._stt_result_row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._stt_result_row.setStyleSheet("QWidget#sttres { background: #13201B; border-bottom-left-radius: 10px;"
                                           " border-bottom-right-radius: 10px; }")
        self._stt_result = QLabel("")
        self._stt_result.setWordWrap(True)
        self._stt_result.setStyleSheet("color: #CFEFE2; font-size: 13px; background: transparent;")
        rl.addWidget(self._stt_result, 1)
        self._stt_result_meta = T.label("", "mono")
        self._stt_result_meta.setStyleSheet(f"color: {T.MUTED}; font-size: 11px; background: transparent;")
        rl.addWidget(self._stt_result_meta)
        self._stt_result_row.setVisible(False)
        card.add_widget(self._stt_result_row)
        lay.addWidget(card)

        # 인식 모델
        card = T.Card("인식 모델")
        cur_pm = _pm(str(stt.get("provider", "gemini")), str(stt.get("model", "gemini-2.5-flash")))
        self._stt_model_combo = T.combo(
            [(_model_text(p, m, _STT_RECOMMENDED), _pm(p, m)) for p, m in _STT_MODELS], width=320)
        T.select_data(self._stt_model_combo, cur_pm, fallback_text=_model_text(*cur_pm.split("|", 1)))
        self._stt_model_combo.currentIndexChanged.connect(self._on_stt_model_changed)
        card.add_row(title="모델", sub="게임 혼잣말 40문장 실측(2026-10) 기준", right=self._stt_model_combo)
        grid_w = QWidget()
        grid_w.setObjectName("mgrid")
        grid_w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        grid_w.setStyleSheet(f"QWidget#mgrid {{ background: {T.DIVIDER}; border-bottom-left-radius: 10px;"
                             " border-bottom-right-radius: 10px; }")
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 1, 0, 0)
        grid.setSpacing(1)
        self._metric_vals = []
        for i, cap in enumerate(("정확도", "응답 속도", "말한 1시간당")):
            cell = QWidget()
            cell.setObjectName("mcell")
            cell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            cell.setStyleSheet(f"QWidget#mcell {{ background: {T.SURFACE};"
                               + (" border-bottom-left-radius: 10px;" if i == 0 else "")
                               + (" border-bottom-right-radius: 10px;" if i == 2 else "") + " }")
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(16, 12, 16, 12)
            cl.setSpacing(4)
            c1 = QLabel(cap)
            c1.setStyleSheet(f"color: {T.MUTED}; font-size: 11px; background: transparent;")
            cl.addWidget(c1)
            v = QLabel("—")
            v.setStyleSheet(f"font-family: '{T.MONO_FAMILY}'; font-size: 18px; background: transparent;")
            cl.addWidget(v)
            self._metric_vals.append(v)
            grid.addWidget(cell, 0, i)
        card.add_widget(grid_w)
        self._update_stt_metrics()
        lay.addWidget(card)

        # 반응 + 고급
        card = T.Card()
        weight = T.Segmented([("off", "끔"), ("low", "낮게"), ("medium", "보통"), ("high", "적극적")],
                             value=str(stt.get("default_weight", "high")).lower())
        weight.changed.connect(lambda v: self._save_stt({"default_weight": v}, runtime=True))
        card.add_row(title="봇 반응 강도", sub="개별 설정이 없는 봇이 내 말에 얼마나 받아칠지", right=weight)

        w, h = T.row_box(spacing=8)
        self._adv_btn = QPushButton("  고급 설정")
        self._adv_btn.setObjectName("link")
        self._adv_btn.setIcon(T.icon("chevron_right", T.TEXT_2, 10, stroke=2))
        self._adv_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._adv_btn.clicked.connect(self._toggle_advanced)
        h.addWidget(self._adv_btn)
        h.addStretch(1)
        h.addWidget(T.label("감지 민감도, 반영 시간, 받아쓰기 프롬프트 등", "sub"))
        card.add_row(w)
        self._adv = self._build_stt_advanced(stt)
        self._adv.setVisible(False)
        card.add_widget(self._adv)
        lay.addWidget(card)
        lay.addStretch(1)
        return page

    def _build_stt_advanced(self, stt: dict) -> QWidget:
        box = QWidget()
        g = QGridLayout(box)
        g.setContentsMargins(16, 4, 16, 16)
        g.setHorizontalSpacing(24)
        g.setVerticalSpacing(12)

        def num(label_text, key, default, lo, hi, step, decimals, runtime, row, col, tip=""):
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color: {T.TEXT_2}; font-size: 13px;")
            lbl.setToolTip(tip)
            if decimals:
                sp = QDoubleSpinBox()
                sp.setDecimals(decimals)
            else:
                sp = QSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setValue(type(lo)(stt.get(key, default)))
            sp.setFixedWidth(84)
            sp.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            sp.setToolTip(tip)
            sp.valueChanged.connect(lambda v, k=key: self._save_stt({k: v}, runtime=runtime, restart=not runtime))
            cell = QHBoxLayout()
            cell.addWidget(lbl, 1)
            cell.addWidget(sp)
            g.addLayout(cell, row, col)

        num("반영할 최근 발화(초)", "lookback_seconds", 20.0, 1.0, 120.0, 1.0, 1, True, 0, 0,
            "화면 분석 직전 N초 안의 발화만 채팅에 반영")
        num("최소 글자 수", "min_transcript_chars", 2, 0, 50, 1, 0, True, 0, 1, "이보다 짧은 받아쓰기는 버림")
        num("음성 감지 민감도", "vad_threshold", 0.5, 0.1, 0.9, 0.05, 2, False, 1, 0, "낮을수록 작은 소리도 말로 잡음")
        num("입력 증폭", "input_gain", 1.0, 0.5, 8.0, 0.5, 1, False, 1, 1, "마이크가 작게 잡히면 올리세요")
        num("최소 발화(ms)", "min_speech_ms", 250, 0, 5000, 50, 0, False, 2, 0)
        num("끝 판정 침묵(ms)", "min_silence_ms", 500, 0, 5000, 50, 0, False, 2, 1)
        num("앞뒤 여유(ms)", "speech_pad_ms", 100, 0, 5000, 50, 0, False, 3, 0)
        num("최대 발화(초)", "max_utterance_seconds", 30.0, 5.0, 120.0, 5.0, 1, False, 3, 1)

        lang = QLineEdit(str(stt.get("language", "ko")))
        lang.setFixedWidth(84)
        lang.editingFinished.connect(lambda: self._save_stt({"language": lang.text().strip() or "ko"}, restart=True))
        cell = QHBoxLayout()
        ll = QLabel("언어")
        ll.setStyleSheet(f"color: {T.TEXT_2}; font-size: 13px;")
        cell.addWidget(ll, 1)
        cell.addWidget(lang)
        g.addLayout(cell, 4, 0)

        pl = QLabel("받아쓰기 프롬프트")
        pl.setStyleSheet(f"color: {T.TEXT_2}; font-size: 13px;")
        g.addWidget(pl, 5, 0, 1, 2)
        self._stt_prompt = QPlainTextEdit(str(stt.get("transcribe_prompt", "")))
        self._stt_prompt.setFixedHeight(72)
        self._stt_prompt_timer = QTimer(self)
        self._stt_prompt_timer.setSingleShot(True)
        self._stt_prompt_timer.setInterval(800)
        self._stt_prompt_timer.timeout.connect(
            lambda: self._save_stt({"transcribe_prompt": self._stt_prompt.toPlainText().strip()}, restart=True))
        self._stt_prompt.textChanged.connect(self._stt_prompt_timer.start)
        g.addWidget(self._stt_prompt, 6, 0, 1, 2)
        return box

    def _toggle_advanced(self):
        show = not self._adv.isVisible()
        self._adv.setVisible(show)
        self._adv_btn.setIcon(T.icon("chevron_down" if show else "chevron_right", T.TEXT_2, 10, stroke=2))

    def _save_stt(self, values: dict, runtime: bool = False, restart: bool = False):
        self._save("stt", values)
        if runtime:
            stt = self._config.get("stt") or {}
            self._queue.put({"stt_runtime": {
                "lookback_seconds": float(stt.get("lookback_seconds", 20.0)),
                "min_transcript_chars": int(stt.get("min_transcript_chars", 2)),
                "default_weight": str(stt.get("default_weight", "high")),
            }})
        if restart:
            self._need_restart("음성 인식")
        self._refresh_ready()

    def _populate_devices(self, want: str):
        c = self._device_combo
        c.blockSignals(True)
        c.clear()
        c.addItem("기본 장치 (시스템 설정)", userData="default")
        try:
            import sounddevice as sd
            for idx, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    c.addItem(f"[{idx}] {dev['name']}", userData=str(idx))
        except Exception as e:
            logger.warning(f"입력 장치 조회 실패: {e}")
        T.select_data(c, str(want))
        c.setToolTip(c.currentText())
        c.blockSignals(False)

    def _selected_device(self) -> str:
        data = self._device_combo.currentData()
        return str(data) if data is not None else "default"

    def _on_device_changed(self, _):
        self._device_combo.setToolTip(self._device_combo.currentText())
        self._save_stt({"device": self._selected_device()}, restart=True)
        self._mic_peak = 0.0
        self._mic_error = ""
        self._stop_mic()
        self._on_page_changed()

    def _on_stt_model_changed(self, _):
        data = self._stt_model_combo.currentData() or ""
        provider, _, model = data.partition("|")
        self._save_stt({"provider": provider, "model": model}, restart=True)
        self._update_stt_metrics()
        self._refresh_keys()

    def _update_stt_metrics(self):
        data = self._stt_model_combo.currentData() or ""
        model = data.partition("|")[2]
        acc, lat = _STT_MEASURED.get(model, (None, None))
        try:
            from src.utils import pricing
            per_min = pricing.stt_cost_per_min(model)
        except Exception:
            per_min = None
        self._metric_vals[0].setText(f"{acc}%" if acc else "측정 안 됨")
        self._metric_vals[1].setText(f"{lat:.1f}초" if lat else "—")
        self._metric_vals[2].setText(f"${per_min * 60:.2f}" if per_min else "—")
        self._nav_buttons[2].set_meta(model[:16])

    # 마이크 레벨 (방송 준비/음성 페이지가 보일 때만 열어 둔다)
    def _start_mic(self):
        if self._mic_worker is not None:
            return
        if not bool((self._config.get("stt") or {}).get("enabled", False)) and \
                _PAGES[self._stack.currentIndex()][0] == "ready":
            return
        sr = int((self._config.get("stt") or {}).get("sample_rate", 16000))
        self._mic_error = ""
        self._mic_worker = MicLevelWorker(parse_device(self._selected_device()), sample_rate=sr)
        self._mic_thread = self._run(self._mic_worker, level=self._on_mic_level,
                                     error=self._on_mic_error, stopped=self._on_mic_stopped)

    def _stop_mic(self):
        if self._mic_worker is not None:
            self._mic_worker.stop()
        self._mic_worker = None
        if hasattr(self, "_voice_meter"):
            self._voice_meter.reset()
            self._ready_meter.reset()

    def _on_mic_level(self, level: float):
        prev = self._mic_peak
        self._mic_peak = max(self._mic_peak, level)
        self._voice_meter.set_level(level)
        self._ready_meter.set_level(level)
        self._voice_meter_lbl.setText("말하는 중" if level > 0.05 else "조용함")
        if prev < 0.05 <= self._mic_peak:
            self._refresh_ready()

    def _on_mic_error(self, msg: str):
        logger.warning(f"마이크 레벨 오류: {msg}")
        self._mic_error = msg
        self._voice_meter_lbl.setText("마이크를 열 수 없음")
        self._refresh_ready()

    def _on_mic_stopped(self):
        self._mic_worker = None

    def _start_stt_test(self):
        if self._stt_test_running:
            return
        data = self._stt_model_combo.currentData() or "gemini|gemini-2.5-flash"
        provider, _, model = data.partition("|")
        keys = _read_env_keys()
        if provider == "openai" and not keys["openai"]:
            self._show_stt_result("OpenAI 키가 없어요. 'AI 모델 · 키'에서 추가하세요.", "", T.WARN)
            return
        if provider == "gemini" and not keys["gemini"]:
            self._show_stt_result("Gemini 키가 없어요. 'AI 모델 · 키'에서 추가하세요.", "", T.WARN)
            return
        stt = self._config.get("stt") or {}
        worker = STTTestWorker(
            device=parse_device(self._selected_device()),
            sample_rate=int(stt.get("sample_rate", 16000)),
            record_seconds=4.0, provider=provider, model=model,
            language=str(stt.get("language", "ko")),
            prompt=str(stt.get("transcribe_prompt", "")),
            gemini_key=keys["gemini"], openai_key=keys["openai"],
            normalize_target_level=float(stt.get("normalize_target_level", 0.1)),
            normalize_percentile=float(stt.get("normalize_percentile", 95.0)),
            normalize_max_gain=float(stt.get("normalize_max_gain", 20.0)),
        )
        self._stt_test_running = True
        self._stt_test_btn.setEnabled(False)
        self._run(worker, progress=lambda m: self._show_stt_result(m, "", T.MUTED),
                  finished=self._on_stt_test_done, error=self._on_stt_test_error)

    def _on_stt_test_done(self, text: str, elapsed: float):
        self._stt_test_running = False
        self._stt_test_btn.setEnabled(True)
        if text:
            self._show_stt_result(f"“{text}”", f"{elapsed:.1f}초", "#CFEFE2")
        else:
            self._show_stt_result("알아들은 말이 없어요. 더 또렷하게 말하거나 마이크를 확인하세요.",
                                  f"{elapsed:.1f}초", T.WARN)

    def _on_stt_test_error(self, msg: str):
        self._stt_test_running = False
        self._stt_test_btn.setEnabled(True)
        self._show_stt_result(msg, "", T.ERROR)

    def _show_stt_result(self, text: str, meta: str, color: str):
        self._stt_result_row.setVisible(True)
        self._stt_result.setText(text)
        self._stt_result.setStyleSheet(f"color: {color}; font-size: 13px; background: transparent;")
        self._stt_result_meta.setText(meta)

    # ── 4. AI 모델 · 키 ──────────────────────────────────────────────────────

    def _build_page_ai(self) -> QWidget:
        page, lay = self._page("AI 모델 · 키", "키는 이 PC에만 저장되고 화면에 다시 표시되지 않아요.")
        ai = self._config.get("ai") or {}

        card = T.Card("API 키")
        self._key_rows: dict[str, dict] = {}
        for provider, optional in (("gemini", False), ("openai", False), ("claude", True)):
            w, h = T.row_box(vertical=True, spacing=10)
            top = QHBoxLayout()
            top.setSpacing(12)
            name = T.label(_PROVIDER_LABEL[provider], "lbl")
            name.setFixedWidth(64)
            top.addWidget(name)
            chip = T.Chip()
            top.addWidget(chip)
            msg = T.label("", "sub")
            top.addWidget(msg, 1)
            check = T.button("연결 확인")
            check.clicked.connect(lambda _=False, p=provider: self._check_key(p))
            top.addWidget(check)
            edit = T.button("변경")
            top.addWidget(edit)
            h.addLayout(top)
            editor = QWidget()
            el = QHBoxLayout(editor)
            el.setContentsMargins(76, 0, 0, 0)
            el.setSpacing(8)
            inp = QLineEdit()
            inp.setEchoMode(QLineEdit.EchoMode.Password)
            inp.setPlaceholderText(f"{_KEY_ENV[provider]} 붙여넣기")
            el.addWidget(inp, 1)
            save = T.button("저장")
            el.addWidget(save)
            cancel = T.button("취소")
            el.addWidget(cancel)
            editor.setVisible(False)
            h.addWidget(editor)
            edit.clicked.connect(lambda _=False, e=editor, i=inp: (e.setVisible(True), i.setFocus()))
            cancel.clicked.connect(lambda _=False, e=editor, i=inp: (i.clear(), e.setVisible(False)))
            save.clicked.connect(lambda _=False, p=provider: self._save_key(p))
            inp.returnPressed.connect(lambda p=provider: self._save_key(p))
            card.add_row(w)
            self._key_rows[provider] = {"chip": chip, "msg": msg, "check": check, "edit": edit,
                                        "editor": editor, "input": inp, "optional": optional}
        lay.addWidget(card)

        card = T.Card("모델")
        vp = str(ai.get("vision_provider", "gemini"))
        vm = ai.get("claude_vision_model" if vp == "claude" else "vision_model", "gemini-2.5-flash")
        self._vision_combo = T.combo([(_model_text(p, m), _pm(p, m)) for p, m in _VISION_MODELS], width=300)
        T.select_data(self._vision_combo, _pm(vp, vm), fallback_text=_model_text(vp, vm))
        self._vision_combo.currentIndexChanged.connect(lambda _: self._on_ai_model("vision"))
        card.add_row(title="화면 분석", sub="15초마다 게임 화면을 읽어요", right=self._vision_combo)

        cp = str(ai.get("chat_provider", "gemini"))
        cm = ai.get("claude_chat_model" if cp == "claude" else "chat_model", "gemini-2.5-flash-lite")
        self._chat_combo = T.combo([(_model_text(p, m), _pm(p, m)) for p, m in _CHAT_MODELS], width=300)
        T.select_data(self._chat_combo, _pm(cp, cm), fallback_text=_model_text(cp, cm))
        self._chat_combo.currentIndexChanged.connect(lambda _: self._on_ai_model("chat"))
        card.add_row(title="채팅 생성", sub="봇 채팅을 한 번에 몰아서 만들어요", right=self._chat_combo)

        pmodel = str(ai.get("profile_model", _PROFILE_MODELS[0]))
        self._profile_model_combo = T.combo([(_model_text("gemini", m), m) for m in _PROFILE_MODELS], width=300)
        T.select_data(self._profile_model_combo, pmodel, fallback_text=_model_text("gemini", pmodel))
        self._profile_model_combo.currentIndexChanged.connect(self._on_profile_model)
        card.add_row(title="게임 프로필 생성", sub="구글 검색으로 게임 정보를 모아요(Gemini 전용) · 다음 생성부터 적용",
                     right=self._profile_model_combo)

        prompt_btn = T.button("편집…")
        prompt_btn.clicked.connect(self._edit_vision_prompt)
        self._prompt_sub = T.label("", "sub")
        card.add_row(title="화면 분석 프롬프트", right=prompt_btn)
        last = card.layout().itemAt(card.layout().count() - 1).widget()
        last.layout().itemAt(0).layout().addWidget(self._prompt_sub)
        self._update_prompt_sub()
        self._ai_warn = T.label("", "sub", wrap=True)
        self._ai_warn.setStyleSheet(f"color: {T.WARN}; font-size: 12px; padding: 0 16px 14px 16px;")
        self._ai_warn.setVisible(False)
        card.add_widget(self._ai_warn)
        lay.addWidget(card)
        lay.addStretch(1)
        return page

    def _refresh_keys(self):
        if not hasattr(self, "_key_rows"):
            return
        keys = _read_env_keys()
        stt = self._config.get("stt") or {}
        for provider, row in self._key_rows.items():
            has = bool(keys[provider])
            row["check"].setEnabled(has)
            row["edit"].setText("변경" if has else "추가")
            if has:
                row["chip"].set_tone("ok", "있음")
            elif provider == "openai" and str(stt.get("provider")) == "openai":
                row["chip"].set_tone("warn", "필요")
            elif row["optional"] or provider == "openai":
                row["chip"].set_tone("neutral", "선택 사항 · 없음")
            else:
                row["chip"].set_tone("warn", "필요")
        self._refresh_ready() if hasattr(self, "_ready_key_badge") else None

    def _check_key(self, provider: str):
        key = _read_env_keys()[provider]
        row = self._key_rows[provider]
        row["msg"].setText("확인 중…")
        row["check"].setEnabled(False)
        self._run(KeyCheckWorker(provider, key), done=self._on_key_checked)

    def _on_key_checked(self, provider: str, ok: bool, msg: str):
        row = self._key_rows[provider]
        row["check"].setEnabled(True)
        row["chip"].set_tone("ok" if ok else "error", "연결됨" if ok else "실패")
        row["msg"].setText("" if ok else msg)

    def _save_key(self, provider: str):
        row = self._key_rows[provider]
        value = row["input"].text().strip()
        if not value:
            return
        try:
            _write_env_key(_KEY_ENV[provider], value)
        except Exception as e:
            row["msg"].setText(f"저장 실패: {e}")
            return
        row["input"].clear()
        row["editor"].setVisible(False)
        row["msg"].setText("저장됨 — 다시 시작하면 적용돼요")
        logger.info(f"{_KEY_ENV[provider]} 저장")
        self._need_restart("API 키")
        self._refresh_keys()

    def _on_ai_model(self, kind: str):
        combo = self._vision_combo if kind == "vision" else self._chat_combo
        provider, _, model = (combo.currentData() or "").partition("|")
        if provider == "claude" and not _read_env_keys()["claude"]:
            # 키 없이 Claude 로 바꾸면 AI 프로세스가 클라이언트를 못 만든다 — 적용하지 않는다.
            self._ai_warn.setText("Claude 모델은 Claude 키가 있어야 쓸 수 있어요. 위에서 키를 추가하고 다시 시작한 뒤 고르세요.")
            self._ai_warn.setVisible(True)
            ai = self._config.get("ai") or {}
            pkey = "vision_provider" if kind == "vision" else "chat_provider"
            p = str(ai.get(pkey, "gemini"))
            mkey = ("claude_" if p == "claude" else "") + ("vision_model" if kind == "vision" else "chat_model")
            combo.blockSignals(True)
            T.select_data(combo, _pm(p, ai.get(mkey, "")))
            combo.blockSignals(False)
            return
        self._ai_warn.setVisible(False)
        values = {f"{kind}_provider": provider}
        values[("claude_" if provider == "claude" else "") + f"{kind}_model"] = model
        self._save("ai", values)
        self._queue.put({f"{kind}_provider": provider, f"{kind}_model": model})
        logger.info(f"{kind} 모델 변경: {provider} / {model}")

    def _on_profile_model(self, _):
        model = self._profile_model_combo.currentData()
        if not model:
            return
        self._save("ai", {"profile_model": model})
        self._queue.put({"profile_model": model})

    def _update_prompt_sub(self):
        custom = bool(((self._config.get("ai") or {}).get("vision_prompt") or "").strip())
        self._prompt_sub.setText("직접 고친 프롬프트 사용 중" if custom else "고급 · 묘사 길이와 형식을 직접 고칩니다")

    def _edit_vision_prompt(self):
        from src.ai.model_client import _DEFAULT_VISION_BODY
        cur = ((self._config.get("ai") or {}).get("vision_prompt") or "").strip() or _DEFAULT_VISION_BODY
        dlg = _PromptDialog(cur, _DEFAULT_VISION_BODY, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        text = dlg.editor.toPlainText().strip()
        # 기본값과 같으면 오버라이드를 두지 않는다(빈 문자열 = 기본 사용 → 기본 개선이 자동 반영).
        store = "" if text == _DEFAULT_VISION_BODY.strip() else text
        self._save("ai", {"vision_prompt": store})
        self._queue.put({"vision_prompt": store})
        self._update_prompt_sub()

    # ── 5. 게임 · 프로필 ─────────────────────────────────────────────────────

    def _build_page_game(self) -> QWidget:
        page, lay = self._page("게임 · 프로필", "봇들이 '그 게임을 아는' 것처럼 말하게 하는 게임 정보와 기타 설정.")
        ui = self._config.get("ui") or {}

        card = T.Card("게임 프로필")
        w, h = T.row_box(spacing=8)
        self._profile_combo = T.combo()
        self._refresh_profiles()
        h.addWidget(self._profile_combo, 1)
        apply_p = T.button("이 프로필 쓰기")
        apply_p.clicked.connect(self._apply_profile)
        h.addWidget(apply_p)
        regen = T.button("다시 만들기")
        regen.clicked.connect(self._regenerate_profile)
        h.addWidget(regen)
        card.add_row(w)
        w, h = T.row_box()
        self._game_profile_status = T.label("불러오는 중…", "sub", wrap=True)
        h.addWidget(self._game_profile_status)
        card.add_row(w)
        lay.addWidget(card)

        card = T.Card("창 목록에서 뺄 이름")
        chips_w = QWidget()
        self._chip_flow = _FlowLayout(chips_w)
        self._chip_flow.setContentsMargins(16, 12, 16, 4)
        card.add_widget(chips_w)
        self._excludes = list(ui.get("window_excludes", []) or [])
        self._render_excludes()
        w, h = T.row_box(spacing=8)
        self._excl_input = QLineEdit()
        self._excl_input.setPlaceholderText("%Discord%  (포함)   Steam%  (시작)   %탐색기  (끝)")
        self._excl_input.returnPressed.connect(self._add_exclude)
        h.addWidget(self._excl_input, 1)
        add = T.button("추가")
        add.clicked.connect(self._add_exclude)
        h.addWidget(add)
        card.add_row(w)
        lay.addWidget(card)

        card = T.Card("기록")
        log_sw = T.Switch(bool((self._config.get("logging") or {}).get("save_to_file", True)))
        log_sw.setAccessibleName("로그 파일 저장")
        log_sw.toggled.connect(lambda on: (self._save("logging", {"save_to_file": bool(on)}),
                                           self._need_restart("로그 저장")))
        card.add_row(title="로그 파일 저장", sub="하루 1개 파일, 7일 지난 로그는 자동 삭제", right=log_sw)
        open_btn = T.button("폴더 열기")
        open_btn.clicked.connect(self._open_user_data_dir)
        card.add_row(title="데이터 폴더", sub=str(paths.user_data_dir()), right=open_btn)
        lay.addWidget(card)

        info = T.label(f"Solo Stream v{APP_VERSION}  ·  문의 contact.team8889@gmail.com", "sub")
        info.setStyleSheet(f"color: {T.FAINT}; font-size: 12px;")
        lay.addWidget(info)
        lay.addStretch(1)
        return page

    def _refresh_profiles(self):
        import yaml
        c = self._profile_combo
        current = c.currentText()
        c.blockSignals(True)
        c.clear()
        c.addItem("자동 (창 이름에서 찾기)", userData=None)
        profile_dir = paths.user_data_dir() / "game_profiles"
        if profile_dir.exists():
            for p in sorted(profile_dir.glob("*.yaml")):
                if p.stem == "default":
                    continue
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        display = (yaml.safe_load(f) or {}).get("name") or p.stem
                except Exception:
                    display = p.stem
                c.addItem(display, userData=p.stem)
        idx = c.findText(current)
        c.setCurrentIndex(max(idx, 0))
        c.blockSignals(False)

    def _apply_profile(self):
        if self._profile_combo.currentIndex() == 0:
            title = (self._config.get("capture") or {}).get("target_window") or self._window_combo.currentText()
            game_name = extract_game_name(title) if title else "Unknown Game"
        else:
            game_name = self._profile_combo.currentData() or self._profile_combo.currentText()
        self._queue.put({"game_name": game_name})
        paths.save_last_session(game_name=game_name)
        self._profile_state = (game_name, "pending", "")
        self._game_profile_status.setText(f"{game_name} · 불러오는 중…")
        self._game_profile_status.setStyleSheet(f"color: {T.MUTED}; font-size: 12px;")
        self._ready_profile_badge.set_state("pending")
        self._ready_profile_sub.setText(f"{game_name} · 불러오는 중…")
        logger.info(f"게임 프로필 적용: {game_name}")

    def _render_excludes(self):
        while self._chip_flow.count():
            it = self._chip_flow.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        for pat in self._excludes:
            b = QPushButton(f"{pat}  ×")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip("눌러서 빼기")
            b.setAccessibleName(f"{pat} 제외 패턴 삭제")
            b.setFixedHeight(26)
            b.setStyleSheet(f"QPushButton {{ background: #22242B; color: {T.TEXT_2}; border: none; border-radius: 13px;"
                            " padding: 0 10px; font-size: 12px; }"
                            "QPushButton:hover { background: #2E3038; color: #fff; }")
            b.clicked.connect(lambda _=False, p=pat: self._remove_exclude(p))
            self._chip_flow.addWidget(b)
        self._chip_flow.parentWidget().updateGeometry()

    def _add_exclude(self):
        pat = self._excl_input.text().strip()
        if pat and pat not in self._excludes:
            self._excludes.append(pat)
            self._save("ui", {"window_excludes": self._excludes})
            self._render_excludes()
            self._refresh_windows()
        self._excl_input.clear()

    def _remove_exclude(self, pat: str):
        if pat in self._excludes:
            self._excludes.remove(pat)
            self._save("ui", {"window_excludes": self._excludes})
            self._render_excludes()
            self._refresh_windows()

    def _open_user_data_dir(self):
        import os
        try:
            os.startfile(str(paths.ensure_user_data()))  # type: ignore[attr-defined]
        except Exception as e:
            logger.error(f"데이터 폴더 열기 실패: {e}")

    # ── 6. 사용량 ────────────────────────────────────────────────────────────

    def _build_page_usage(self) -> QWidget:
        from src.utils import pricing
        page, lay = self._page("사용량", f"지금 고른 모델 기준의 대략적인 비용이에요. 단가 {pricing.PRICING_AS_OF} 공개가, "
                                       "토큰량은 추정치라 실제 청구액과 다를 수 있어요.")
        d = pricing.USAGE_DEFAULTS
        card = T.Card("사용량 가정")
        box = QWidget()
        g = QGridLayout(box)
        g.setContentsMargins(16, 10, 16, 16)
        g.setHorizontalSpacing(24)
        g.setVerticalSpacing(12)
        self._cost_spins = {}
        specs = [
            ("hours", "세션 길이(시간)", 0.5, 24.0, 0.5, 1),
            ("speech_min_per_hour", "시간당 말하는 분량(분)", 0.0, 60.0, 5.0, 0),
            ("screen_change_pct", "화면이 바뀌는 비율(%)", 0.0, 100.0, 5.0, 0),
            ("chat_calls_per_hour", "시간당 채팅 생성(회)", 0.0, 1000.0, 10.0, 0),
        ]
        for i, (key, text, lo, hi, step, dec) in enumerate(specs):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {T.TEXT_2}; font-size: 13px;")
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(dec)
            sp.setValue(float(d[key]))
            sp.setFixedWidth(84)
            sp.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            sp.valueChanged.connect(self._recompute_cost)
            cell = QHBoxLayout()
            cell.addWidget(lbl, 1)
            cell.addWidget(sp)
            g.addLayout(cell, i // 2, i % 2)
            self._cost_spins[key] = sp
        card.add_widget(box)
        lay.addWidget(card)

        card = T.Card("예상 비용")
        w, h = T.row_box()
        self._cost_out = QLabel()
        self._cost_out.setTextFormat(Qt.TextFormat.RichText)
        self._cost_out.setWordWrap(True)
        h.addWidget(self._cost_out, 1)
        card.add_row(w)
        lay.addWidget(card)
        lay.addStretch(1)
        return page

    def _recompute_cost(self, *_):
        if not hasattr(self, "_cost_out"):
            return
        from src.utils import pricing
        ai = self._config.get("ai") or {}
        stt = self._config.get("stt") or {}
        vm = ai.get("claude_vision_model") if ai.get("vision_provider") == "claude" else ai.get("vision_model")
        cm = ai.get("claude_chat_model") if ai.get("chat_provider") == "claude" else ai.get("chat_model")
        sm = stt.get("model") if stt.get("enabled") else ""
        interval = float((self._config.get("capture") or {}).get("interval_seconds", 15) or 15)
        sp = self._cost_spins
        est = pricing.estimate_costs(
            vision_model=vm or "", chat_model=cm or "", stt_model=sm or "",
            capture_interval_s=interval, hours=sp["hours"].value(),
            speech_min_per_hour=sp["speech_min_per_hour"].value(),
            screen_change_pct=sp["screen_change_pct"].value(),
            chat_calls_per_hour=sp["chat_calls_per_hour"].value(),
        )
        f = pricing.format_usd

        def row(name, block, usage):
            known = block["known"]
            per_h = f(block["per_hour"]) if known else f"<span style='color:{T.WARN}'>?</span>"
            per_s = f(block["per_session"]) if known else f"<span style='color:{T.WARN}'>?</span>"
            return (f"<tr><td style='padding:6px 12px 6px 0;color:{T.TEXT}'>{name}</td>"
                    f"<td style='padding:6px 12px;color:{T.MUTED}'>{block['model'] or '—'}</td>"
                    f"<td style='padding:6px 12px;color:{T.MUTED}'>{usage}</td>"
                    f"<td align='right' style='padding:6px 12px;color:{T.TEXT}'>{per_h}</td>"
                    f"<td align='right' style='padding:6px 0 6px 12px;color:{T.TEXT_2}'>{per_s}</td></tr>")

        v, c, s = est["vision"], est["chat"], est["stt"]
        rows = (row("화면 분석", v, f"{v['calls_per_hour']:.0f}회/시간")
                + row("채팅 생성", c, f"{c['calls_per_hour']:.0f}회/시간")
                + row("음성 인식", s, f"{s['min_per_hour']:.0f}분/시간"))
        head = (f"<tr style='color:{T.FAINT};font-size:11px'><td style='padding:0 12px 6px 0'>항목</td>"
                "<td style='padding:0 12px 6px'>모델</td><td style='padding:0 12px 6px'>사용량</td>"
                "<td align='right' style='padding:0 12px 6px'>시간당</td>"
                f"<td align='right' style='padding:0 0 6px 12px'>세션({est['hours']:.1f}시간)</td></tr>")
        total = (f"<tr><td colspan='3' style='padding:10px 12px 0 0;font-weight:700'>합계</td>"
                 f"<td align='right' style='padding:10px 12px 0;color:{T.ACCENT_HI};font-weight:700'>"
                 f"{f(est['total_per_hour'])}</td>"
                 f"<td align='right' style='padding:10px 0 0 12px;color:{T.ACCENT_HI};font-weight:700'>"
                 f"{f(est['total_session'])}</td></tr>")
        warn = ""
        if est["unknown"]:
            warn = (f"<p style='color:{T.WARN};font-size:12px'>공개 단가가 없는 모델: "
                    f"{', '.join(sorted(set(est['unknown'])))} — 합계에서 빠졌어요</p>")
        self._cost_out.setText(f"<table width='100%' cellspacing='0'>{head}{rows}{total}</table>{warn}")
        self._nav_buttons[5].set_meta(f(est["total_per_hour"]) + "/h")
