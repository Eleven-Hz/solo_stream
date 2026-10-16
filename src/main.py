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
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import load_config, get_api_key, get_claude_api_key, get_openai_api_key
from src.utils.logger import setup_logger

logger = setup_logger("solo_stream")

# 흥분(hype) 발동 임계값. 이 이상이면 채팅 타이밍 고도화(빠르게/많이/mood_hint)가 켜진다.
# build_hype_profile·발동 로그·메아리 감쇠 판정이 모두 이 값을 공유한다.
HYPE_THRESHOLD = 0.35
# 직전 라운드가 발동이었던 다음 라운드에 적용하는 상향 임계값. 봇 채팅 신호를 감쇠해도
# 메아리가 임계값 바로 위에 걸려 발동이 안 풀리는 경계 케이스를 평소로 되돌린다.
HYPE_THRESHOLD_AFTER_HYPE = 0.5


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

    interval = _clamp_interval(config["capture"].get("interval_seconds", BASE_INTERVAL_S))
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
            elif "interval_seconds" in setting:
                interval = _clamp_interval(setting["interval_seconds"])
                log.info(f"캡처 주기 변경: {interval:g}s")
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


# ── 캡처 주기에 맞춘 페이싱 ──────────────────────────────────────────────────
# chat.delay_min/max · topic_window_rounds 등 기본값은 15초 주기 기준으로 맞춰져 있다.
# 주기를 바꾸면 같은 '시간 감각'이 유지되도록 비례해서 늘리고 줄인다.
BASE_INTERVAL_S = 15.0
MIN_INTERVAL_S = 5.0     # vision+chat 한 라운드가 3~6초라 이보다 짧으면 라운드가 밀린다
MAX_INTERVAL_S = 60.0
# 한 발화를 채팅 프롬프트에 넣는 최대 라운드 수. 주기가 짧으면 lookback 안에서 같은
# 발화가 여러 라운드에 반복돼 봇들이 같은 말에 계속 반응하는 걸 막는다.
MAX_SPEECH_ROUNDS = 2


def _clamp_interval(sec) -> float:
    try:
        sec = float(sec)
    except (TypeError, ValueError):
        return BASE_INTERVAL_S
    return max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, sec))


def _paced(config: dict, interval_s: float) -> tuple[float, float, int]:
    """주기에 맞춘 (delay_min, delay_max, topic_window_rounds).

    채팅은 한 라운드 안에서 메시지마다 delay 만큼 차례로 뜨므로, delay 를 주기에 비례시켜
    다음 라운드와 겹치지 않게 한다. 소재 쏠림 윈도우는 '라운드 수'가 아니라 같은 시간(≈90초)을 보게 한다."""
    scale = interval_s / BASE_INTERVAL_S
    chat_cfg = config.get("chat", {}) or {}
    d_min = float(chat_cfg.get("delay_min", 1.0)) * scale
    d_max = float(chat_cfg.get("delay_max", 8.0)) * scale
    window = max(3, round(int(chat_cfg.get("topic_window_rounds", 6)) / scale))
    return round(d_min, 2), round(d_max, 2), window


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


def _normalize_msg(text: str) -> str:
    """중복 비교용 정규화: 공백·문장부호 제거 + 소문자. ㅠㅠㅠㅠ 길이 차 등은 의도적으로
    구분 유지(완전 동일 복붙만 잡는다). 문장부호는 떼어 단답 봇의 "흠." / "흠" 같은
    사실상 같은 연속 단답도 잡는다(KNOWN_ISSUES 4-1). 부호만 있는 메시지("...")는 그대로 둔다."""
    compact = "".join((text or "").split()).lower()
    stripped = compact.strip(".,!?~…·")
    return stripped or compact


def _drop_repeat_chats(
    chats: list[dict],
    chat_history: list[dict],
    *,
    recent_n: int = 10,
) -> list[dict]:
    """LLM이 직전 라운드 발언을 글자 그대로 복붙한 채팅을 드롭한다 (B: 중복 차단).

    프롬프트 규칙(A)으로도 대부분 막히지만, 가끔 새는 완전 동일 복붙을 코드에서
    확정적으로 거른다. 판정 기준:
      - 같은 봇(name)이 최근 chat_history에서 친 것과 메시지가 동일하면 드롭.
      - 이번 라운드 batch 안에서도 같은 (name, message) 중복이면 한 번만 남긴다.
    서로 다른 봇이 우연히 같은 짧은 리액션("ㅠㅠ")을 치는 것은 자연스러우므로
    name이 다르면 막지 않는다 (그건 봇별 페르소나 표현).
    """
    recent = chat_history[-recent_n:]
    seen_by_name: set[tuple[str, str]] = {
        (c.get("name", ""), _normalize_msg(c.get("message", ""))) for c in recent
    }
    out: list[dict] = []
    for c in chats:
        key = (c.get("name", ""), _normalize_msg(c.get("message", "")))
        if not key[1]:
            continue  # 빈 메시지 제외
        if key in seen_by_name:
            logger.debug(f"[중복 드롭] {key[0]}: {c.get('message','')}")
            continue
        seen_by_name.add(key)
        out.append(c)
    return out


# ── 라운드 로그 포맷 ─────────────────────────────────────────────────────────

