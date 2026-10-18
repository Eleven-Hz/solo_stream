"""오버레이 + 설정 창 스모크 테스트 (실제 화면 캡처 → PNG).

가짜 게임 화면 창 위에 오버레이를 띄우고, 화면을 실제로 캡처해 각 상태를 PNG 로 남긴다.
(위젯 render() 는 그래픽 효과·반투명 창 합성을 실제와 다르게 그려서 화면 캡처를 쓴다.)
위젯 생성·페이지 전환·투명도 단계·메시지 종류(봇/내 말/시스템)를 점검하고, 마지막에
'화면 분석에서 채팅창 숨기기'가 켜지면 캡처에서 오버레이가 사라지는지도 확인한다.
API 호출은 하지 않는다. 몇 초간 화면에 창이 떴다 사라진다.

사용:
  .\\venv\\Scripts\\python.exe tests\\ui\\smoke_settings_tabs.py [출력폴더]
  (기본 출력: tests/ui/output/)
"""
from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from src.utils.config_loader import load_config


def _game_backdrop(w: int, h: int) -> QPixmap:
    """게임 화면 대신 쓰는 밝은/어두운 혼합 배경(가독성 확인용)."""
    pm = QPixmap(w, h)
    pm.fill(QColor("#3B5A2E"))
    p = QPainter(pm)
    p.fillRect(0, 0, w, h // 3, QColor("#C9D6A3"))  # 밝은 영역(하늘/눈밭 같은 곳)
    p.setPen(QColor(0, 0, 0, 40))
    for x in range(0, w, 32):
        p.drawLine(x, 0, x, h)
    for y in range(0, h, 32):
        p.drawLine(0, y, w, y)
    p.end()
    return pm


class _Backdrop(QWidget):
    def __init__(self, w, h):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.resize(w, h)
        self._pm = _game_backdrop(w, h)

    def paintEvent(self, _):
        QPainter(self).drawPixmap(0, 0, self._pm)


def _grab(rect, path: Path):
    QTest.qWait(250)
    QApplication.primaryScreen().grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height()).save(str(path))


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "ui" / "output"
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)

    from src.ui import theme as T
    from src.ui.overlay import ChatOverlay
    app.setFont(T.font(13))

    config = load_config()
    ui_q: multiprocessing.Queue = multiprocessing.Queue()
    set_q: multiprocessing.Queue = multiprocessing.Queue()

    bd = _Backdrop(420, 680)
    bd.move(60, 60)
    bd.show()
    ov = ChatOverlay(config, ui_q, set_q, onboarding=False, hotkeys_ok=True)
    ov.resize(380, 640)
    ov.move(80, 80)
    ov.set_capture_exclusion(False)
    ov.show()
    area = QRect(60, 60, 420, 680)
    QTest.qWait(200)
    ov.set_show_stt(True)
    msgs = [
        ("chat", "신입생", "이거 레벨 63 몬스터야?"),
        ("chat", "감성러", "비 오는 소리 좋다…"),
        ("chat", "공격수", "여기 퍼즐 또 하겠네"),
        ("chat", "겜린이", "와 몬스터 개많아..!"),
        ("stt", "", "크로스코드 게임을 시작해 봅시다."),
        ("chat", "고인물킹", "고슴도치는 원거리로 빼고 치세요"),
        ("chat", "트롤마스터", "ㅋㅋㅋ 시작부터 한 대 맞음 <태그> & 특수문자"),
        ("chat", "분석충", "이단 차지 쿨 돌았어요"),
        ("sys", "시스템", "⚠ AI 서버가 혼잡합니다(503 등). 잠시 동안 채팅이 끊기거나 누락될 수 있습니다."),
        ("chat", "도네요정", "대박!! 한 방에 정리"),
        ("chat", "스토리덕", "여기 배경 진짜 예쁘다"),
        ("stt", "", "항상 이동할 때는 대시로 빠르게."),
        ("chat", "스피드러너", "대시 캔슬 쓰면 더 빨라요"),
        ("chat", "PD님", "다음엔 어디로 가볼까? 이 문장은 일부러 길게 써서 줄바꿈이 잘 되는지 확인합니다"),
    ]
    for kind, name, text in msgs:
        if kind == "stt":
            ov._handle_message({"stt_transcript": True, "text": text})
        else:
            ov._handle_message({"name": name, "message": text})
    ov._handle_message({"system": True, "type": "profile_loaded", "name": "CrossCode (크로스코드)",
                        "warning": None, "status": "ok", "detail": ""})
    QTest.qWait(200)
    assert ov._list.count() == len(msgs), f"메시지 수 불일치: {ov._list.count()}"

    for t in (15, 50, 100):
        ov.set_transparency(t, save=False)
        _grab(area, out / f"overlay_t{t}.png")
    ov.set_chat_active(True)
    ov._hovered = False
    ov._update_chrome(animate=False)
    _grab(area, out / "overlay_t100_live_nohover.png")
    ov.set_transparency(15, save=False)
    # 캡처 제외: 켜면 화면 캡처에 오버레이가 찍히지 않아야 한다.
    ov.set_capture_exclusion(True)
    _grab(area, out / "overlay_capture_excluded.png")
    ov.set_capture_exclusion(False)

    sw = ov._settings
    sw.resize(960, 720)
    sw.move(520, 60)
    sw.show()
    from src.ui import winapi
    winapi.set_exclude_from_capture(int(sw.winId()), False)
    for key in ("ready", "overlay", "voice", "ai", "game", "usage"):
        sw.open_page(key, show=False)
        _grab(sw.frameGeometry(), out / f"settings_{key}.png")
    sw._shutdown()
    sw.hide()
    ov.hide()
    bd.hide()
    print(f"OK — PNG 저장: {out}")


if __name__ == "__main__":
    main()
