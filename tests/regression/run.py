"""2026-11 테스터 피드백 회귀 시험 (API 호출 없음).

실행: venv\\Scripts\\python tests/regression/run.py
"""
import io
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import numpy as np
import yaml
from PIL import Image, ImageDraw

from src.ai.model_client import create_profile_client, selected_model
from src.capture.change_detect import has_significant_change
from src.chat import game_profile_loader as gp
from src.utils import paths
from src.utils.api_errors import describe_api_error, is_fatal_api_error
from src.utils.config_loader import _migrate_models

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


def _jpg(im) -> bytes:
    b = io.BytesIO()
    im.save(b, "JPEG", quality=85)
    return b.getvalue()


def _screen(draw) -> bytes:
    im = Image.new("RGB", (1024, 576), (40, 50, 70))
    draw(ImageDraw.Draw(im))
    return _jpg(im)


def test_install_files():
    req = (ROOT / "requirements.txt").read_bytes()
    try:
        req.decode("cp949")
        ok = True
    except UnicodeDecodeError:
        ok = False
    check("requirements.txt 를 CP949 로 읽을 수 있다", ok)
    spec = (ROOT / "solo_stream.spec").read_text(encoding="utf-8")
    check("번들 프로필은 crosscode 만", '"game_profiles/crosscode.yaml"' in spec and "game_profiles/*.yaml\"" not in spec)


def test_defaults():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    check("기본 화면 분석은 luna", selected_model(cfg, "vision") == ("openai", "gpt-6-luna"))
    check("기본 채팅은 luna", selected_model(cfg, "chat") == ("openai", "gpt-6-luna"))
    check("기본 프로필 생성은 luna", cfg["ai"]["profile_model"] == "gpt-6-luna")
    check("기본 캡처 창은 비어 있음", not cfg["capture"]["target_window"])
    check("내 말 표시 기본 켬", cfg["ui"]["show_stt_history"] is True)
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    active = [k for k, v in cfg["ai"].items() if isinstance(v, str) and v.startswith("gemini-2")]
    check("기본값에 2.5 모델 없음", not active and not str(cfg["stt"]["fallback_model"]).startswith("gemini-2"), text[:0])

    user = {"ai": {"vision_provider": "gemini", "vision_model": "gemini-2.5-flash", "profile_model": "gemini-2.5-flash"},
            "stt": {"provider": "gemini", "model": "gemini-2.5-flash"}}
    changed = _migrate_models(user, cfg["model_migrations"])
    check("저장된 2.5 선택이 교체됨", len(changed) == 3 and user["stt"]["model"] == "gemini-3.8-flash", str(changed))


def test_profile_client():
    cfg = {"ai": {"profile_model": "gpt-6-luna", "vision_model": "g", "chat_model": "g"}}
    check("키 둘 다 없으면 None", create_profile_client(cfg, None, None) is None)
    c, m = create_profile_client(cfg, "x", None)
    check("OpenAI 키 없으면 Gemini 로", type(c).__name__ == "GeminiClient" and m == "gemini-3.8-flash")
    c, m = create_profile_client(cfg, "x", "y")
    check("둘 다 있으면 고른 luna", type(c).__name__ == "OpenAIClient" and m == "gpt-6-luna")


def test_change_detect():
    def dialog(a, b):
        def d(dr):
            dr.rectangle((60, 400, 964, 550), fill=(15, 15, 25), outline=(200, 200, 200))
            dr.text((80, 415), a, fill=(240, 240, 240), font_size=20)
            dr.text((80, 445), b, fill=(240, 240, 240), font_size=20)
        return _screen(d)

    def tile(x):
        return _screen(lambda dr: dr.rectangle((x, 150, x + 60, 210), fill=(220, 190, 60)))

    check("대사가 바뀌면 변화", has_significant_change(dialog("Hello traveler", "Have you seen him?"),
                                                 dialog("The bridge collapsed", "Ask at the guild.")))
    check("퍼즐 칸이 움직이면 변화", has_significant_change(tile(300), tile(370)))
    same = dialog("same", "screen")
    check("같은 화면은 변화 아님", not has_significant_change(same, dialog("same", "screen")))


