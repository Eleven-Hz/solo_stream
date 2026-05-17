"""
Vision 모델 비교 테스트 러너.
tests/vision/config.yaml 의 설정을 읽어 실행하고
결과를 UTF-8 파일로 저장한다.

실행:
  python tests/vision/run.py
  python tests/vision/run.py --config tests/vision/config.yaml
"""
import argparse
import io
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

import yaml

from src.utils.config_loader import load_config, get_api_key, get_claude_api_key
from src.ai.model_client import GeminiClient, ClaudeClient
from src.chat.game_profile_loader import load_profile, build_profile_context, build_vision_context
from tests.vision.db import init_db, save_session, save_round, save_result
from tests.vision.report import build_html


def _divider(char: str = "-", width: int = 60) -> str:
    return char * width


def _capture_game_window(app_config: dict) -> bytes:
    from src.capture.screen import capture_screen
    target = app_config["capture"].get("target_window")
    resize_w = app_config["capture"].get("resize_width", 1024)
    return capture_screen(target_window=target, resize_width=resize_w)


def _load_image_file(path: str) -> bytes:
    from PIL import Image
    img = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _extract_video_frames(video_path: str, video_cfg: dict) -> list[tuple[float, bytes]]:
    """영상에서 프레임을 추출해 (타임스탬프_초, jpeg_bytes) 리스트로 반환."""
    import cv2
    from PIL import Image

    interval = video_cfg.get("frame_interval", 5.0)
    start_sec = video_cfg.get("start", 0.0)
    end_sec = video_cfg.get("end", None)
    resize_w = video_cfg.get("resize_width", 1024)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"영상 파일을 열 수 없습니다: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps

    end_sec = min(end_sec, duration) if end_sec is not None else duration

    frames: list[tuple[float, bytes]] = []
    ts = start_sec
    while ts <= end_sec + 1e-6:
        cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000)
        ret, frame = cap.read()
        if not ret:
            break

        h, w = frame.shape[:2]
        if w > resize_w:
            scale = resize_w / w
            frame = cv2.resize(frame, (resize_w, int(h * scale)), interpolation=cv2.INTER_AREA)

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        frames.append((ts, buf.getvalue()))

        ts += interval

    cap.release()
    return frames


