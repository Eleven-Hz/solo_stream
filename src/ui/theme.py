"""
UI 공용 테마 — 색 토큰, 폰트, 아이콘, 재사용 위젯.

오버레이(overlay.py)와 설정 창(settings.py)이 같은 톤을 쓰도록 색·위젯을 여기 모은다.
아이콘은 이모지 대신 QPainter 로 직접 그린다(폰트에 따라 이모지 폭/색이 달라지는 문제 회피).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# ── 색 토큰 ──────────────────────────────────────────────────────────────────
BG = "#0F1013"          # 설정 창 바탕
SURFACE = "#17181D"     # 카드
SURFACE_2 = "#1E2027"   # 버튼
INPUT = "#111216"       # 입력칸
LINE = "#262830"        # 카드 테두리
LINE_2 = "#31343D"      # 입력 테두리
DIVIDER = "#23252C"     # 카드 안 구분선
TEXT = "#E9EAEE"
TEXT_2 = "#C9CDD6"
MUTED = "#8B909C"
FAINT = "#6E7380"
ACCENT = "#5EE6B0"
ACCENT_HI = "#9AF2CF"
ACCENT_FG = "#0B1F17"
LIVE = "#FF5A5F"
WARN = "#F5B04C"
ERROR = "#FF6B6B"

FONT_FAMILY = "Malgun Gothic"
MONO_FAMILY = "Consolas"

# 봇 닉네임 색 — 어두운/투명 배경 위에서 모두 읽히도록 명도를 맞춘 팔레트.
NAME_COLORS = [
    "#8EC9FF", "#B7E08A", "#7FE3D0", "#FFB36B", "#C9A7FF",
    "#FF8E8E", "#FF9BD2", "#FFD66B", "#9FB4FF", "#7DDB9A",
]


def name_color(name: str) -> str:
    return NAME_COLORS[sum(ord(c) for c in name) % len(NAME_COLORS)]


def rgba(hex_color: str, alpha: float) -> str:
    """'#RRGGBB' + 0~1 알파 → QSS rgba() 문자열."""
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{int(max(0.0, min(1.0, alpha)) * 255)})"


def font(size_px: int = 13, weight: int = 400, mono: bool = False) -> QFont:
    f = QFont(MONO_FAMILY if mono else FONT_FAMILY)
    f.setPixelSize(size_px)
    f.setWeight(QFont.Weight(weight))
    return f


# ── 아이콘 ───────────────────────────────────────────────────────────────────

def _draw_icon(p: QPainter, name: str, s: float):
    """s×s 영역에 선 아이콘을 그린다(16 기준 좌표를 s 로 스케일)."""
    k = s / 16.0
    pt = lambda x, y: QPointF(x * k, y * k)  # noqa: E731
    path = QPainterPath()
    if name == "play":
        path.moveTo(pt(4.5, 2.5)); path.lineTo(pt(13, 8)); path.lineTo(pt(4.5, 13.5)); path.closeSubpath()
        p.fillPath(path, p.pen().color())
        return
    if name == "pause":
        p.drawLine(pt(5.5, 3), pt(5.5, 13)); p.drawLine(pt(10.5, 3), pt(10.5, 13))
    elif name == "close":
        p.drawLine(pt(4, 4), pt(12, 12)); p.drawLine(pt(12, 4), pt(4, 12))
    elif name == "gear":
        p.drawEllipse(QRectF(5.6 * k, 5.6 * k, 4.8 * k, 4.8 * k))
        for a, b in [((8, 1.5), (8, 3.5)), ((8, 12.5), (8, 14.5)), ((1.5, 8), (3.5, 8)),
                     ((12.5, 8), (14.5, 8)), ((3.4, 3.4), (4.8, 4.8)), ((11.2, 11.2), (12.6, 12.6)),
                     ((3.4, 12.6), (4.8, 11.2)), ((11.2, 4.8), (12.6, 3.4))]:
            p.drawLine(pt(*a), pt(*b))
    elif name == "cursor":
        path.moveTo(pt(3, 2)); path.lineTo(pt(7.5, 13)); path.lineTo(pt(9.1, 8.6)); path.lineTo(pt(13.5, 7))
        path.closeSubpath(); p.drawPath(path)
    elif name == "check":
        path.moveTo(pt(3, 8.5)); path.lineTo(pt(6.2, 11.5)); path.lineTo(pt(13, 4.5)); p.drawPath(path)
    elif name == "chevron_down":
        path.moveTo(pt(3.5, 6)); path.lineTo(pt(8, 10.5)); path.lineTo(pt(12.5, 6)); p.drawPath(path)
    elif name == "chevron_right":
        path.moveTo(pt(6, 3.5)); path.lineTo(pt(10.5, 8)); path.lineTo(pt(6, 12.5)); p.drawPath(path)
    elif name == "refresh":
        p.drawArc(QRectF(3 * k, 3 * k, 10 * k, 10 * k), 30 * 16, 300 * 16)
        path.moveTo(pt(13, 2.5)); path.lineTo(pt(13, 6)); path.lineTo(pt(9.5, 6)); p.drawPath(path)
    elif name == "warn":
        path.moveTo(pt(8, 2)); path.lineTo(pt(14.5, 13.5)); path.lineTo(pt(1.5, 13.5)); path.closeSubpath()
        p.drawPath(path); p.drawLine(pt(8, 6.5), pt(8, 9.5)); p.drawPoint(pt(8, 11.6))
    elif name == "dot":
        p.setBrush(p.pen().color()); p.drawEllipse(QRectF(5 * k, 5 * k, 6 * k, 6 * k))


def icon(name: str, color: str = TEXT_2, size: int = 16, stroke: float = 1.6) -> QIcon:
    """선 아이콘 QIcon. 고해상도 화면을 위해 2배로 그린다."""
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), stroke * scale)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    _draw_icon(p, name, size * scale)
    p.end()
    pm.setDevicePixelRatio(scale)
    return QIcon(pm)


_ASSET_DIR: Path | None = None


def asset_path(name: str, color: str = MUTED, size: int = 10) -> str:
    """QSS 의 url() 에 쓸 아이콘 PNG 경로(임시 폴더에 1회 생성)."""
    global _ASSET_DIR
    if _ASSET_DIR is None:
        _ASSET_DIR = Path(tempfile.gettempdir()) / "solo_stream_ui"
        _ASSET_DIR.mkdir(parents=True, exist_ok=True)
    path = _ASSET_DIR / f"{name}_{color.strip('#')}_{size}.png"
    if not path.exists():
        icon(name, color, size, stroke=2.0).pixmap(QSize(size, size), 2.0).save(str(path))
    return path.as_posix()


# ── 공용 스타일시트 (설정 창 루트에 한 번 적용) ─────────────────────────────

def settings_qss() -> str:
    chev = asset_path("chevron_down", MUTED, 10)
    return f"""
