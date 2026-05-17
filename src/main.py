"""
Solo Stream — 메인 진입점.
세 개의 프로세스를 시작하고 Queue로 연결한다.

  Capture Process → (image_queue) → AI Process → (ui_queue) → UI Process

UI Process에서 설정 변경(창 선택, 게임 프로필)이 발생하면
settings_queue를 통해 AI/Capture Process에 전달한다.
"""
import logging
import multiprocessing
import multiprocessing.queues
import random
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import load_config, get_api_key, get_claude_api_key
from src.utils.logger import setup_logger

logger = setup_logger("solo_stream")


# ── 캡처 프로세스 ─────────────────────────────────────────────────────────────

def capture_process(
    image_queue: multiprocessing.Queue,
    settings_queue: multiprocessing.Queue,
    config: dict,
):
    setup_logger("solo_stream.capture")
    log = logging.getLogger("solo_stream.capture")

    from src.capture.screen import capture_screen
    from src.capture.change_detect import has_significant_change

    interval = config["capture"]["interval_seconds"]
    threshold = config["capture"]["ssim_threshold"]
    target = config["capture"].get("target_window")
    resize_w = config["capture"].get("resize_width", 1024)

    log.info(f"캡처 시작 (주기={interval}s, SSIM threshold={threshold})")
    if target:
        log.info(f"대상 창: '{target}'")

    prev_frame = None
    skip_count = 0
    total_count = 0
    chat_active = False

    while True:
        # 설정 변경 수신 (non-blocking)
        while not settings_queue.empty():
            setting = settings_queue.get_nowait()
            if "target_window" in setting:
                target = setting["target_window"]
                prev_frame = None
                log.info(f"캡처 대상 변경: '{target}'")
            elif "chat_active" in setting:
                chat_active = setting["chat_active"]
                log.info(f"캡처 {'시작' if chat_active else '정지'}")

        if not chat_active:
            time.sleep(0.5)
            continue

        try:
            frame = capture_screen(target_window=target, resize_width=resize_w)
            total_count += 1

            if prev_frame is None or has_significant_change(prev_frame, frame, threshold):
                image_queue.put(frame)
                prev_frame = frame
                log.info(f"[캡처 #{total_count}] 변화 감지 → AI 큐 (스킵율={skip_count/max(total_count,1)*100:.0f}%)")
            else:
                skip_count += 1
                log.debug(f"[캡처 #{total_count}] 유사 프레임 스킵")

            time.sleep(interval)
        except Exception as e:
            log.error(f"캡처 오류: {e}")
            time.sleep(interval)


# ── AI 프로세스 ──────────────────────────────────────────────────────────────