def _extract_game_name(raw: str) -> str:
    name = raw or ""
    name = re.sub(r"\s*[-]\s*Steam.*", "", name)
    name = re.sub(r"\bMin\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\bv\d[\d.]*[-\w]*", "", name, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", name).strip() or "Unknown Game"


def run(test_cfg: dict, app_config: dict, out: io.TextIOWrapper, html_path: Path | None = None):
    def w(line: str = ""):
        out.write(line + "\n")
        out.flush()

    gemini_key = get_api_key(app_config)
    claude_key = get_claude_api_key(app_config)

    # 모델 주입
    app_config["ai"]["vision_model"] = test_cfg["gemini"]["model"]
    app_config["ai"]["claude_vision_model"] = test_cfg["claude"]["model"]

    gemini_client = GeminiClient(api_key=gemini_key, config=app_config)
    claude_client = ClaudeClient(api_key=claude_key, config=app_config) if claude_key else None

    # 게임 프로필
    raw_game = test_cfg.get("game") or app_config["capture"].get("target_window", "")
    game_name = _extract_game_name(raw_game)
    w(f"게임 프로필 로드 중: {game_name} ...")
    game_profile = load_profile(game_name, ai_client=gemini_client, config=app_config)
    profile_context = build_vision_context(game_profile)

    rounds = test_cfg.get("rounds", 1)
    interval = test_cfg.get("interval", 3.0)
    image_path = test_cfg.get("image")
    video_cfg = test_cfg.get("video")  # video 섹션 (있으면 영상 모드)

    description = test_cfg.get("output", {}).get("description", "")
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 영상 모드: 프레임 미리 추출
    video_frames: list[tuple[float, bytes]] = []
    if video_cfg and video_cfg.get("file"):
        video_path = str(ROOT / video_cfg["file"])
        w(f"영상 파일 프레임 추출 중: {video_path}")
        video_frames = _extract_video_frames(video_path, video_cfg)
        rounds = len(video_frames)
        w(f"추출된 프레임: {rounds}개")

    # DB 세션 생성
    init_db()
    if video_frames:
        source = f"video:{video_cfg['file']}"
    elif image_path:
        source = f"image:{image_path}"
    else:
        source = "capture"
    session_id = save_session(
        started_at=started_at,
        description=description,
        game=game_profile.get("name", game_name),
        gemini_model=app_config["ai"]["vision_model"],
        claude_model=app_config["ai"]["claude_vision_model"],
        source=source,
    )

    w(_divider("="))
    w(f"  시작 시각    : {started_at}")
    if description:
        w(f"  설명         : {description}")
    w("  Vision 모델 비교 테스트")
    w(f"  Gemini 모델  : {app_config['ai']['vision_model']}")
    w(f"  Claude 모델  : {app_config['ai']['claude_vision_model']}")
    w(f"  Claude 키    : {'있음' if claude_key else '없음'}")
    w(f"  게임 프로필  : {game_profile.get('name', game_name)}")
    w(f"  라운드       : {rounds}회")
    if video_frames:
        w(f"  소스         : 영상 ({video_cfg['file']})")
    else:
        w(f"  이미지       : {image_path or '게임 창 캡처'}")
    w(_divider("="))

    all_results: list[dict] = []
    last_gemini_location = ""
    last_claude_location = ""

    for round_num in range(1, rounds + 1):
        w(f"\n[라운드 {round_num} / {rounds}]")
        w(_divider())

        if video_frames:
            ts, image_bytes = video_frames[round_num - 1]
            m, s = divmod(int(ts), 60)
            w(f"영상 프레임 로드: {ts:.1f}s ({m:02d}:{s:02d})  ({len(image_bytes):,} bytes)")
        elif image_path:
            image_bytes = _load_image_file(image_path)
            w(f"이미지 파일 로드: {image_path}")
        else:
            w("게임 창 캡처 중...")
            image_bytes = _capture_game_window(app_config)
            w(f"캡처 완료 ({len(image_bytes):,} bytes)")

        results: dict = {}

        # DB: 라운드 + 이미지 저장
        ts_sec = video_frames[round_num - 1][0] if video_frames else None
        round_id = save_round(session_id, round_num, image_bytes, ts_sec)

        w("\n[Gemini]")
        t0 = time.perf_counter()
        gemini_result = gemini_client.analyze_screenshot(
            image_bytes,
            game_profile_context=profile_context,
            previous_location=last_gemini_location,
        )
        gemini_elapsed = time.perf_counter() - t0
        results["gemini"] = {"text": gemini_result, "elapsed": gemini_elapsed}
        save_result(round_id, "gemini", gemini_result, gemini_elapsed)
        w(gemini_result)
        w(f"  ({gemini_elapsed:.2f}s)")
        for line in gemini_result.splitlines():
            if line.startswith("위치:"):
                loc = line[len("위치:"):].strip()
                if loc and loc != "알 수 없음":
                    last_gemini_location = loc
                break

        if claude_client:
            w("\n[Claude]")
            t0 = time.perf_counter()
            claude_result = claude_client.analyze_screenshot(
                image_bytes,
                game_profile_context=profile_context,
                previous_location=last_claude_location,
            )
            claude_elapsed = time.perf_counter() - t0
            results["claude"] = {"text": claude_result, "elapsed": claude_elapsed}
            save_result(round_id, "claude", claude_result, claude_elapsed)
            w(claude_result)
            w(f"  ({claude_elapsed:.2f}s)")
            for line in claude_result.splitlines():
                if line.startswith("위치:"):
                    loc = line[len("위치:"):].strip()
                    if loc and loc != "알 수 없음":
                        last_claude_location = loc
                    break
        else:
            w("\n[Claude 건너뜀] CLAUDE_API_KEY 미설정")
            results["claude"] = None

        results["round"] = round_num
        if video_frames:
            results["timestamp"] = video_frames[round_num - 1][0]
        all_results.append(results)

        if round_num < rounds and not video_frames:
            w(f"\n{interval:.0f}초 후 다음 라운드...")
            time.sleep(interval)

    # 요약
    if rounds > 1:
        w(f"\n{_divider('=')}")
        w("  요약")
        w(_divider("="))
        gemini_times = [r["gemini"]["elapsed"] for r in all_results]
        w(f"  Gemini  평균 응답시간: {sum(gemini_times)/len(gemini_times):.2f}s")
        if claude_client:
            claude_times = [r["claude"]["elapsed"] for r in all_results if r.get("claude")]
            if claude_times:
                w(f"  Claude  평균 응답시간: {sum(claude_times)/len(claude_times):.2f}s")
        w(_divider("="))

    # HTML 리포트 생성
    if html_path:
        html_content = build_html([session_id])
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_content, encoding="utf-8")
        w(f"\n리포트: {html_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=str(ROOT / "tests" / "vision" / "config.yaml"),
        help="테스트 설정 파일 경로",
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        test_cfg = yaml.safe_load(f)

    app_config = load_config()

    out_cfg = test_cfg.get("output", {})
    output_path = ROOT / out_cfg.get("file", "tests/vision/result.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoding = out_cfg.get("encoding", "utf-8")
    append = out_cfg.get("append", False)
    mode = "a" if append else "w"

    txt_file = out_cfg.get("file", "tests/vision/result.txt")
    html_path = ROOT / Path(txt_file).with_suffix(".html")

    print(f"결과 저장: {output_path}  ({'추가' if append else '덮어쓰기'})")

    with open(output_path, mode, encoding=encoding) as out:
        if append and output_path.stat().st_size > 0:
            out.write("\n" + "=" * 60 + "\n")
        run(test_cfg, app_config, out, html_path=html_path)

    print("완료.")


if __name__ == "__main__":
    main()
