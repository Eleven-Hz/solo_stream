"""YAML 파일에서 봇 페르소나를 로딩한다."""
import logging
from pathlib import Path

import yaml

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


def load_personas(persona_dir: str) -> list[dict]:
    """
    persona_dir 안의 모든 YAML 파일에서 봇을 로딩해 전체 풀을 반환.
    매 채팅 배치마다 main에서 이 풀에서 랜덤 샘플링한다.
    """
    base_path = Path(persona_dir)
    if not base_path.is_absolute():
        # 프로젝트 루트 기준
        base_path = Path(__file__).parent.parent.parent / persona_dir

    all_bots = []
    for yaml_file in sorted(base_path.glob("*.yaml")):
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        bots = data.get("bots", [])
        all_bots.extend(bots)
        logger.info(f"페르소나 로딩: {yaml_file.name} ({len(bots)}명)")

    if not all_bots:
        raise ValueError(f"페르소나 파일을 찾을 수 없습니다: {base_path}")

    return all_bots