class _FakeClient:
    def __init__(self, fail: bool, fail_only: str = ""):
        self.fail = fail
        self.fail_only = fail_only

    def web_search_text(self, prompt, model):
        if self.fail_only and self.fail_only in prompt:
            raise RuntimeError("Request timed out.")
        if self.fail:
            raise RuntimeError("404 NOT_FOUND model no longer available")
        return '{"name": "새 이름", "description": "설명", "characters": [], "systems": ["a"], "locations": [], "enemies": [], "skills": [], "vision_focus": [], "chat_tips": [], "corrections": []}'


def test_profile_safety():
    with tempfile.TemporaryDirectory() as d:
        gp._PROFILE_DIR = Path(d)
        path = Path(d) / "mygame.yaml"
        path.write_text("name: 옛 프로필\ndescription: 소중한 내용\n", encoding="utf-8")
        r = gp.load_profile("mygame", ai_client=_FakeClient(True), auto_generate=True, model="m")
        kept = yaml.safe_load(path.read_text(encoding="utf-8"))
        check("생성 실패해도 기존 파일 유지", kept["description"] == "소중한 내용")
        check("생성 실패 시 기존 내용으로 진행하고 실패 표시", r["description"] == "소중한 내용" and r.get("_generation_aborted"))
        r = gp.load_profile("nofile", ai_client=_FakeClient(True), auto_generate=True, model="m")
        check("생성 실패하면 빈 파일을 만들지 않음", not (Path(d) / "nofile.yaml").exists() and r.get("_generation_aborted"))
        r = gp.load_profile("mygame", ai_client=_FakeClient(False, "전투 시스템"), auto_generate=True, model="m")
        kept = yaml.safe_load(path.read_text(encoding="utf-8"))
        check("일부 단계 실패면 기존 프로필 유지", kept["description"] == "소중한 내용" and r.get("_generation_aborted"))
        gp.load_profile("partial", ai_client=_FakeClient(False, "전투 시스템"), auto_generate=True, model="m")
        check("있던 프로필이 없으면 부분 결과 저장", (Path(d) / "partial.yaml").exists())
        gp.load_profile("mygame", ai_client=_FakeClient(False), auto_generate=True, model="m")
        new = yaml.safe_load(path.read_text(encoding="utf-8"))
        check("생성 성공하면 교체", new["name"] == "새 이름")
        check("임시 파일 없음", not list(Path(d).glob("*.tmp")))


def test_profile_validation():
    v = gp.validate_profile
    check("정상 프로필 통과", v({"name": "a", "description": "d", "locations": ["x"], "stt_terms": []}) == [])
    check("칸이 없어도 통과", v({"name": "a"}) == [])
    for label, bad in [("최상위 목록", ["a"]), ("문자열", "hello"), ("빈 값", None), ("제목 없음", {"description": "d"}),
                       ("제목 숫자", {"name": 123}), ("설명 숫자", {"name": "a", "description": 5}),
                       ("목록 자리 문자열", {"name": "a", "characters": "레아"}), ("목록 자리 숫자", {"name": "a", "locations": 3}),
                       ("항목이 사전", {"name": "a", "locations": [{"이름": "마을"}]}), ("항목이 숫자", {"name": "a", "skills": [1]}),
                       ("빈 항목", {"name": "a", "skills": ["  "]})]:
        check(f"거부: {label}", bool(v(bad)))
    for p in sorted((ROOT / "game_profiles").glob("*.yaml")):
        check(f"저장소 프로필 통과: {p.name}", v(yaml.safe_load(p.read_text(encoding="utf-8"))) == [],
              str(v(yaml.safe_load(p.read_text(encoding="utf-8")))))

    with tempfile.TemporaryDirectory() as d:
        gp._PROFILE_DIR = Path(d)
        for label, text in [("문법 오류", "name: a\nlocations: [1"), ("최상위 목록", "- a\n"), ("빈 파일", ""),
                            ("목록 자리 숫자", "name: a\nlocations: 3\n"), ("항목이 사전", "name: a\nlocations:\n- x: y\n"),
                            ("글자 깨짐", None)]:
            path = Path(d) / "broken.yaml"
            if text is None:
                path.write_bytes(b"name: \xff\xfe\x00")
            else:
                path.write_text(text, encoding="utf-8")
            before = path.read_bytes()
            try:
                p = gp.load_profile("broken")
                ok = bool(p.get("_invalid")) and bool(gp.build_profile_context(p)) and bool(gp.build_vision_context(p))
            except Exception as e:
                ok, p = False, str(e)
            check(f"깨진 파일에도 앱이 안 멈춤: {label}", ok, str(p)[:120])
            check(f"깨진 파일을 지우지 않음: {label}", path.read_bytes() == before)
        try:
            gp.save_profile("g", {"name": "a", "locations": 3})
            refused = False
        except ValueError:
            refused = True
        check("잘못된 프로필은 저장 거부", refused and not (Path(d) / "g.yaml").exists())