def _send_usage(ui_queue, source: str, snap: dict) -> None:
    """사용량 스냅샷을 UI 로. 큐가 차 있으면 버린다 — 누적값이라 다음 스냅샷이 대신한다."""
    try:
        ui_queue.put_nowait({"system": True, "type": "usage", "source": source, "data": snap})
    except Exception:
        pass


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
            f"  ({chat_debug.elapsed_s:.2f}s, 토큰 in={chat_debug.in_tokens} out={chat_debug.out_tokens})"
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
    openai_api_key: str | None = None,
):
    setup_logger("solo_stream")
    setup_logger("solo_stream.ai")
    log = logging.getLogger("solo_stream.ai")

    from src.ai.model_client import (
        create_vision_client, create_chat_client, GeminiClient, is_transient_error,
        model_config_key, resolve_provider,
    )
    from src.chat.persona_loader import load_personas
    from src.chat.orchestrator import schedule_chats
    from src.chat.game_profile_loader import load_profile, build_profile_context, build_vision_context
    from src.chat.emotion import EmotionState, update_emotions
    from src.chat.hype import compute_hype, build_hype_profile
    from src.export.websocket import start_broadcaster
    from src.utils.logger import get_chat_logger

    # 채팅 외부 출력(WS). 기본 비활성 — config 의 export.websocket.enabled 로 켠다.
    # schedule_chats 가 이 브로드캐스터를 통해 외부 소비자(project_ns)에게도 채팅을 보낸다.
    start_broadcaster(config)

    # 실제 사용량: 호출마다 토큰을 모아 UI '사용량' 탭으로 보낸다(STT 는 stt_process 가 따로).
    from src.utils import usage
    usage.set_sink(usage.UsageMeter(on_change=lambda snap: _send_usage(ui_queue, "ai", snap)).add)

    provider_keys = {"gemini": api_key, "claude": claude_api_key, "openai": openai_api_key}
    _LABEL = {"gemini": "Gemini", "openai": "OpenAI", "claude": "Claude"}

    def _make_client(kind: str):
        """설정된 provider 로 클라이언트를 만든다. 키가 없어 다른 provider 로 대체되면 채팅창에 알린다."""
        provider, model, fell_from = resolve_provider(config, kind, provider_keys)
        if fell_from:
            label = "화면 분석" if kind == "vision" else "채팅 생성"
            ui_queue.put({
                "name": "시스템",
                "message": (f"ℹ {_LABEL[fell_from]} 키가 없어 {label}을 {_LABEL[provider]}({model})로 "
                            f"대체합니다 — 설정 'AI 모델 · 키'에서 키를 추가하세요."),
            })
        log.info(f"{kind}: {provider}/{model}" + (f" ({fell_from} 대체)" if fell_from else ""))
        factory = create_vision_client if kind == "vision" else create_chat_client
        return factory(config, gemini_api_key=api_key, claude_api_key=claude_api_key,
                       openai_api_key=openai_api_key)

    client = _make_client("vision")
    chat_client = _make_client("chat")
    # 게임 프로필 '자동 생성'만 Gemini 전용(Google Search 그라운딩). Gemini 키가 없으면 None —
    # 자동 생성 요청이 와도 빈 프로필로 진행하고 안내한다.
    gemini_client = GeminiClient(api_key=api_key, config=config) if api_key else None
    bot_pool = load_personas(config["chat"]["persona_dir"])
    log.info(f"봇 풀 {len(bot_pool)}명 로딩: {[b['name'] for b in bot_pool]}")

    # 소재(클리셰) 쏠림 억제 (KNOWN_ISSUES 4-1/4-2): 최근 라운드 과열 소재를 프롬프트로
    # 피하게 하고, 라운드 안에서 같은 소재를 쓰는 봇 수를 후처리로 제한한다.
    from src.chat.topic_guard import TopicTracker, limit_round_topics
    from src.chat import flow
    chat_cfg = config.get("chat", {}) or {}
    topic_tracker = TopicTracker(
        window_rounds=int(chat_cfg.get("topic_window_rounds", 6)),
        cliche_keywords=chat_cfg.get("cliche_keywords"),
    )
    topic_round_max = int(chat_cfg.get("topic_round_max", 2))
    # 대화 흐름(src/chat/flow.py): 직전 발언자 일부를 이어서 출석시키고, 1명은 앞 채팅을 받아친다.
    carry_over = int(chat_cfg.get("carry_over", 1))
    reply_chance = float(chat_cfg.get("reply_chance", 0.7))
    bot_habits = flow.BotHabits()

    chat_log = get_chat_logger()
    # 세션 시작 시 게임 프로필을 chat_log 에 한 번 박아두고, 라운드 로그에서는 생략한다.
    # (게임 프로필 변경 시 settings_queue 핸들러에서 다시 호출됨)

    # 초기 게임 프로필 로드
    # 마지막 세션에서 명시적으로 적용한 게임명이 있으면 그것을 우선 사용한다.
    # (자동 감지/추출보다 사용자가 "프로필 적용"한 선택이 우선)
    restored_game = config["capture"].get("game_name")
    if restored_game:
        game_name = restored_game
    else:
        initial_game = config["capture"].get("target_window") or ""
        # 창 타이틀에서 게임명 추출 (예: "CrossCode Min v1.4.2-4" → "CrossCode")
        game_name = _extract_game_name(initial_game)

    # 캡처 대상 창 타이틀. 프로필명과 비교해 불일치 경고를 띄우는 데 쓴다.
    # settings_queue 의 target_window 변경 시 갱신된다(아래 루프). 닫힘 변수로 공유.
    _current_window = {"title": config["capture"].get("target_window") or ""}

    def _notify_profile_loaded(profile: dict):
        """프로필 로드 완료를 UI에 통지. 생성 중 단계 실패가 있으면 채팅창에도 표시."""
        # 캡처 창 ↔ 적용 프로필 이름이 많이 다르면 경고 텍스트를 함께 실어 보낸다.
        # (채팅창이 아니라 상단 타이틀바 아이콘으로 표시 — 채팅 흐름을 방해하지 않음)
        win_title = _current_window["title"]
        prof_name = profile.get("name", "?")
        warning = None
        if _profile_name_mismatch(win_title, prof_name):
            warning = (
                f"캡처 중인 창('{_extract_game_name(win_title)}')과 적용된 게임 "
                f"프로필('{prof_name}')이 많이 다릅니다.\n프로필이 잘못 적용됐을 수 "
                f"있으니 '게임 프로필' 탭에서 확인하세요."
            )
        # 프로필 생성 결과 상태를 한 번에 산출해 UI(설정 탭)에 지속 표시되게 실어 보낸다.
        #   ok      : 정상 생성/로드
        #   aborted : 503 등으로 생성이 통째로 중단됨(빈 프로필 임시 사용)
        #   partial : 일부 단계만 실패(나머지 정보는 있음)
        aborted = profile.get("_generation_aborted")
        gen_errors = profile.get("_generation_errors") or []
        if profile.get("_missing"):
            status, detail = "missing", ""
        elif aborted:
            status, detail = "aborted", str(aborted)
        elif gen_errors:
            summary = " / ".join(gen_errors[:3])
            if len(gen_errors) > 3:
                summary += f" 외 {len(gen_errors) - 3}건"
            status, detail = "partial", f"{len(gen_errors)}단계 실패 — {summary}"
        else:
            status, detail = "ok", ""

        ui_queue.put({"system": True, "type": "profile_loaded",
                      "name": prof_name, "warning": warning,
                      "status": status, "detail": detail})

        # 채팅창에도 1회성 안내(흘러가지만 즉시 인지용). 설정 탭에는 위 status로 상시 표시됨.
        if status == "missing":
            ui_queue.put({
                "name": "시스템",
                "message": (
                    f"ℹ '{prof_name}' 게임 프로필이 없어요. 화면만 보고 채팅합니다. "
                    f"설정 '게임 · 프로필'에서 프롬프트를 복사해 ChatGPT·Claude·Gemini에 "
                    f"붙여넣으면 무료로 만들 수 있어요."
                ),
            })
        elif status == "aborted":
            ui_queue.put({
                "name": "시스템",
                "message": (
                    f"⚠ 게임 프로필 생성이 서버 혼잡(503)으로 중단됐습니다. "
                    f"모델이 일시적으로 과부하 상태입니다. 빈 프로필로 임시 진행하며, "
                    f"잠시 후 '게임 프로필 재생성'으로 다시 시도하세요. — {detail}"
                ),
            })
        elif status == "partial":
            ui_queue.put({
                "name": "시스템",
                "message": (
                    f"⚠ 게임 프로필 일부 생성 실패. "
                    f"서버 혼잡(503) 등 일시적 오류일 수 있습니다. "
                    f"잠시 후 '게임 프로필 재생성'으로 다시 시도하세요. — {detail}"
                ),
            })

    game_profile = load_profile(game_name, ai_client=gemini_client, config=config)  # 없으면 missing(자동 생성 안 함)
    profile_context = build_profile_context(game_profile)
    vision_context = build_vision_context(game_profile)
    log.info(f"게임 프로필 로드: {game_profile.get('name', '?')}")
    _notify_profile_loaded(game_profile)

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
    interval_s = _clamp_interval(config.get("capture", {}).get("interval_seconds", BASE_INTERVAL_S))
    delay_min, delay_max, topic_window = _paced(config, interval_s)
    topic_tracker.set_window(topic_window)
    log.info(f"페이싱: 주기 {interval_s:g}s → 채팅 간격 {delay_min}~{delay_max}s, 소재 윈도우 {topic_window}라운드")
    chat_history: list[dict] = []
    round_num = 0
    typing_round = None  # '입력 중' 표시를 켜 둔 라운드(끝나면 꺼야 함)
    chat_active = False  # 시작/종료 버튼으로 제어
    last_location = ""  # 직전 라운드에서 확인된 위치
    # 직전 채팅 생성이 503 등 서버 혼잡이었는지. 상태가 바뀔 때만 채팅창에 안내해
    # (혼잡 시작 1회 / 회복 1회) 매 라운드 같은 메시지가 쌓이지 않게 한다.
    api_busy = False
    # 직전 라운드 흥분 발동 여부. 발동이면 그 라운드 봇 채팅(mood_hint로 ㅋㅋ/ㅠㅠ 대량
    # 생성)이 이번 hype 입력으로 되먹임돼 분위기가 봇 메아리만으로 눈덩이처럼 커진다
    # (부정 자가발전 루프). 발동 직후 라운드는 채팅 신호를 감쇠해 메아리를 끊는다.
    prev_round_hyped = False
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
    # 누적 발화 버퍼: {"timestamp": float, "text": str, "duration": float, "recv": 도착 시각, "rounds": 사용 횟수}
    user_speech_buffer: list[dict] = []
    # 발화 즉시 반응: 받아쓰기가 도착하면 다음 화면 분석을 기다리지 않고 직전 화면으로 채팅 라운드를
    # 바로 돌린다(채팅 호출만, 화면 분석 없음). 주기 대기 때문에 말하고 10~20초 뒤에야 반응하던 문제.
    instant_reply = bool(stt_cfg.get("instant_reply", True))
    instant_debounce = float(stt_cfg.get("instant_debounce_seconds", 1.5))
    instant_min_gap = float(stt_cfg.get("instant_min_gap_seconds", 5.0))
    instant_max_wait = float(stt_cfg.get("instant_max_wait_seconds", 4.0))
    instant_batch = int(stt_cfg.get("instant_batch", 3))
    last_game_context = ""
    last_vision_t = 0.0
    last_round_start = 0.0
    prev_batch_names: list[str] = []
    last_round_chats: list[dict] = []
    emotion_labels: dict = {}

    def _speech_round_due(now: float) -> bool:
        """새 발화가 있고(아직 안 쓴 것), 말이 끝난 지 debounce 가 지났고, 다음 화면 분석이 곧 오지 않으면 True."""
        fresh = [e for e in user_speech_buffer if e.get("rounds", 0) == 0]
        if not fresh:
            return False
        recv = [e.get("recv", e["timestamp"]) for e in fresh]
        # 이어서 말하는 중일 수 있다 — 문장이 끝나길 잠깐 기다리되, 계속 말하면 max_wait 후엔 반응
        if now - max(recv) < instant_debounce and now - min(recv) < instant_max_wait:
            return False
        # 직전 라운드 '시작'부터 min_gap — 화면 분석 라운드가 길어져도(분석+채팅 7~8초) 그 직후 반응할 틈을 남긴다
        if now - last_round_start < instant_min_gap:
            return False
        # 화면 분석 라운드가 3초 안에 올 예정이면 그쪽에서 같이 반응한다
        return now - last_vision_t < interval_s - 3.0

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
                    ai_cfg[model_config_key(new_provider, "vision")] = new_model
                log.info(f"vision 변경: {new_provider} / {new_model or '(모델 유지)'}")
                client = _make_client("vision")
            elif "vision_prompt" in setting:
                ai_cfg = config.setdefault("ai", {})
                ai_cfg["vision_prompt"] = setting["vision_prompt"]
                log.info("vision 프롬프트 변경 — vision client 재생성")
                client = _make_client("vision")
            elif "chat_provider" in setting:
                new_provider = setting["chat_provider"]
                new_model = setting.get("chat_model")
                ai_cfg = config.setdefault("ai", {})
                ai_cfg["chat_provider"] = new_provider
                if new_model:
                    ai_cfg[model_config_key(new_provider, "chat")] = new_model
                log.info(f"chat 모델 변경: {new_provider} / {new_model or '(모델 유지)'}")
                chat_client = _make_client("chat")
            elif "interval_seconds" in setting:
                interval_s = _clamp_interval(setting["interval_seconds"])
                config.setdefault("capture", {})["interval_seconds"] = interval_s
                delay_min, delay_max, topic_window = _paced(config, interval_s)
                topic_tracker.set_window(topic_window)
                log.info(f"페이싱 변경: 주기 {interval_s:g}s → 채팅 간격 {delay_min}~{delay_max}s, "
                         f"소재 윈도우 {topic_window}라운드")
            elif "profile_model" in setting:
                # 다음 프로필 생성/재생성 때 load_profile(config=...)에서 읽힌다.
                ai_cfg = config.setdefault("ai", {})
                ai_cfg["profile_model"] = setting["profile_model"]
                log.info(f"게임 프로필 생성 모델 변경: {setting['profile_model']}")
            elif "target_window" in setting:
                # 캡처는 별도 프로세스가 처리하지만, AI 프로세스도 프로필 불일치
                # 경고를 위해 현재 창 타이틀을 추적해 둔다.
                _current_window["title"] = setting["target_window"] or ""
            elif "game_name" in setting:
                new_game = setting["game_name"]
                # generate=True 는 설정의 '자동 생성(API)' 버튼 — 그때만 Gemini 검색으로 만든다.
                auto_gen = bool(setting.get("generate"))
                if auto_gen and gemini_client is None:
                    auto_gen = False
                    ui_queue.put({"name": "시스템", "message": (
                        "ℹ 프로필 자동 생성은 Gemini 키가 있어야 해요(구글 검색 사용). "
                        "'프롬프트 복사'로 무료로 만들 수 있어요.")})
                log.info(f"게임 프로필 변경 요청: {new_game}{' (자동 생성)' if auto_gen else ''}")
                game_profile = load_profile(new_game, ai_client=gemini_client, config=config,
                                            auto_generate=auto_gen)
                profile_context = build_profile_context(game_profile)
                vision_context = build_vision_context(game_profile)
                chat_history.clear()
                topic_tracker.clear()
                bot_habits.clear()
                last_round_chats = []
                last_game_context = ""
                last_location = ""
                log.info(f"게임 프로필 변경 완료: {game_profile.get('name', '?')}")
                _log_profile_header(profile_context, game_profile.get("name", "?"))
                # UI 라벨("재생성 중...")을 완료 상태로 갱신 + 생성 실패 시 안내 표시
                _notify_profile_loaded(game_profile)
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
                if "instant_reply" in rt:
                    instant_reply = bool(rt["instant_reply"])
                log.info(
                    f"STT 런타임 설정 갱신: lookback={lookback_s:.1f}s "
                    f"min_chars={min_tx_chars} default_weight={default_weight} instant_reply={instant_reply}"
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
                    ev["recv"] = time.time()
                    user_speech_buffer.append(ev)

        # 1차 drain: 라운드 시작 전에 큐를 비워둠
        _drain_stt_queue()

        try:
            try:
                image_bytes = image_queue.get(timeout=0.5)
            except multiprocessing.queues.Empty:
                # 화면이 안 왔으면 새 발화로 즉시 반응할지 본다. 아니면 다음 루프.
                _drain_stt_queue()
                if not (stt_enabled and instant_reply and last_game_context
                        and _speech_round_due(time.time())):
                    raise
                image_bytes = None
            speech_round = image_bytes is None
            last_round_start = time.time()
            round_num += 1
            # 채팅창 하단 '입력 중' 표시. 이 라운드 채팅이 다 뜨면(schedule_chats) 또는 0개면 끈다.
            ui_queue.put({"system": True, "type": "typing", "round": round_num, "on": True})
            typing_round = round_num
            event_text = ""
            if speech_round:
                log.info(f"=== 라운드 #{round_num} 발화 즉시 반응 (직전 화면 재사용) ===")
                game_context = last_game_context
                vision_elapsed = 0.0
            else:
                log.info(f"=== 라운드 #{round_num} Vision 분석 시작 ===")
                last_vision_t = time.time()

                vision_t0 = time.perf_counter()
                game_context = client.analyze_screenshot(
                    image_bytes,
                    game_profile_context=vision_context,
                    previous_location=last_location,
                )
                vision_elapsed = time.perf_counter() - vision_t0
                log.info(f"[게임 상황] {game_context}")
                last_game_context = game_context

                # 위치 / 이벤트 추출 (이벤트는 감정 신호원 — 없으면 빈 문자열)
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
            speech_used: list[dict] = []
            if stt_enabled and user_speech_buffer:
                for e in user_speech_buffer:
                    sec_ago = now - e["timestamp"]
                    if sec_ago <= lookback_s and e.get("rounds", 0) < MAX_SPEECH_ROUNDS:
                        speech_used.append(e)
                        user_speech_payload.append({
                            "text": e["text"],
                            "seconds_ago": sec_ago,
                            "reacted": e.get("rounds", 0) > 0,
                        })

            # 발화에서 호명된 봇은 이번 라운드 batch에 반드시 포함시킨다.
            mentioned = _detect_mentioned_bots(user_speech_payload, bot_pool)
            mentioned_names = [b["name"] for b in mentioned]
            if mentioned_names:
                log.info(f"호명 감지: {mentioned_names}")

            # 감정 갱신: 3신호(채팅 톤 + Vision 이벤트 + STT 발화)로 봇 풀 전체 감정 전이.
            # Vision 이벤트가 비어도 나머지 신호로 굴러간다.
            speech_texts = [u["text"] for u in user_speech_payload]
            # 발화 라운드는 화면 분석 사이에 끼는 추가 라운드라 감정 감쇠를 두 번 돌리지 않는다.
            if not speech_round or not emotion_labels:
                emotion_labels = update_emotions(
                    emotion_states, bot_pool, chat_history, event_text, speech_texts,
                )

            # 흥분도(hype) 계산: 스트리머 발화 + 직전 채팅 톤(ㅋㅋ/ㅠㅠ/강조어)에서
            # hype(0~1)·valence(±)를 뽑아 이번 라운드 타이밍·물량·분위기 힌트를 정한다.
            # 직전 라운드가 발동이었으면 봇 채팅 신호를 감쇠(0.3)해 ㅋㅋ/ㅠㅠ 메아리가
            # 분위기를 자가발전시키는 걸 끊는다. 발화 신호는 감쇠하지 않는다.
            recent_chat_texts = [c.get("message", "") for c in chat_history[-8:]]
            echo_damp = 0.15 if prev_round_hyped else 1.0
            hype_signal = compute_hype(
                speech_texts, recent_chat_texts, chat_echo_damp=echo_damp,
            )
            # 감쇠 라운드는 임계값을 올려, 메아리만 남은 경계 케이스(감쇠 후 hype가
            # 임계값 바로 위)도 평소로 되돌린다 — 발동을 "한 박자 쉬게" 한다.
            round_threshold = HYPE_THRESHOLD_AFTER_HYPE if prev_round_hyped else HYPE_THRESHOLD
            hype_profile = build_hype_profile(
                hype_signal, base_delay_min=delay_min, base_delay_max=delay_max,
                threshold=round_threshold,
            )
            round_hyped = hype_profile.hype >= round_threshold
            if round_hyped:
                log.info(
                    f"흥분 감지 hype={hype_profile.hype:.2f} val={hype_signal.valence:+.2f} "
                    f"→ delay={hype_profile.delay_min}~{hype_profile.delay_max} "
                    f"+batch={hype_profile.extra_batch} skip_relief={hype_profile.skip_relief}"
                    f"{' (메아리 감쇠 적용)' if prev_round_hyped else ''}"
                )
            prev_round_hyped = round_hyped

            # 라운드마다 batch_size(흥분 시 증량)만큼 발언자 선정: 호명 봇 → 직전 발언자 carry_over 명 → 무작위.
            # 발화 즉시 반응 라운드는 instant_batch 명만(짧게 받아치기).
            base_size = instant_batch if speech_round else batch_size + hype_profile.extra_batch
            sample_size = min(base_size, len(bot_pool))
            batch = flow.pick_batch(bot_pool, sample_size, must=mentioned,
                                    prev_names=prev_batch_names, carry=carry_over)
            # batch 봇의 emotion을 동적 라벨로 덮은 사본 사용 (원본 bot_pool은 보존)
            batch = [{**b, "emotion": emotion_labels.get(b["name"], b.get("emotion", "평온"))} for b in batch]
            # 봇별 말버릇(최근 계속 쓴 단어)은 이번엔 금지 — 지금 화면·발화에 나온 단어는 제외
            now_text = " ".join([game_context] + speech_texts)
            batch = bot_habits.annotate(batch, now_text)
            log.info(f"이번 라운드 발언자 {len(batch)}명: {[b['name'] for b in batch]}")
            # 과열 소재 회피 — 지금 화면·발화에 나온 소재(보스전 중 '보스' 등)는 면제
            hot_topics = flow.exempt_current(topic_tracker.hot_keys(), game_context, speech_texts)
            if hot_topics:
                log.info(f"과열 소재(이번 라운드 회피): {hot_topics}")
            # 발화 즉시 반응 라운드는 발화에 집중 — 봇끼리 받아치기는 화면 분석 라운드에서만
            reply = None if speech_round else flow.pick_reply(
                batch, last_round_chats, chance=reply_chance, exclude=set(mentioned_names))
            round_notes = flow.build_round_notes(speech=user_speech_payload, bots=batch,
                                                 reply=reply, speech_round=speech_round)
            if reply:
                log.info(f"받아치기: {reply[0]} → {reply[1]['name']}")
            chats = chat_client.generate_chats(
                game_context=game_context,
                bots=batch,
                chat_history=chat_history,
                game_profile_context=profile_context,
                user_speech=user_speech_payload or None,
                default_speech_weight=default_weight,
                mentioned_names=mentioned_names,
                mood_hint=hype_profile.mood_hint or None,
                roster=bot_pool,
                avoid_topics=hot_topics,
                round_notes=round_notes,
            )

            # 채팅 생성이 503 등 서버 혼잡이면 채팅창에 안내. 매 라운드 반복되지
            # 않도록 상태가 바뀔 때만(혼잡 시작 1회 / 회복 1회) 띄운다.
            chat_err = getattr(chat_client, "last_chat_call", None)
            chat_err = chat_err.error if chat_err else ""
            now_busy = is_transient_error(chat_err)
            if now_busy and not api_busy:
                ui_queue.put({
                    "name": "시스템",
                    "message": ("⚠ AI 서버가 혼잡합니다(503 등). 잠시 동안 채팅이 "
                                "끊기거나 누락될 수 있습니다. 자동으로 다시 시도합니다."),
                })
            elif api_busy and not now_busy:
                ui_queue.put({
                    "name": "시스템",
                    "message": "✓ AI 서버 연결이 회복됐습니다. 채팅을 다시 생성합니다.",
                })
            api_busy = now_busy
            # 채팅이 만들어진 라운드만 발화 사용 횟수를 센다(실패 라운드의 발화는 다음에 다시 쓴다).
            if not chat_err:
                for e in speech_used:
                    e["rounds"] = e.get("rounds", 0) + 1

            # 이번 라운드 발언자(batch)가 아닌 봇의 채팅은 버린다. 프롬프트에 다른 시청자
            # 목록(roster)이 들어가 LLM 이 그 봇들 채팅까지 지어내는 경우가 있다.
            batch_names = {b["name"] for b in batch}
            outsiders = [c for c in chats if c.get("name") not in batch_names]
            if outsiders:
                log.info(f"발언자 외 채팅 {len(outsiders)}개 드롭: {[c.get('name') for c in outsiders]}")
                chats = [c for c in chats if c.get("name") in batch_names]

            # 직전 라운드 발언을 그대로 복붙한 채팅 드롭 (B). hype delay 축소로 인접
            # 라운드 간격이 짧아지면 같은 메시지가 화면에 거의 동시에 떠 중복으로 보인다.
            before = len(chats)
            chats = _drop_repeat_chats(chats, chat_history)
            if len(chats) < before:
                log.info(f"중복 복붙 {before - len(chats)}개 드롭")

            # 같은 소재에 여러 봇이 몰린 채팅 드롭 (호명 봇은 면제)
            chats, topic_dropped = limit_round_topics(
                chats,
                hot_keys=hot_topics,
                cliche_keywords=topic_tracker.cliche_keywords,
                per_round_max=topic_round_max,
                exempt_names=set(mentioned_names),
            )
            if topic_dropped:
                log.info("소재 쏠림 드롭: " + ", ".join(
                    f"{c.get('name')}[{k}]" for c, k in topic_dropped))
            chats, habit_dropped = bot_habits.drop_habit_chats(chats, batch, set(mentioned_names))
            if habit_dropped:
                log.info("말버릇 반복 드롭: " + ", ".join(f"{c.get('name')}[{k}]" for c, k in habit_dropped))
            topic_tracker.push_round([c.get("message", "") for c in chats])
            bot_habits.push(chats)
            prev_batch_names = [b["name"] for b in batch]
            last_round_chats = list(chats)

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
                    f"{' (메아리 감쇠)' if echo_damp < 1.0 else ''}"
                    if round_hyped else ""
                ),
            )

            # MAX_SPEECH_ROUNDS 번 쓰인 발화는 버퍼에서 제거. 한 번 쓰인 발화는 다음 라운드에
            # '이미 반응함'으로 표시돼 1명 정도만 이어서 반응한다.
            if stt_enabled:
                user_speech_buffer = [
                    e for e in user_speech_buffer if e.get("rounds", 0) < MAX_SPEECH_ROUNDS
                ]

            if chats:
                log.info(f"채팅 생성 {len(chats)}개")
                for c in chats:
                    chat_log.info(f"  {c['name']}: {c['message']}")

                chat_history.extend(chats)
                context_window = config["chat"]["context_window"]
                if len(chat_history) > context_window:
                    chat_history = chat_history[-context_window:]

                # 발화 즉시 반응은 빨리 떠야 의미가 있다 — 간격을 짧게.
                d_min, d_max = ((0.4, min(3.0, hype_profile.delay_max)) if speech_round
                                else (hype_profile.delay_min, hype_profile.delay_max))
                t = threading.Thread(
                    target=schedule_chats,
                    args=(
                        chats, batch, ui_queue,
                        d_min, d_max,
                        mentioned_names, hype_profile.skip_relief,
                    ),
                    kwargs={"round_id": round_num},
                    daemon=True,
                )
                t.start()
            else:
                ui_queue.put({"system": True, "type": "typing", "round": round_num, "on": False})
            typing_round = None

        except multiprocessing.queues.Empty:
            pass  # 타임아웃은 정상 — 다음 루프 계속
        except Exception as e:
            log.error(f"AI 프로세스 오류: {e}", exc_info=True)
            if typing_round is not None:
                ui_queue.put({"system": True, "type": "typing", "round": typing_round, "on": False})
                typing_round = None


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