QWidget {{ color: {TEXT}; font-family: '{FONT_FAMILY}'; font-size: 13px; }}
QLabel {{ background: transparent; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#divider {{ background: {DIVIDER}; border: none; max-height: 1px; min-height: 1px; }}
QLabel#h1 {{ font-size: 20px; font-weight: 700; }}
QLabel#desc {{ color: {MUTED}; font-size: 13px; }}
QLabel#ctitle {{ color: {MUTED}; font-size: 12px; font-weight: 600; }}
QLabel#lbl {{ color: {TEXT}; font-size: 14px; font-weight: 500; }}
QLabel#sub {{ color: {MUTED}; font-size: 12px; }}
QLabel#mono {{ font-family: '{MONO_FAMILY}'; color: {TEXT_2}; }}
QPushButton#btn {{ height: 32px; padding: 0 12px; border-radius: 7px; border: 1px solid {LINE_2};
    background: {SURFACE_2}; color: #D7DAE1; font-size: 12px; font-weight: 500; }}
QPushButton#btn:hover {{ background: #262931; }}
QPushButton#btn:disabled {{ color: {FAINT}; background: {SURFACE}; }}
QPushButton#primary {{ height: 44px; padding: 0 22px; border-radius: 9px; border: none;
    background: {ACCENT}; color: {ACCENT_FG}; font-size: 14px; font-weight: 700; }}
QPushButton#primary:hover {{ background: {ACCENT_HI}; }}
QPushButton#danger {{ height: 44px; padding: 0 22px; border-radius: 9px; border: none;
    background: {LIVE}; color: #2A0A0B; font-size: 14px; font-weight: 700; }}
