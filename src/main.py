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

from src.utils.config_loader import load_config, get_api_key, get_claude_api_key, get_openai_api_key
from src.utils.logger import setup_logger

logger = setup_logger("solo_stream")

# 흥분(hype) 발동 임계값. 이 이상이면 채팅 타이밍 고도화(빠르게/많이/mood_hint)가 켜진다.
# build_hype_profile·발동 로그가 공유하는 발동 임계값.
HYPE_THRESHOLD = 0.35


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
            frame, captured_from = capture_screen(target_window=target, resize_width=resize_w)
            total_count += 1

            if prev_frame is None or has_significant_change(prev_frame, frame, threshold):
                image_queue.put(frame)
                prev_frame = frame
                log.info(f"[캡처 #{total_count}] [{captured_from}] 변화 감지 → AI 큐 (스킵율={skip_count/max(total_count,1)*100:.0f}%)")
            else:
                skip_count += 1
                log.debug(f"[캡처 #{total_count}] [{captured_from}] 유사 프레임 스킵")

            time.sleep(interval)
        except Exception as e:
            log.error(f"캡처 오류: {e}")
            time.sleep(interval)


# ── 봇 호명 감지 ─────────────────────────────────────────────────────────────

def _detect_mentioned_bots(
    user_speech: list[dict],
    bot_pool: list[dict],
) -> list[dict]:
    """스트리머 발화에서 봇 이름(또는 별칭)이 언급된 봇들을 찾는다.

    발화 텍스트(공백 제거)에 봇 `name` 또는 `aliases` 항목(공백 제거)이
    부분 문자열로 등장하면 호명된 것으로 본다. 별칭은 STT가 이름을 풀어 받아쓰는
    경우(예: "도네요정"→"돈의 요정", "외쿸인"→"외국인")를 보완하기 위한 것.
    bot_pool 순서를 유지해 반환하며, 같은 봇은 한 번만 포함한다.
    """
    if not user_speech or not bot_pool:
        return []
    # 모든 발화 텍스트를 공백 제거해 하나로 합쳐 검사
    joined = "".join((u.get("text") or "").replace(" ", "") for u in user_speech)
    if not joined:
        return []
    mentioned = []
    for b in bot_pool:
        candidates = [b.get("name") or ""]
        candidates.extend(b.get("aliases") or [])
        for cand in candidates:
            key = str(cand).replace(" ", "")
            if key and key in joined:
                mentioned.append(b)
                break
    return mentioned


# ── 라운드 로그 포맷 ─────────────────────────────────────────────────────────

