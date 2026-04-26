"""
이미지 분석 모델 비교 테스트.

Gemini와 Claude 두 모델에 동일한 게임 스크린샷을 전송하고
결과를 나란히 출력합니다.

사용법:
  # 현재 게임 창을 캡처해서 비교
  python tests/test_vision_compare.py

  # 이미지 파일을 직접 지정
  python tests/test_vision_compare.py --image path/to/screenshot.jpg

  # 반복 횟수 지정
  python tests/test_vision_compare.py --rounds 3

환경 변수 (.env.local):
  GEMINI_API_KEY=...
  CLAUDE_API_KEY=...
"""
import argparse
import sys
import time
from pathlib import Path

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import load_config, get_api_key, get_claude_api_key
from src.ai.model_client import GeminiClient, ClaudeClient


def _capture_game_window(config: dict) -> bytes:
    """현재 게임 창 캡처."""
    from src.capture.screen import capture_screen
    target = config["capture"].get("target_window")
    resize_w = config["capture"].get("resize_width", 1024)
    return capture_screen(target_window=target, resize_width=resize_w)


def _load_image_file(path: str) -> bytes:
    """파일에서 JPEG bytes 로드. PNG 등은 JPEG으로 변환."""
    from PIL import Image
    import io
    img = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _divider(char: str = "─", width: int = 60) -> str:
    return char * width


def run_comparison(image_bytes: bytes, gemini_client: GeminiClient, claude_client: ClaudeClient | None) -> dict:
    """두 모델에 동일 이미지를 보내고 결과 반환."""
    results: dict = {}

    print(f"\n{'▶ Gemini':}")
    t0 = time.perf_counter()
    gemini_result = gemini_client.analyze_screenshot(image_bytes)
    gemini_elapsed = time.perf_counter() - t0
    results["gemini"] = {"text": gemini_result, "elapsed": gemini_elapsed}
    print(gemini_result)
    print(f"  ({gemini_elapsed:.2f}s)")

    if claude_client:
        print(f"\n{'▶ Claude':}")
        t0 = time.perf_counter()
        claude_result = claude_client.analyze_screenshot(image_bytes)
        claude_elapsed = time.perf_counter() - t0
        results["claude"] = {"text": claude_result, "elapsed": claude_elapsed}
        print(claude_result)
        print(f"  ({claude_elapsed:.2f}s)")
    else:
        print("\n[Claude 건너뜀] CLAUDE_API_KEY 미설정")
        results["claude"] = None

    return results


def main():
    parser = argparse.ArgumentParser(description="Gemini vs Claude 이미지 분석 비교")
    parser.add_argument("--image", type=str, default=None, help="이미지 파일 경로 (없으면 게임 창 캡처)")
    parser.add_argument("--rounds", type=int, default=1, help="반복 횟수 (기본: 1)")
    parser.add_argument("--interval", type=float, default=3.0, help="라운드 간 대기 시간 (초, 기본: 3)")
    args = parser.parse_args()

    config = load_config()
    gemini_key = get_api_key(config)
    claude_key = get_claude_api_key(config)

    gemini_client = GeminiClient(api_key=gemini_key, config=config)
    claude_client = ClaudeClient(api_key=claude_key, config=config) if claude_key else None

    print(_divider("═"))
    print("  이미지 분석 모델 비교 테스트")
    print(f"  Gemini 모델  : {config['ai']['vision_model']}")
    print(f"  Claude 모델  : {config['ai'].get('claude_vision_model', 'claude-opus-4-7')}")
    print(f"  Claude 키    : {'있음' if claude_key else '없음 (CLAUDE_API_KEY 미설정)'}")
    print(f"  라운드       : {args.rounds}회")
    if args.image:
        print(f"  이미지 파일  : {args.image}")
    else:
        print(f"  캡처 대상    : {config['capture'].get('target_window', '전체 화면')}")
    print(_divider("═"))

    all_results: list[dict] = []

    for round_num in range(1, args.rounds + 1):
        print(f"\n{'▌ 라운드 ' + str(round_num):} / {args.rounds}")
        print(_divider())

        if args.image:
            image_bytes = _load_image_file(args.image)
        else:
            print("게임 창 캡처 중...")
            image_bytes = _capture_game_window(config)
            print(f"캡처 완료 ({len(image_bytes):,} bytes)")

        results = run_comparison(image_bytes, gemini_client, claude_client)
        results["round"] = round_num
        all_results.append(results)

        if round_num < args.rounds:
            print(f"\n{args.interval:.0f}초 후 다음 라운드...")
            time.sleep(args.interval)

    # ── 요약 ──────────────────────────────────────────────────────────────
    if args.rounds > 1:
        print(f"\n{_divider('═')}")
        print("  요약")
        print(_divider("═"))

        gemini_times = [r["gemini"]["elapsed"] for r in all_results]
        avg_gemini = sum(gemini_times) / len(gemini_times)
        print(f"  Gemini  평균 응답시간: {avg_gemini:.2f}s")

        if claude_client:
            claude_times = [r["claude"]["elapsed"] for r in all_results if r["claude"]]
            if claude_times:
                avg_claude = sum(claude_times) / len(claude_times)
                print(f"  Claude  평균 응답시간: {avg_claude:.2f}s")

        print(_divider("═"))


if __name__ == "__main__":
    main()