def ai_process(
    image_queue: multiprocessing.Queue,
    ui_queue: multiprocessing.Queue,
    settings_queue: multiprocessing.Queue,
    config: dict,
    api_key: str,
    claude_api_key: str | None = None,
):
    setup_logger("solo_stream")
    setup_logger("solo_stream.ai")
    log = logging.getLogger("solo_stream.ai")

    from src.ai.model_client import create_vision_client, GeminiClient
    from src.chat.persona_loader import load_personas
    from src.chat.orchestrator import schedule_chats
    from src.chat.game_profile_loader import load_profile, build_profile_context, build_vision_context
    from src.utils.logger import get_chat_logger

    client = create_vision_client(config, gemini_api_key=api_key, claude_api_key=claude_api_key)
    # 게임 프로필 생성은 항상 Gemini로 (Google Search 그라운딩 필요)
    gemini_client = GeminiClient(api_key=api_key, config=config)
    bot_pool = load_personas(config["chat"]["persona_dir"])
    log.info(f"봇 풀 {len(bot_pool)}명 로딩: {[b['name'] for b in bot_pool]}")

    chat_log = get_chat_logger()

    # 초기 게임 프로필 로드
    initial_game = config["capture"].get("target_window") or ""
    # 창 타이틀에서 게임명 추출 (예: "CrossCode Min v1.4.2-4" → "CrossCode")
    game_name = _extract_game_name(initial_game)
    game_profile = load_profile(game_name, ai_client=gemini_client, config=config)
    profile_context = build_profile_context(game_profile)
    vision_context = build_vision_context(game_profile)
    log.info(f"게임 프로필 로드: {game_profile.get('name', '?')}")

    batch_size = config["chat"]["batch_size"]
    delay_min = config["chat"]["delay_min"]
    delay_max = config["chat"]["delay_max"]
    chat_history: list[dict] = []
    round_num = 0
    chat_active = False  # 시작/종료 버튼으로 제어
    last_location = ""  # 직전 라운드에서 확인된 위치

    while True:
        # 설정 변경 수신 (non-blocking)
        while not settings_queue.empty():
            setting = settings_queue.get_nowait()
            if "vision_provider" in setting:
                new_provider = setting["vision_provider"]
                new_model = setting.get("vision_model")
                ai_cfg = config.setdefault("ai", {})
                ai_cfg["vision_provider"] = new_provider
                if new_model:
                    if new_provider == "claude":
                        ai_cfg["claude_vision_model"] = new_model
                    else:
                        ai_cfg["vision_model"] = new_model
                log.info(f"vision 변경: {new_provider} / {new_model or '(모델 유지)'}")
                client = create_vision_client(config, gemini_api_key=api_key, claude_api_key=claude_api_key)
            elif "game_name" in setting:
                new_game = setting["game_name"]
                log.info(f"게임 프로필 변경 요청: {new_game}")
                game_profile = load_profile(new_game, ai_client=gemini_client, config=config)
                profile_context = build_profile_context(game_profile)
                vision_context = build_vision_context(game_profile)
                chat_history.clear()
                last_location = ""
                log.info(f"게임 프로필 변경 완료: {game_profile.get('name', '?')}")
            elif "chat_active" in setting:
                chat_active = setting["chat_active"]
                log.info(f"채팅 {'시작' if chat_active else '일시정지'}")

        if not chat_active:
            time.sleep(0.5)
            continue

        try:
            image_bytes = image_queue.get(timeout=5)
            round_num += 1
            log.info(f"=== 라운드 #{round_num} Vision 분석 시작 ===")

            game_context = client.analyze_screenshot(
                image_bytes,
                game_profile_context=vision_context,
                previous_location=last_location,
            )
            log.info(f"[게임 상황] {game_context}")

            # 위치 추출해서 다음 라운드에 전달
            for line in game_context.splitlines():
                if line.startswith("위치:"):
                    loc = line[len("위치:"):].strip()
                    if loc and loc != "알 수 없음":
                        last_location = loc
                    break
            chat_log.info(f"=== 라운드 #{round_num} [{game_profile.get('name','?')}] ===")
            chat_log.info(f"[게임 상황] {game_context}")

            # 라운드마다 풀에서 batch_size만큼 랜덤 샘플링
            sample_size = min(batch_size, len(bot_pool))
            batch = random.sample(bot_pool, sample_size)
            log.info(f"이번 라운드 발언자 {sample_size}명: {[b['name'] for b in batch]}")
            chats = client.generate_chats(
                game_context=game_context,
                bots=batch,
                chat_history=chat_history,
                game_profile_context=profile_context,
            )

            if chats:
                log.info(f"채팅 생성 {len(chats)}개")
                for c in chats:
                    chat_log.info(f"  {c['name']}: {c['message']}")

                chat_history.extend(chats)
                context_window = config["chat"]["context_window"]
                if len(chat_history) > context_window:
                    chat_history = chat_history[-context_window:]

                t = threading.Thread(
                    target=schedule_chats,
                    args=(chats, batch, ui_queue, delay_min, delay_max),
                    daemon=True,
                )
                t.start()

        except multiprocessing.queues.Empty:
            pass  # 타임아웃은 정상 — 다음 루프 계속
        except Exception as e:
            log.error(f"AI 프로세스 오류: {e}", exc_info=True)


def _extract_game_name(window_title: str) -> str:
    """
    창 타이틀에서 게임명 추출.
    예: "CrossCode Min v1.4.2-4" → "CrossCode"
        "Elden Ring" → "Elden Ring"
    버전 번호(v숫자), 'Min', '- Steam' 등 제거.
    """
    import re
    name = window_title or ""
    name = re.sub(r"\s*[-–]\s*Steam.*", "", name)
    name = re.sub(r"\bMin\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\bv\d[\d.]*[-\w]*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "Unknown Game"


# ── UI 프로세스 ──────────────────────────────────────────────────────────────

def ui_process(
    ui_queue: multiprocessing.Queue,
    settings_queue: multiprocessing.Queue,
    config: dict,
):
    from src.ui.overlay import run_overlay
    run_overlay(config, ui_queue, settings_queue)


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    config = load_config()
    api_key = get_api_key(config)
    claude_api_key = get_claude_api_key(config)
    logger.info("Solo Stream 시작")

    image_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=2)
    ui_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=100)
    # 설정 변경을 브로드캐스트하는 큐 (UI → Capture, UI → AI)
    capture_settings_queue: multiprocessing.Queue = multiprocessing.Queue()
    ai_settings_queue: multiprocessing.Queue = multiprocessing.Queue()

    processes = [
        multiprocessing.Process(
            target=capture_process,
            args=(image_queue, capture_settings_queue, config),
            name="CaptureProcess",
            daemon=True,
        ),
        multiprocessing.Process(
            target=ai_process,
            args=(image_queue, ui_queue, ai_settings_queue, config, api_key, claude_api_key),
            name="AIProcess",
            daemon=True,
        ),
    ]

    for p in processes:
        p.start()
        logger.info(f"{p.name} 시작 (PID={p.pid})")

    # UI에서 설정 변경 시 두 큐에 모두 전달하는 브로드캐스트 큐
    broadcast_queue: multiprocessing.Queue = multiprocessing.Queue()

    def _broadcast_settings():
        while True:
            try:
                setting = broadcast_queue.get(timeout=1)
                capture_settings_queue.put(setting)
                ai_settings_queue.put(setting)
            except Exception:
                pass

    broadcast_thread = threading.Thread(target=_broadcast_settings, daemon=True)
    broadcast_thread.start()

    try:
        ui_process(ui_queue, broadcast_queue, config)
    except KeyboardInterrupt:
        logger.info("종료 신호 수신")
    finally:
        for p in processes:
            p.terminate()
        logger.info("Solo Stream 종료")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