def _write_round_block(
    chat_log: logging.Logger,
    *,
    round_num: int,
    game_name: str,
    game_context: str,
    vision_elapsed: float,
    user_speech: list[dict],
    batch: list[dict],
    default_weight: str,
    chats: list[dict],
    chat_debug,
    profile_context: str = "",
    hype_note: str = "",
) -> None:
    """한 라운드의 vision / STT / 프롬프트 / 응답을 한 블록으로 chat_log 에 출력.

    줄마다 timestamp 가 찍히면 시각적으로 분절되므로,
    `\\n` 으로 합친 단일 멀티라인 메시지를 한 번에 info 호출한다.
    (`_BlockFormatter` 가 첫 줄에만 timestamp 를 찍는다.)
    """
    from src.chat.persona_loader import normalize_user_speech_weight  # noqa: PLC0415

    lines: list[str] = []
    lines.append(f"========== 라운드 #{round_num} [{game_name}] ==========")
    lines.append(f"[VISION] ({vision_elapsed:.2f}s)")
    for gl in (game_context or "").splitlines():
        lines.append(f"  {gl}")

    if user_speech:
        lines.append("")
        lines.append(f"[STT 발화 {len(user_speech)}개]")
        for u in user_speech:
            lines.append(f"  ({u['seconds_ago']:5.1f}s 전) {u['text']}")
    else:
        lines.append("")
        lines.append("[STT 발화 없음]")

    bot_summary = ", ".join(
        f"{b['name']}({normalize_user_speech_weight(b.get('user_speech_weight'), default=default_weight)}"
        f"/{b.get('emotion', '?')})"
        for b in batch
    )
    lines.append("")
    lines.append(f"[봇 {len(batch)}명] {bot_summary}")
    if hype_note:
        lines.append(hype_note)

    if chat_debug is not None:
        lines.append("")
        lines.append(
            f"[CHAT 호출] {chat_debug.provider}/{chat_debug.model}"
            f"  ({chat_debug.elapsed_s:.2f}s)"
        )
        if chat_debug.prompt:
            prompt_for_log = chat_debug.prompt
            # 게임 프로필 컨텍스트는 세션 헤더에서 한 번만 출력하고 라운드별 프롬프트에선 생략
            if profile_context and profile_context in prompt_for_log:
                prompt_for_log = prompt_for_log.replace(
                    profile_context,
                    "(...게임 프로필 컨텍스트 — 세션 헤더 참조...)",
                )
            lines.append("[CHAT 프롬프트]")
            for pl in prompt_for_log.splitlines():
                lines.append(f"  | {pl}")
        if chat_debug.raw_response:
            lines.append("[CHAT 원본 응답]")
            for rl in chat_debug.raw_response.splitlines():
                lines.append(f"  > {rl}")

    lines.append("")
    lines.append(f"[CHAT 결과] {len(chats)}개")
    for c in chats:
        lines.append(f"  {c.get('name','?')}: {c.get('message','')}")
    lines.append("")

    chat_log.info("\n".join(lines))


# ── AI 프로세스 ──────────────────────────────────────────────────────────────

