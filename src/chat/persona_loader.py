"""YAML 파일에서 봇 페르소나를 로딩한다."""
import logging
from pathlib import Path

import yaml

from src.utils import paths

logger = logging.getLogger("solo_stream.persona")

_VALID_WEIGHTS = {"off", "low", "medium", "high"}


def normalize_user_speech_weight(value, default: str = "medium") -> str:
    """YAML이 off를 False로 파싱하는 함정 대비 + 검증."""
    if value is False:
        return "off"
    if value is True:
        return "on" if "on" in _VALID_WEIGHTS else default
    if value is None:
        return default
    s = str(value).lower().strip()
    return s if s in _VALID_WEIGHTS else default


# 설정 창 '시청자 봇' 페이지가 쓰는 사용자 파일 — 다른 페르소나 파일을 모두 합친 뒤 맨 마지막에 덮는다.
UI_FILE = "settings_bots.yaml"
_UI_FILE_HEADER = (
    "# 설정 창 '시청자 봇' 페이지가 저장하는 파일입니다 — 직접 고치지 말고 설정 창에서 바꾸세요.\n"
    "# 기본 봇과 같은 name 이면 그 봇을 통째로 대체, disabled: true 면 끔, 새 name 이면 새 봇.\n"
)


def _load_bots_from_dir(dir_path: Path) -> list[dict]:
    """한 디렉터리의 모든 ``*.yaml``(설정 창 파일 ``UI_FILE`` 제외)에서 봇을 읽어 순서대로 모은다.

    파일 최상위에 ``disabled: true`` 가 있으면 그 파일 전체를 건너뛴다
    (예: 번들 ``default_bots.yaml`` 을 통째로 끄고 싶을 때).
    """
    bots: list[dict] = []
    if not dir_path or not dir_path.is_dir():
        return bots
    for yaml_file in sorted(dir_path.glob("*.yaml")):
        if yaml_file.name == UI_FILE:
            continue
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if data.get("disabled") is True:
            logger.info(f"페르소나 파일 비활성화(skip): {yaml_file.name}")
            continue
        file_bots = data.get("bots", []) or []
        bots.extend(file_bots)
        logger.info(f"페르소나 로딩: {yaml_file.name} ({len(file_bots)}명)")
    return bots


def _merge_bots(base: list[dict], override: list[dict]) -> list[dict]:
    """``name`` 기준으로 override 를 base 위에 덮는다.

    - 같은 ``name`` 이 override 에 있으면 그 봇을 **통째로 교체**(부분 병합 아님).
    - 봇에 ``disabled: true`` 가 있으면 그 ``name`` 을 결과에서 제외.
    - override 에만 있는 새 ``name`` 은 추가.
    - ``name`` 없는 항목은 키 충돌이 불가능하므로 그대로 추가.
    base 의 원래 순서를 보존하고, 새 봇은 뒤에 붙인다.
    """
    by_name: dict[str, dict] = {}
    order: list[str] = []          # name 있는 봇의 등장 순서
    nameless: list[dict] = [b for b in base if not b.get("name")]

    for bot in base:
        name = bot.get("name")
        if not name:
            continue
        if name not in by_name:
            order.append(name)
        by_name[name] = bot

    disabled: set[str] = set()
    for bot in override:
        name = bot.get("name")
        if not name:
            nameless.append(bot)
            continue
        if bot.get("disabled") is True:
            disabled.add(name)
            continue
        if name not in by_name:
            order.append(name)
        by_name[name] = bot

    merged = [by_name[n] for n in order if n not in disabled]
    merged.extend(nameless)
    return merged


def _ui_file(persona_dir: str) -> Path:
    base = Path(persona_dir)
    return base / UI_FILE if base.is_absolute() else paths.user_data_dir() / persona_dir / UI_FILE


def _load_ui_bots(persona_dir: str) -> list[dict]:
    f = _ui_file(persona_dir)
    if not f.is_file():
        return []
    with open(f, "r", encoding="utf-8") as fp:
        return list((yaml.safe_load(fp) or {}).get("bots") or [])


