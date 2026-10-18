r"""README 스크린샷 생성 — 실제 위젯을 그려(QWidget.grab) JPG 로 저장한다.

화면 캡처 대신 위젯 렌더를 써서 모니터 크기·원격 화면에 잘리지 않는다.
실제 키·사용자 데이터를 건드리지 않도록 임시 데이터 폴더(가짜 키 + crosscode 프로필)를 만들어 쓴다.
게임 화면은 샘플 영상(tests/vision/video/crosscode_video_01.mkv) 88초 프레임, 채팅은
그 장면에서 gpt-6-luna 가 실제로 만든 채팅(2026-10-15 리플레이)이다. 사용량 탭 수치는 예시.

실행 (프로젝트 루트):  venv\Scripts\python.exe tests\ui\readme_shots.py [출력폴더]
  기본 출력: tests/ui/output/readme/  → 확인 후 docs/images/ 로 복사
"""
import multiprocessing, os, shutil, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
# 임시 데이터 폴더 — src import 전에 APPDATA 를 바꿔야 키(.env.local)도 여기서 읽힌다.
_tmp = Path(tempfile.mkdtemp(prefix="solo_readme_"))
(_tmp / "SoloStream" / "game_profiles").mkdir(parents=True)
(_tmp / "SoloStream" / ".env.local").write_text(
    "GEMINI_API_KEY=dummy\nOPENAI_API_KEY=dummy\nCLAUDE_API_KEY=dummy\n", encoding="utf-8")