QPushButton#link {{ border: none; background: transparent; color: {TEXT_2}; font-size: 13px;
    font-weight: 500; text-align: left; padding: 0; }}
QPushButton#link:hover {{ color: #fff; }}
QComboBox {{ min-height: 34px; padding: 0 30px 0 12px; border-radius: 7px; border: 1px solid {LINE_2};
    background: {INPUT}; color: {TEXT}; font-size: 13px; }}
QComboBox:hover {{ border-color: #444855; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({chev}); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: #1A1C22; color: {TEXT}; border: 1px solid {LINE_2};
    selection-background-color: #2A2D36; outline: none; padding: 4px; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{ min-height: 32px; padding: 0 10px; border-radius: 7px;
    border: 1px solid {LINE_2}; background: {INPUT}; color: {TEXT}; font-size: 13px;
    selection-background-color: #2E5C4B; }}
QPlainTextEdit {{ padding: 8px 10px; }}
QSpinBox, QDoubleSpinBox {{ font-family: '{MONO_FAMILY}'; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 4px; background: #2A2D35; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: #fff; }}
QScrollArea, QScrollArea > QWidget {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #33363F; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QToolTip {{ background: #1A1C22; color: {TEXT}; border: 1px solid {LINE_2}; padding: 4px 6px; }}
"""


# ── 재사용 위젯 ──────────────────────────────────────────────────────────────

class Switch(QAbstractButton):
    """iOS 스타일 on/off 스위치. toggled(bool) 시그널을 그대로 쓴다."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 22)

    def sizeHint(self):
        return QSize(38, 22)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        track = QColor(ACCENT if on else "#3A3D47")
        if not self.isEnabled():
            track.setAlpha(90)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 38, 22), 11, 11)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(19 if on else 3, 3, 16, 16))


class Segmented(QWidget):
    """[낮게|보통|적극적] 같은 단일 선택 버튼 묶음. changed(value) 시그널."""
    changed = pyqtSignal(str)

    def __init__(self, options: list[tuple[str, str]], value: str | None = None,
                 compact: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("seg")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget#seg {{ background: {rgba('#FFFFFF', 0.06) if compact else INPUT}; "
            f"border: {'none' if compact else '1px solid ' + LINE}; border-radius: 8px; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self._buttons: dict[str, QPushButton] = {}
        for val, label in options:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(28 if compact else 30)
            b.setStyleSheet(
                "QPushButton { border: none; border-radius: 6px; padding: 0 12px; "
                f"background: transparent; color: #A3A9B6; font-size: 12px; font-weight: 500; }}"
                f"QPushButton:checked {{ background: {rgba(ACCENT, 0.18)}; color: {ACCENT_HI}; }}"
                "QPushButton:hover:!checked { color: #E9EAEE; }"
            )
            b.clicked.connect(lambda _=False, v=val: self._pick(v, emit=True))
            lay.addWidget(b)
            self._buttons[val] = b
        self._value = None
        self._pick(value if value in self._buttons else options[0][0], emit=False)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str):
        if value in self._buttons:
            self._pick(value, emit=False)

    def _pick(self, value: str, emit: bool):
        for v, b in self._buttons.items():
            b.setChecked(v == value)
        changed = value != self._value
        self._value = value
        if emit and changed:
            self.changed.emit(value)


class LevelMeter(QWidget):
    """마이크 입력 레벨 막대(0~1). 떨어질 때는 천천히 줄어들게 감쇠한다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = 0.0
        self.setFixedHeight(6)
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_level(self, level: float):
        level = max(0.0, min(1.0, level))
        self._level = level if level > self._level else self._level * 0.82 + level * 0.18
        self.update()

    def reset(self):
        self._level = 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#24262D"))
        p.drawRoundedRect(QRectF(0, 0, self.width(), 6), 3, 3)
        if self._level > 0.005:
            p.setBrush(QColor(ACCENT))
            p.drawRoundedRect(QRectF(0, 0, max(6.0, self.width() * self._level), 6), 3, 3)


class StatusBadge(QLabel):
    """방송 준비 체크리스트의 원형 상태 표시(ok/warn/error/pending)."""

    _STYLES = {
        "ok": (ACCENT, "check"),
        "warn": (WARN, "warn"),
        "error": (ERROR, "warn"),
        "pending": (FAINT, "dot"),
    }

    def __init__(self, state: str = "pending", parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state(state)

    def set_state(self, state: str):
        color, ic = self._STYLES.get(state, self._STYLES["pending"])
        self.setStyleSheet(f"background: {rgba(color, 0.16)}; border-radius: 12px;")
        self.setPixmap(icon(ic, color, 12, stroke=2.2).pixmap(12, 12))


class Chip(QLabel):
    """작은 상태 칩(연결됨/없음 등)."""

    def __init__(self, text: str = "", tone: str = "neutral", parent=None):
        super().__init__(text, parent)
        self.setFixedHeight(24)
        self.set_tone(tone)

    def set_tone(self, tone: str, text: str | None = None):
        if text is not None:
            self.setText(text)
        bg, fg = {
            "ok": (rgba(ACCENT, 0.14), ACCENT_HI),
            "warn": (rgba(WARN, 0.16), "#F5C27A"),
            "error": (rgba(ERROR, 0.16), "#FF9B9B"),
        }.get(tone, ("#22242B", "#A3A9B6"))
        self.setStyleSheet(
            f"background: {bg}; color: {fg}; border-radius: 12px; padding: 0 9px; "
            "font-size: 11px; font-weight: 500;"
        )


def button(text: str, kind: str = "btn") -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def label(text: str, kind: str = "lbl", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName(kind)
    lbl.setWordWrap(wrap)
    return lbl


def combo(items: list[tuple[str, str]] | None = None, current: str | None = None,
          width: int | None = None) -> QComboBox:
    """(표시 텍스트, 값) 목록으로 콤보 생성. current 값이 목록에 없으면 추가해 유지한다."""
    c = QComboBox()
    c.setCursor(Qt.CursorShape.PointingHandCursor)
    if width:
        c.setFixedWidth(width)
    else:
        c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        c.setMinimumContentsLength(14)
    for text, val in items or []:
        c.addItem(text, userData=val)
    if current is not None:
        select_data(c, current)
    return c


def select_data(c: QComboBox, value, fallback_text: str | None = None):
    """userData 로 항목 선택. 없으면 fallback_text 로 항목을 추가해 선택."""
    for i in range(c.count()):
        if c.itemData(i) == value:
            c.setCurrentIndex(i)
            return
    if fallback_text is not None:
        c.addItem(fallback_text, userData=value)
        c.setCurrentIndex(c.count() - 1)


class Card(QFrame):
    """설정 카드. add_row() 로 구분선이 들어간 행을 쌓는다."""

    def __init__(self, title: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._rows = 0
        if title:
            t = label(title, "ctitle")
            t.setContentsMargins(16, 14, 16, 0)
            self._lay.addWidget(t)

    def add_row(self, content: QWidget | None = None, *, title: str | None = None,
                sub: str | None = None, right: QWidget | None = None,
                left: QWidget | None = None) -> QWidget:
        """한 행 추가. content 를 주면 그대로, 아니면 [left][title/sub] ... [right] 형태로 만든다."""
        if self._rows:
            d = QFrame()
            d.setObjectName("divider")
            self._lay.addWidget(d)
        self._rows += 1
        if content is None:
            content = QWidget()
            h = QHBoxLayout(content)
            h.setContentsMargins(16, 14, 16, 14)
            h.setSpacing(14)
            if left is not None:
                h.addWidget(left, 0, Qt.AlignmentFlag.AlignVCenter)
            text_box = QVBoxLayout()
            text_box.setSpacing(3)
            if title:
                text_box.addWidget(label(title, "lbl"))
            if sub:
                text_box.addWidget(label(sub, "sub", wrap=True))
            h.addLayout(text_box, 1)
            if right is not None:
                h.addWidget(right, 0, Qt.AlignmentFlag.AlignVCenter)
        self._lay.addWidget(content)
        return content

    def add_widget(self, w: QWidget):
        """구분선 없이 카드 안에 위젯을 그대로 붙인다(접힘 영역 등)."""
        self._lay.addWidget(w)


def row_box(vertical: bool = False, spacing: int = 12) -> tuple[QWidget, QHBoxLayout | QVBoxLayout]:
    """카드 행용 컨테이너(카드 안쪽 여백 16/14 적용)."""
    w = QWidget()
    lay = QVBoxLayout(w) if vertical else QHBoxLayout(w)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(spacing)
    return w, lay