def ai_process(
    image_queue: multiprocessing.Queue,
    ui_queue: multiprocessing.Queue,
    settings_queue: multiprocessing.Queue,
    config: dict,
    api_key: str,
    claude_api_key: str | None = None,
    stt_queue: "multiprocessing.Queue | None" = None,
):
    setup_logger("solo_stream")
    setup_logger("solo_stream.ai")
    log = logging.getLogger("solo_stream.ai")

    from src.ai.model_client import create_vision_client, create_chat_client, GeminiClient
    from src.chat.persona_loader import load_personas
    from src.chat.orchestrator import schedule_chats
    from src.chat.game_profile_loader import load_profile, build_profile_context, build_vision_context
    from src.chat.emotion import EmotionState, update_emotions
    from src.chat.hype import compute_hype, build_hype_profile
    from src.utils.logger import get_chat_logger

    client = create_vision_client(config, gemini_api_key=api_key, claude_api_key=claude_api_key)
    chat_client = create_chat_client(config, gemini_api_key=api_key, claude_api_key=claude_api_key)
    # 게임 프로필 생성은 항상 Gemini로 (Google Search 그라운딩 필요)
    gemini_client = GeminiClient(api_key=api_key, config=config)
    bot_pool = load_personas(config["chat"]["persona_dir"])
    log.info(f"봇 풀 {len(bot_pool)}명 로딩: {[b['name'] for b in bot_pool]}")

    chat_log = get_chat_logger()
    # 세션 시작 시 게임 프로필을 chat_log 에 한 번 박아두고, 라운드 로그에서는 생략한다.
    # (게임 프로필 변경 시 settings_queue 핸들러에서 다시 호출됨)

    # 초기 게임 프로필 로드
    initial_game = config["capture"].get("target_window") or ""
    # 창 타이틀에서 게임명 추출 (예: "CrossCode Min v1.4.2-4" → "CrossCode")
    game_name = _extract_game_name(initial_game)
    game_profile = load_profile(game_name, ai_client=gemini_client, config=config)
    profile_context = build_profile_context(game_profile)
    vision_context = build_vision_context(game_profile)
    log.info(f"게임 프로필 로드: {game_profile.get('name', '?')}")

    # 같은 게임 프로필이 짧은 시간에 여러 번 재로드되어도 헤더는 한 번만 찍는다.
    _last_logged_profile = {"key": None}

    def _log_profile_header(profile_ctx: str, game_label: str):
        key = (game_label, profile_ctx)
        if _last_logged_profile["key"] == key:
            return
        _last_logged_profile["key"] = key
        header = ["########## 세션 시작 — 게임 프로필 ##########",
                  f"# 게임: {game_label}", "#"]
        for ln in profile_ctx.splitlines():
            header.append(f"# {ln}")
        header.append("########## (이하 라운드 프롬프트에서는 위 프로필 컨텍스트를 생략) ##########")
        chat_log_local = get_chat_logger()
        chat_log_local.info("\n".join(header))

    _log_profile_header(profile_context, game_profile.get("name", "?"))

    batch_size = config["chat"]["batch_size"]
    delay_min = config["chat"]["delay_min"]
    delay_max = config["chat"]["delay_max"]
    chat_history: list[dict] = []
    round_num = 0
    chat_active = False  # 시작/종료 버튼으로 제어
    last_location = ""  # 직전 라운드에서 확인된 위치
    # 봇별 동적 감정 상태 (3신호로 매 라운드 갱신). base는 페르소나 YAML emotion.
    emotion_states: dict = {
        b["name"]: EmotionState(b["name"], b.get("emotion", "평온"), b.get("relationship", ""))
        for b in bot_pool
    }

    # STT 설정
    stt_cfg = config.get("stt", {}) or {}
    stt_enabled = bool(stt_cfg.get("enabled", False)) and stt_queue is not None
    lookback_s = float(stt_cfg.get("lookback_seconds", 20.0))
    min_tx_chars = int(stt_cfg.get("min_transcript_chars", 1))
    default_weight = str(stt_cfg.get("default_weight", "medium")).lower()
    # 누적 발화 버퍼: {"timestamp": float, "text": str, "duration": float}
    user_speech_buffer: list[dict] = []

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
            elif "chat_provider" in setting:
                new_provider = setting["chat_provider"]
                new_model = setting.get("chat_model")
                ai_cfg = config.setdefault("ai", {})
                ai_cfg["chat_provider"] = new_provider
                if new_model:
                    if new_provider == "claude":
                        ai_cfg["claude_chat_model"] = new_model
                    else:
                        ai_cfg["chat_model"] = new_model
                log.info(f"chat 모델 변경: {new_provider} / {new_model or '(모델 유지)'}")
                chat_client = create_chat_client(config, gemini_api_key=api_key, claude_api_key=claude_api_key)
            elif "game_name" in setting:
                new_game = setting["game_name"]
                log.info(f"게임 프로필 변경 요청: {new_game}")
                game_profile = load_profile(new_game, ai_client=gemini_client, config=config)
                profile_context = build_profile_context(game_profile)
                vision_context = build_vision_context(game_profile)
                chat_history.clear()
                last_location = ""
                log.info(f"게임 프로필 변경 완료: {game_profile.get('name', '?')}")
                _log_profile_header(profile_context, game_profile.get("name", "?"))
            elif "chat_active" in setting:
                chat_active = setting["chat_active"]
                log.info(f"채팅 {'시작' if chat_active else '일시정지'}")
            elif "stt_runtime" in setting:
                rt = setting["stt_runtime"] or {}
                if "lookback_seconds" in rt:
                    lookback_s = float(rt["lookback_seconds"])
                if "min_transcript_chars" in rt:
                    min_tx_chars = int(rt["min_transcript_chars"])
                if "default_weight" in rt:
                    default_weight = str(rt["default_weight"]).lower()
                log.info(
                    f"STT 런타임 설정 갱신: lookback={lookback_s:.1f}s "
                    f"min_chars={min_tx_chars} default_weight={default_weight}"
                )

        if not chat_active:
            time.sleep(0.5)
            continue

        def _drain_stt_queue():
            """stt_queue 의 모든 발화를 user_speech_buffer 에 누적."""
            if not stt_enabled:
                return
            while True:
                try:
                    ev = stt_queue.get_nowait()
                except multiprocessing.queues.Empty:
                    break
                except Exception:
                    break
                if ev.get("text") and len(ev["text"]) >= min_tx_chars:
                    user_speech_buffer.append(ev)

        # 1차 drain: 라운드 시작 전에 큐를 비워둠
        _drain_stt_queue()

        try:
            image_bytes = image_queue.get(timeout=5)
            round_num += 1
            log.info(f"=== 라운드 #{round_num} Vision 분석 시작 ===")

            vision_t0 = time.perf_counter()
            game_context = client.analyze_screenshot(
                image_bytes,
                game_profile_context=vision_context,
                previous_location=last_location,
            )
            vision_elapsed = time.perf_counter() - vision_t0
            log.info(f"[게임 상황] {game_context}")

            # 위치 / 이벤트 추출 (이벤트는 감정 신호원 — 없으면 빈 문자열)
            event_text = ""
            for line in game_context.splitlines():
                if line.startswith("위치:"):
                    loc = line[len("위치:"):].strip()
                    if loc and loc != "알 수 없음":
                        last_location = loc
                elif line.startswith("이벤트:"):
                    event_text = line[len("이벤트:"):].strip()

            # 2차 drain: vision 분석이 도는 동안(수 초~십수 초) 도착한 발화까지 수거
            _drain_stt_queue()
            # 버퍼 정리: lookback_s 보다 오래된 발화는 제거
            now = time.time()
            user_speech_buffer = [
                e for e in user_speech_buffer if now - e["timestamp"] <= lookback_s
            ]

            # 사용자 발화: 현재 시점 기준 lookback 내 발화만 사용
            user_speech_payload: list[dict] = []
            if stt_enabled and user_speech_buffer:
                for e in user_speech_buffer:
                    sec_ago = now - e["timestamp"]
                    if sec_ago <= lookback_s:
                        user_speech_payload.append({
                            "text": e["text"],
                            "seconds_ago": sec_ago,
                        })

            # 발화에서 호명된 봇은 이번 라운드 batch에 반드시 포함시킨다.
            mentioned = _detect_mentioned_bots(user_speech_payload, bot_pool)
            mentioned_names = [b["name"] for b in mentioned]
            if mentioned_names:
                log.info(f"호명 감지: {mentioned_names}")

            # 감정 갱신: 3신호(채팅 톤 + Vision 이벤트 + STT 발화)로 봇 풀 전체 감정 전이.
            # Vision 이벤트가 비어도 나머지 신호로 굴러간다.
            speech_texts = [u["text"] for u in user_speech_payload]
            emotion_labels = update_emotions(
                emotion_states, bot_pool, chat_history, event_text, speech_texts,
            )

            # 흥분도(hype) 계산: 스트리머 발화 + 직전 채팅 톤(ㅋㅋ/ㅠㅠ/강조어)에서
            # hype(0~1)·valence(±)를 뽑아 이번 라운드 타이밍·물량·분위기 힌트를 정한다.
            recent_chat_texts = [c.get("message", "") for c in chat_history[-8:]]
            hype_signal = compute_hype(speech_texts, recent_chat_texts)
            hype_profile = build_hype_profile(
                hype_signal, base_delay_min=delay_min, base_delay_max=delay_max,
                threshold=HYPE_THRESHOLD,
            )
            round_hyped = hype_profile.hype >= HYPE_THRESHOLD
            if round_hyped:
                log.info(
                    f"흥분 감지 hype={hype_profile.hype:.2f} val={hype_signal.valence:+.2f} "
                    f"→ delay={hype_profile.delay_min}~{hype_profile.delay_max} "
                    f"+batch={hype_profile.extra_batch} skip_relief={hype_profile.skip_relief}"
                )

            # 라운드마다 batch_size(흥분 시 증량)만큼 발언자 선정 (호명 봇 우선)
            sample_size = min(batch_size + hype_profile.extra_batch, len(bot_pool))
            mentioned_in_batch = mentioned[:sample_size]
            remaining_slots = sample_size - len(mentioned_in_batch)
            others_pool = [b for b in bot_pool if b not in mentioned_in_batch]
            others = random.sample(others_pool, min(remaining_slots, len(others_pool)))
            batch = mentioned_in_batch + others
            # batch 봇의 emotion을 동적 라벨로 덮은 사본 사용 (원본 bot_pool은 보존)
            batch = [{**b, "emotion": emotion_labels.get(b["name"], b.get("emotion", "평온"))} for b in batch]
            log.info(f"이번 라운드 발언자 {len(batch)}명: {[b['name'] for b in batch]}")
            chats = chat_client.generate_chats(
                game_context=game_context,
                bots=batch,
                chat_history=chat_history,
                game_profile_context=profile_context,
                user_speech=user_speech_payload or None,
                default_speech_weight=default_weight,
                mentioned_names=mentioned_names,
                mood_hint=hype_profile.mood_hint or None,
            )

            # 한 라운드의 전체 흐름을 chat_log 에 단일 블록으로 출력
            _write_round_block(
                chat_log,
                round_num=round_num,
                game_name=game_profile.get("name", "?"),
                game_context=game_context,
                vision_elapsed=vision_elapsed,
                user_speech=user_speech_payload,
                batch=batch,
                default_weight=default_weight,
                chats=chats,
                chat_debug=getattr(chat_client, "last_chat_call", None),
                profile_context=profile_context,
                hype_note=(
                    f"[흥분] hype={hype_profile.hype:.2f} val={hype_signal.valence:+.2f}"
                    f" → delay={hype_profile.delay_min}~{hype_profile.delay_max}"
                    f" +batch={hype_profile.extra_batch} skip_relief={hype_profile.skip_relief}"
                    if round_hyped else ""
                ),
            )

            # 채팅 생성에 쓰인 발화는 버퍼에서 제거 (같은 발화가 다음 라운드에 또 들어가지 않게)
            if stt_enabled and user_speech_payload:
                used_texts = {u["text"] for u in user_speech_payload}
                user_speech_buffer = [
                    e for e in user_speech_buffer if e["text"] not in used_texts
                ]

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
                    args=(
                        chats, batch, ui_queue,
                        hype_profile.delay_min, hype_profile.delay_max,
                        mentioned_names, hype_profile.skip_relief,
                    ),
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