def _base_bots(persona_dir: str) -> list[dict]:
    """설정 창 파일을 빼고 합친 봇 — 번들 기본 + 사용자가 직접 둔 파일."""
    base_path = Path(persona_dir)
    if base_path.is_absolute():
        # 절대경로면 그 디렉터리만 로드(테스트/특수 케이스).
        return _load_bots_from_dir(base_path)
    bundled = _load_bots_from_dir(paths.resource_dir() / persona_dir)
    user_dir = paths.user_data_dir() / persona_dir
    # DEV 모드면 user_data == 프로젝트 루트라 bundled 와 같은 폴더 → 중복 병합 방지.
    if user_dir.resolve() == (paths.resource_dir() / persona_dir).resolve():
        return bundled
    return _merge_bots(bundled, _load_bots_from_dir(user_dir))


def load_personas(persona_dir: str) -> list[dict]:
    """봇 페르소나 전체 풀을 반환. 매 채팅 배치마다 main 이 여기서 샘플링한다.

    **번들 기본 + 사용자 오버라이드 + 설정 창 파일** 을 합친다.

    - 번들 ``resource_dir()/personas/*.yaml`` (읽기전용 기본, 항상 최신 → 앱
      업데이트로 추가된 봇/필드가 자동 반영됨) 을 베이스로 읽고,
    - 사용자 ``user_data_dir()/personas/*.yaml`` 을 그 위에 ``name`` 기준 병합한다.
    - 마지막으로 설정 창이 저장한 ``UI_FILE`` 을 덮는다(켜기/끄기·수정·새 봇).

    따라서 사용자는 **번들 기본 파일을 건드리지 않고**, user_data 의 personas/ 에
    자기 파일만 두면 된다.
      - 새 봇 추가: 자기 파일에 새 ``name`` 으로 정의 → 그대로 추가됨.
      - 기본 봇 수정: 같은 ``name`` 을 정의 → 그 봇을 통째로 대체.
      - 기본 봇 제외: 같은 ``name`` 에 ``disabled: true`` → 그 봇 제거.
      - 기본 전체 끄기: 파일 최상위에 ``disabled: true``(예: 번들과 같은
        ``default_bots.yaml`` 을 user_data 에 두고 그렇게 표시).
    """
    all_bots = _merge_bots(_base_bots(persona_dir), _load_ui_bots(persona_dir))
    if not all_bots:
        raise ValueError(f"페르소나 파일을 찾을 수 없습니다: {paths.resource_dir() / persona_dir}")
    return all_bots


def _is_edit(entry: dict) -> bool:
    """설정 창 파일 항목이 봇 내용을 담았는지(수정·새 봇) — name/disabled 만 있으면 켜기/끄기 표시."""
    return any(k not in ("name", "disabled") for k in entry)


def persona_entries(persona_dir: str) -> list[dict]:
    """설정 창 목록용. ``[{bot, builtin, edited, enabled}]`` — 기본 봇 순서 뒤에 설정 창에서 만든 봇.

    builtin: 기본(번들·직접 둔 파일) 봇 / edited: 설정 창에서 내용을 바꿈 / enabled: 켜져 있음.
    꺼진 봇도 다시 켤 수 있게 목록에 남긴다."""
    ui = {b["name"]: b for b in _load_ui_bots(persona_dir) if b.get("name")}
    out = []
    for b in _base_bots(persona_dir):
        if not b.get("name"):
            continue
        o = ui.pop(b["name"], None)
        edited = bool(o) and _is_edit(o)
        bot = {k: v for k, v in (o if edited else b).items() if k != "disabled"}
        out.append({"bot": bot, "builtin": True, "edited": edited, "enabled": not (o and o.get("disabled"))})
    for o in ui.values():
        if _is_edit(o):
            bot = {k: v for k, v in o.items() if k != "disabled"}
            out.append({"bot": bot, "builtin": False, "edited": False, "enabled": not o.get("disabled")})
    return out


def save_persona_entries(persona_dir: str, entries: list[dict]) -> Path:
    """``persona_entries`` 형식 목록을 설정 창 파일에 저장 — 기본값 그대로인 봇은 적지 않는다."""
    bots = []
    for e in entries:
        name = e["bot"].get("name")
        if e["builtin"] and not e["edited"]:
            if not e["enabled"]:
                bots.append({"name": name, "disabled": True})
            continue
        bot = dict(e["bot"])
        if not e["enabled"]:
            bot["disabled"] = True
        bots.append(bot)
    f = _ui_file(persona_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump({"bots": bots}, allow_unicode=True, sort_keys=False)
    f.write_text(_UI_FILE_HEADER + body, encoding="utf-8")
    logger.info(f"페르소나 저장: {f.name} ({len(bots)}개 항목)")
    return f