shutil.copy(ROOT / "game_profiles" / "crosscode.yaml", _tmp / "SoloStream" / "game_profiles")
os.environ["APPDATA"] = str(_tmp)
os.environ.pop("SOLO_STREAM_DEV", None)
for _k in ("GEMINI_API_KEY", "GEMINI_API", "OPENAI_API_KEY", "CLAUDE_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ[_k] = ""

import cv2
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "ui" / "output" / "readme"
out.mkdir(parents=True, exist_ok=True)
from src.utils import paths
assert "SoloStream" in str(paths.user_data_dir()) and "Roaming" not in str(paths.user_data_dir()), paths.user_data_dir()
from src.utils.config_loader import load_config
app = QApplication(sys.argv)
from src.ui import theme as T
app.setFont(T.font(13))
import src.ui.settings as S
S.list_windows = lambda excludes=None: ["CrossCode Min v1.4.2-4", "메모장", "Spotify"]
from src.ui.overlay import ChatOverlay

cfg = load_config()
cfg["capture"]["target_window"] = "CrossCode Min v1.4.2-4"

# ── 1) 게임 위 오버레이 (샘플 영상 90초 장면 + 그 장면에서 gpt-6-luna 가 실제로 만든 채팅) ──
cap = cv2.VideoCapture("tests/vision/video/crosscode_video_01.mkv")
cap.set(cv2.CAP_PROP_POS_MSEC, 88000)
ok, frame = cap.read(); assert ok
frame = cv2.resize(frame, (1920, 1080))
rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
game = QPixmap.fromImage(QImage(rgb.data, 1920, 1080, 3 * 1920, QImage.Format.Format_RGB888).copy())

ov = ChatOverlay(cfg, multiprocessing.Queue(), multiprocessing.Queue(), onboarding=False, hotkeys_ok=True)
ov.resize(400, 700); ov.move(0, 0); ov.show(); QTest.qWait(200)
ov.set_show_stt(True)
msgs = [
    ("chat", "공포팬", "고슴도치 쓰러졌네 ㅠㅠ"),
    ("chat", "스피드러너", "빠른 전투가 맛이지"),
    ("chat", "신입생", "픽셀 그래픽 진짜 예쁘네요"),
    ("chat", "밈창고", "B랭크 효율 굿"),
    ("stt", "", "이미 제 레벨이 너무 높기 때문에 모든 적을 죽여도 다 1씩밖에 경험치가…"),
    ("chat", "겜린이", "렙 높으면 경험치 1이야? 😮"),
    ("chat", "고인물킹", "과레벨이면 잡몹 경험치 거의 없죠."),
    ("chat", "도네요정", "파동 공격 멋져❤️"),
    ("chat", "스토리덕", "레아 표정 연기 최고야"),
    ("chat", "공익봇", "B랭크 유지 잘하고 계시네요."),
    ("chat", "꿀잼충", "다음 맵 가즈아ㅋㅋㅋ"),
]
for kind, name, text in msgs:
    if kind == "stt":
        ov._handle_message({"stt_transcript": True, "text": text})
    else:
        ov._handle_message({"name": name, "message": text})
ov._handle_message({"system": True, "type": "profile_loaded", "name": "CrossCode (크로스코드)",
                    "warning": None, "status": "ok", "detail": ""})
ov.set_chat_active(True)
ov.set_transparency(15, save=False)
ov._hovered = True; ov._update_chrome(animate=False); QTest.qWait(300)
ovpm = ov.grab()
hero = game.copy()
p = QPainter(hero); x, y = 1920 - ovpm.width() - 40, 1080 - ovpm.height() - 60
p.drawPixmap(x, y, ovpm); p.end()
hero.save(str(out / "0_game_with_chat.jpg"), "JPG", 88)
zoom = hero.copy(QRect(x - 20, y - 20, ovpm.width() + 40, ovpm.height() + 40))
zoom.save(str(out / "1_chat_overlay.jpg"), "JPG", 90)
# 글자만 모드(투명도 100)
ov.set_transparency(100, save=False); ov._hovered = False; ov._update_chrome(animate=False); QTest.qWait(300)
pm2 = game.copy(QRect(x - 20, y - 20, ovpm.width() + 40, ovpm.height() + 40))
p = QPainter(pm2); p.drawPixmap(20, 20, ov.grab()); p.end()
pm2.save(str(out / "1b_chat_text_only.jpg"), "JPG", 90)
ov.set_transparency(15, save=False)

# ── 2) 설정 창 ──
sw = ov._settings
ov.set_chat_active(False)
sw.resize(960, 760); sw.show()
sw._on_capture(cv2.imencode(".jpg", cv2.resize(frame, (640, 360)))[1].tobytes(), "CrossCode Min v1.4.2-4")
sw._refresh_windows()
sw.set_profile_name("CrossCode (크로스코드)", status="ok")
sw._mic_peak = 0.4
sw._refresh_ready()
now = time.time()
def row(kind, model, calls, i, o, a, cost):
    return dict(kind=kind, model=model, calls=calls, in_tokens=i, out_tokens=o, audio_sec=a, cost=cost, priced=True)
sw.set_usage("ai", {"started": now - 42 * 60, "rows": [
    row("vision", "gemini-2.5-flash", 131, 131 * 1650, 131 * 260, 0, 131 * 1650 * 0.3e-6 + 131 * 260 * 2.5e-6),
    row("chat", "gpt-6-luna", 131, 131 * 5800, 131 * 110, 0, 131 * 5800 * 0.1e-6 + 131 * 110 * 0.5e-6)]})
sw.set_usage("stt", {"started": now - 42 * 60, "rows": [
    row("stt", "gpt-transcribe", 97, 0, 0, 640.0, 640 / 60 * 0.0045)]})
for key, name in (("ready", "2_settings_ready"), ("ai", "3_settings_ai_model"), ("voice", "4_settings_voice"),
                  ("game", "5_settings_game_profile"), ("usage", "6_settings_usage")):
    sw.open_page(key, show=False)
    sw._mic_peak = 0.4; sw._refresh_ready()
    QTest.qWait(400)
    sw.grab().save(str(out / f"{name}.jpg"), "JPG", 92)
sw._shutdown(); sw.hide(); ov.hide()
shutil.rmtree(_tmp, ignore_errors=True)
print("OK", out)