# ── STT 프로세스 ─────────────────────────────────────────────────────────────

def stt_process(
    stt_queue: multiprocessing.Queue,
    settings_queue: multiprocessing.Queue,
    config: dict,
    api_key: str,
    openai_api_key: str | None = None,
):
    setup_logger("solo_stream")
    setup_logger("solo_stream.stt")
    log = logging.getLogger("solo_stream.stt")

    from src.stt import STTPipeline, UtteranceEvent
    from src.stt.transcriber import create_transcriber

    stt_cfg = config.get("stt", {}) or {}
    sample_rate = int(stt_cfg.get("sample_rate", 16000))

    provider = str(stt_cfg.get("provider", "gemini")).lower()
    transcriber = create_transcriber(
        provider,
        gemini_api_key=api_key,
        openai_api_key=openai_api_key,
        model=stt_cfg.get("model", "gemini-2.5-flash"),
        language=stt_cfg.get("language", "ko"),
        prompt=stt_cfg.get("transcribe_prompt"),
        normalize_target_level=float(stt_cfg.get("normalize_target_level", 0.1)),
        normalize_percentile=float(stt_cfg.get("normalize_percentile", 95.0)),
        normalize_max_gain=float(stt_cfg.get("normalize_max_gain", 20.0)),
    )
    log.info(f"STT transcriber provider={provider} model={stt_cfg.get('model')}")

    pipeline = STTPipeline(
        transcriber=transcriber,
        sample_rate=sample_rate,
        device=stt_cfg.get("device", "default"),
        threshold=float(stt_cfg.get("vad_threshold", 0.5)),
        min_speech_ms=int(stt_cfg.get("min_speech_ms", 250)),
        min_silence_ms=int(stt_cfg.get("min_silence_ms", 500)),
        speech_pad_ms=int(stt_cfg.get("speech_pad_ms", 100)),
        max_utterance_s=float(stt_cfg.get("max_utterance_seconds", 30.0)),
        min_transcript_chars=int(stt_cfg.get("min_transcript_chars", 1)),
        input_gain=float(stt_cfg.get("input_gain", 1.0)),
    )

    def _push(ev: "UtteranceEvent") -> None:
        try:
            stt_queue.put({
                "timestamp": ev.timestamp,
                "text": ev.text,
                "duration": ev.duration_s,
            }, timeout=0.5)
        except Exception as e:
            log.warning(f"stt_queue.put 실패: {e}")

    chat_active = False
    started = False

    while True:
        # 설정 변경 수신
        while not settings_queue.empty():
            setting = settings_queue.get_nowait()
            if "chat_active" in setting:
                chat_active = setting["chat_active"]
                log.info(f"STT {'시작' if chat_active else '일시정지'}")

        if chat_active and not started:
            pipeline.start(_push)
            started = True
        elif not chat_active and started:
            pipeline.stop()
            started = False

        time.sleep(0.3)


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
    openai_api_key = get_openai_api_key(config)
    logger.info("Solo Stream 시작")

    # 오래된 로그 정리 (자식 프로세스 spawn 전 1회)
    from src.utils.logger import prune_old_logs
    removed = prune_old_logs()
    if removed:
        logger.info(f"오래된 로그 {removed}개 정리 (7일 경과)")

    image_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=2)
    ui_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=100)
    # 설정 변경을 브로드캐스트하는 큐 (UI → Capture, UI → AI, UI → STT)
    capture_settings_queue: multiprocessing.Queue = multiprocessing.Queue()
    ai_settings_queue: multiprocessing.Queue = multiprocessing.Queue()
    stt_settings_queue: multiprocessing.Queue = multiprocessing.Queue()

    stt_enabled = bool((config.get("stt") or {}).get("enabled", False))
    stt_queue: multiprocessing.Queue | None = (
        multiprocessing.Queue(maxsize=200) if stt_enabled else None
    )

    processes = [
        multiprocessing.Process(
            target=capture_process,
            args=(image_queue, capture_settings_queue, config),
            name="CaptureProcess",
            daemon=True,
        ),
        multiprocessing.Process(
            target=ai_process,
            args=(image_queue, ui_queue, ai_settings_queue, config, api_key, claude_api_key, stt_queue),
            name="AIProcess",
            daemon=True,
        ),
    ]
    if stt_enabled:
        processes.append(multiprocessing.Process(
            target=stt_process,
            args=(stt_queue, stt_settings_queue, config, api_key, openai_api_key),
            name="STTProcess",
            daemon=True,
        ))
        logger.info("STT enabled — STTProcess 추가")
    else:
        logger.info("STT disabled (config.stt.enabled=false) — STTProcess 미시작")

    for p in processes:
        p.start()
        logger.info(f"{p.name} 시작 (PID={p.pid})")

    # UI에서 설정 변경 시 모든 큐에 전달하는 브로드캐스트 큐
    broadcast_queue: multiprocessing.Queue = multiprocessing.Queue()

    def _broadcast_settings():
        import queue
        while True:
            try:
                setting = broadcast_queue.get(timeout=1)
                capture_settings_queue.put(setting)
                ai_settings_queue.put(setting)
                if stt_enabled:
                    stt_settings_queue.put(setting)
                logger.debug(f"[브로드캐스트] {setting}")
            except queue.Empty:
                pass
            except Exception as e:
                logger.error(f"[브로드캐스트 오류] {e}")

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