def _normalize_for_match(s: str) -> str:
    """이름 비교용 정규화: 괄호 주석 제거, 소문자, 공백/기호 제거.

    예: "CrossCode (크로스코드)" → "crosscode",
        "Stellaris - Steam" → "stellaris".
    """
    import re
    s = s or ""
    s = re.sub(r"\([^)]*\)", "", s)          # 괄호 주석 제거 (한국어 병기 등)
    s = re.sub(r"[^0-9a-z가-힣]", "", s.lower())  # 영문/숫자/한글만 남김
    return s


def _profile_name_mismatch(window_title: str, profile_name: str) -> bool:
    """캡처 창에서 추출한 게임명과 적용된 프로필 이름이 '많이 다르면' True.

    재시작 복원/자동 감지가 엇나가 엉뚱한 프로필이 적용된 경우(예: 창은
    Stellaris 인데 프로필은 CrossCode)를 사용자에게 경고하기 위한 휴리스틱.
    한쪽이 다른 쪽에 포함되면 일치로 보고, 아니면 유사도로 판정한다.
    """
    import difflib

    win = _normalize_for_match(_extract_game_name(window_title))
    prof = _normalize_for_match(profile_name)
    # 비교 불가하거나 자동 감지 실패 표식이면 경고하지 않는다(오탐 방지).
    if not win or not prof or win == "unknowngame":
        return False
    if win in prof or prof in win:
        return False
    ratio = difflib.SequenceMatcher(None, win, prof).ratio()
    return ratio < 0.5