def test_api_errors():
    msg = "404 NOT_FOUND. This model is no longer available to new users."
    check("모델 없음은 치명 오류", is_fatal_api_error(msg) and "모델" in describe_api_error(msg))
    check("키 오류 안내", "키" in describe_api_error("401 Incorrect API key provided"))
    check("503 은 치명 오류 아님", not is_fatal_api_error("503 UNAVAILABLE"))


def test_stt_reports_failure():
    from src.stt.pipeline import STTPipeline

    class Failing:
        last_error = ""
        fail = True

        def transcribe(self, pcm, sr):
            self.last_error = "404 NOT_FOUND" if self.fail else ""
            return ("" if self.fail else "안녕하세요"), 0.0

    t = Failing()
    p = STTPipeline.__new__(STTPipeline)
    import queue
    p._transcriber, p._sample_rate, p._min_transcript_chars = t, 16000, 1
    p._utt_queue = queue.Queue()
    events, statuses = [], []
    p._on_event, p._on_status = events.append, lambda k, m: statuses.append((k, m))
    audio = np.zeros(1600, dtype=np.float32)
    p._utt_queue.put(audio)
    t2 = __import__("threading").Thread(target=p._transcribe_loop, daemon=True)
    t2.start()
    time.sleep(0.2)
    t.fail = False
    p._utt_queue.put(audio)
    p._utt_queue.put(None)
    t2.join(timeout=2)
    check("받아쓰기 실패가 상태로 보고됨", statuses and statuses[0] == ("transcribe", "404 NOT_FOUND"), str(statuses))
    check("회복도 보고되고 발화가 전달됨", statuses[-1] == ("transcribe", "") and len(events) == 1, str(statuses))


def test_legacy_files():
    with tempfile.TemporaryDirectory() as d:
        os.environ.pop("SOLO_STREAM_DEV", None)
        old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = d
        try:
            udir = paths.ensure_user_data()
            (udir / "config.yaml").write_text("old: 1", encoding="utf-8")
            (udir / "game_profiles" / "default.yaml").write_text("x: 1", encoding="utf-8")
            check("예전 파일 2개 발견", len(paths.legacy_user_files()) == 2)
            check("정리 개수", paths.archive_legacy_files() == 2)
            check("지우지 않고 .bak 으로", (udir / "config.yaml.bak").exists() and not paths.legacy_user_files())
        finally:
            if old is not None:
                os.environ["APPDATA"] = old


if __name__ == "__main__":
    for fn in (test_install_files, test_defaults, test_profile_client, test_change_detect,
               test_profile_safety, test_profile_validation, test_api_errors, test_stt_reports_failure, test_legacy_files):
        fn()
    failed = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(f"{'통과' if ok else '실패'}  {name}" + (f"  — {detail}" if detail and not ok else ""))
    print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
    sys.exit(1 if failed else 0)