# ── STT 프로세스 ─────────────────────────────────────────────────────────────

def stt_process(
    stt_queue: multiprocessing.Queue,
    settings_queue: multiprocessing.Queue,
    config: dict,
    api_key: str,
    openai_api_key: str | None = None,
    ui_queue: "multiprocessing.Queue | None" = None,
):
    setup_logger("solo_stream")
    setup_logger("solo_stream.stt")
    log = logging.getLogger("solo_stream.stt")

    from src.stt import STTPipeline, UtteranceEvent
    from src.stt.transcriber import create_transcriber
    from src.utils import usage
    if ui_queue is not None:
        usage.set_sink(usage.UsageMeter(on_change=lambda snap: _send_usage(ui_queue, "stt", snap)).add)

    stt_cfg = config.get("stt", {}) or {}
    sample_rate = int(stt_cfg.get("sample_rate", 16000))

    provider = str(stt_cfg.get("provider", "gemini")).lower()
    model = stt_cfg.get("model", "gemini-2.5-flash")
    # 기본값은 OpenAI(gpt-transcribe). 고른 쪽 키가 없으면 STT 를 죽이지 않고
    # 있는 쪽(OpenAI ↔ Gemini)으로 대체하고 채팅창에 한 번 알린다. (main 은 둘 중 하나라도
    # 있어야 STT 프로세스를 띄운다.)
    if provider == "gemini" and not api_key and openai_api_key:
        model = "gpt-transcribe"
        log.warning("Gemini 키가 없어 STT 를 OpenAI(gpt-transcribe)로 대체합니다")
        provider = "openai"
    if provider == "openai" and not openai_api_key and api_key:
        model = stt_cfg.get("fallback_model", "gemini-2.5-flash")
        log.warning(f"OpenAI 키가 없어 STT 를 Gemini({model})로 대체합니다")
        provider = "gemini"
        if ui_queue is not None:
            try:
                ui_queue.put_nowait({
                    "name": "시스템",
                    "message": ("ℹ OpenAI 키가 없어 음성 인식을 Gemini로 대체합니다. "
                                "정확도가 낮을 수 있어요 — 설정 'AI 모델 · 키'에서 OpenAI 키를 추가하세요."),
                })
            except Exception:
                pass
    # 받아쓰기 힌트에 게임 고유명사(지명·적·기술, 프로필 stt_terms)를 붙여 오인식을 줄인다
    # ("시즈카"→"시즈 카로", "에밀리네이터"→"에뮬레이터"). 게임 프로필이 바뀌면 다시 만든다.
    from src.chat.flow import stt_hint_terms, stt_prompt_with_terms

    def _stt_prompt_for(game: str | None) -> str:
        if not game:
            return stt_cfg.get("transcribe_prompt") or ""
        try:
            from src.chat.game_profile_loader import load_profile
            terms = stt_hint_terms(load_profile(game))
        except Exception as e:
            log.warning(f"STT 힌트용 프로필 로드 실패: {e}")
            terms = []
        log.info(f"STT 고유명사 힌트 {len(terms)}개 ({game})")
        return stt_prompt_with_terms(stt_cfg.get("transcribe_prompt"), terms)

    cap_cfg = config.get("capture", {}) or {}
    stt_game = cap_cfg.get("game_name") or _extract_game_name(cap_cfg.get("target_window") or "")
    transcriber = create_transcriber(
        provider,
        gemini_api_key=api_key,
        openai_api_key=openai_api_key,
        model=model,
        language=stt_cfg.get("language", "ko"),
        prompt=_stt_prompt_for(stt_game),
        normalize_target_level=float(stt_cfg.get("normalize_target_level", 0.1)),
        normalize_percentile=float(stt_cfg.get("normalize_percentile", 95.0)),
        normalize_max_gain=float(stt_cfg.get("normalize_max_gain", 20.0)),
    )
    log.info(f"STT transcriber provider={provider} model={model}")

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
        # 받아쓰기 결과를 오버레이에도 흘려보낸다(채팅창 인라인 회색 표시용).
        # 항상 push하고 표시 여부는 UI 설정(ui.show_stt_history)에서 판단한다 —
        # 텍스트라 가볍고, 우클릭 토글로 런타임에 바뀌어도 STT 프로세스는 신경 안 써도 됨.
        if ui_queue is not None and ev.text:
            try:
                ui_queue.put_nowait({"stt_transcript": True, "text": ev.text})
            except Exception:
                pass

    chat_active = False
    started = False

    while True:
        # 설정 변경 수신
        while not settings_queue.empty():
            setting = settings_queue.get_nowait()
            if "chat_active" in setting:
                chat_active = setting["chat_active"]
                log.info(f"STT {'시작' if chat_active else '일시정지'}")
            elif "game_name" in setting and hasattr(transcriber, "set_prompt"):
                transcriber.set_prompt(_stt_prompt_for(setting["game_name"]))

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
    onboarding: bool = False,
):
    from src.ui.overlay import run_overlay
    run_overlay(config, ui_queue, settings_queue, onboarding=onboarding)


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    # 최초 실행 시딩: 사용자 데이터 폴더(%APPDATA%\SoloStream) 생성 +
    # 번들 기본 config.yaml / personas/ 복사. DEV 모드면 프로젝트 루트라 no-op.
    from src.utils import paths
    paths.ensure_user_data()

    config = load_config()

    # 마지막 세션에 적용한 창/게임명을 복원한다 (config.yaml 의 하드코딩 기본값보다 우선).
    # 이게 없으면 재시작마다 config 기본 target_window(예: CrossCode)로 폴백한다.
    # → 캡처 대상과 게임 프로필이 모두 마지막 선택으로 복원되도록 config 에 주입한다.
    last_session = paths.load_last_session()
    if last_session.get("target_window"):
        config.setdefault("capture", {})["target_window"] = last_session["target_window"]
    if last_session.get("game_name"):
        # ai_process 가 target_window 추출 대신 이 값을 우선 사용한다.
        config.setdefault("capture", {})["game_name"] = last_session["game_name"]
    if last_session:
        logger.info(
            f"마지막 세션 복원: 창='{last_session.get('target_window', '(없음)')}' "
            f"게임='{last_session.get('game_name', '(자동)')}'"
        )

    api_key = get_api_key(config)
    claude_api_key = get_claude_api_key(config)
    openai_api_key = get_openai_api_key(config)
    logger.info("Solo Stream 시작")

    # 키 미설정 시 graceful degrade: AI/STT 프로세스를 띄우지 않고 UI(오버레이)만 시작해
    # 첫 실행 온보딩(API 키 입력)을 노출한다. frozen 배포에서 콘솔 없이 조용히 죽던
    # ValueError 크래시를 대체. 키는 Gemini/OpenAI/Claude 중 아무거나 하나면 된다 —
    # 고른 모델의 키가 없으면 model_client.resolve_provider 가 있는 키로 대체한다.
    onboarding = not (api_key or openai_api_key or claude_api_key)
    if onboarding:
        logger.warning(
            "API 키가 하나도 없음 — AI/STT 비활성. 설정에서 키 입력 후 재시작하세요. "
            f"(키 저장 위치: {paths.user_data_dir() / '.env.local'})"
        )

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

    # 음성 인식은 OpenAI 나 Gemini 키가 있어야 한다(Claude 는 받아쓰기 없음).
    stt_enabled = (bool((config.get("stt") or {}).get("enabled", False)) and not onboarding
                   and bool(api_key or openai_api_key))
    if not onboarding and (config.get("stt") or {}).get("enabled") and not stt_enabled:
        logger.warning("음성 인식: OpenAI/Gemini 키가 없어 끕니다 (Claude 키만 있음)")
    stt_queue: multiprocessing.Queue | None = (
        multiprocessing.Queue(maxsize=200) if stt_enabled else None
    )

    # 온보딩(키 미설정) 모드면 AI/Capture/STT 를 띄우지 않는다 (키 없이 호출하면 인증 실패).
    # UI 만 시작해 키 입력을 받고, 사용자가 저장 후 재시작하면 정상 동작한다.
    processes: list[multiprocessing.Process] = []
    if not onboarding:
        processes = [
            multiprocessing.Process(
                target=capture_process,
                args=(image_queue, capture_settings_queue, config),
                name="CaptureProcess",
                daemon=True,
            ),
            multiprocessing.Process(
                target=ai_process,
                args=(image_queue, ui_queue, ai_settings_queue, config, api_key, claude_api_key, stt_queue,
                      openai_api_key),
                name="AIProcess",
                daemon=True,
            ),
        ]
        if stt_enabled:
            processes.append(multiprocessing.Process(
                target=stt_process,
                args=(stt_queue, stt_settings_queue, config, api_key, openai_api_key, ui_queue),
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
        ui_process(ui_queue, broadcast_queue, config, onboarding=onboarding)
    except KeyboardInterrupt:
        logger.info("종료 신호 수신")
    finally:
        for p in processes:
            p.terminate()
        logger.info("Solo Stream 종료")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
