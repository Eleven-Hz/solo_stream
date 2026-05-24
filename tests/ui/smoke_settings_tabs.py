"""SettingsPanel 탭 구조 smoke test.

QApplication을 띄우되 GUI는 잠깐만 보여주고 자동 종료한다.
탭 수, 각 탭의 핵심 위젯 존재 여부, STT 위젯 초기값 등을 확인.

사용:
  .\\venv\\Scripts\\python.exe tests\\ui\\smoke_settings_tabs.py
"""
from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication

from src.utils.config_loader import load_config
from src.ui.overlay import SettingsPanel


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    config = load_config()
    settings_queue: multiprocessing.Queue = multiprocessing.Queue()

    panel = SettingsPanel(settings_queue, config)
    panel.resize(420, 700)
    panel.show()

    assert hasattr(panel, "_tabs"), "탭 위젯이 만들어지지 않았습니다"
    tabs = panel._tabs
    assert tabs.count() == 3, f"탭 개수 3 기대, 실제 {tabs.count()}"
    expected = ["게임", "AI 모델", "음성"]
    actual = [tabs.tabText(i) for i in range(tabs.count())]
    assert actual == expected, f"탭 라벨 불일치: {actual}"
    print(f"탭 라벨 OK: {actual}")

    # 핵심 위젯 존재 확인
    for attr in [
        "_window_combo", "_exclude_list", "_profile_combo",   # 게임
        "_provider_combo", "_vision_model_combo",             # AI vision
        "_chat_provider_combo", "_chat_model_combo",          # AI chat
        "_gemini_key_input", "_claude_key_input",             # AI 키
        # STT
        "_stt_enabled_chk", "_stt_weight_combo", "_stt_lookback_spin",
        "_stt_min_chars_spin", "_stt_threshold_spin", "_stt_min_speech_ms_spin",
        "_stt_min_silence_ms_spin", "_stt_speech_pad_ms_spin", "_stt_max_utt_spin",
        "_stt_model_combo", "_stt_language_input", "_stt_device_input",
        "_stt_prompt_text",
    ]:
        assert hasattr(panel, attr), f"위젯 누락: {attr}"
    print(f"필수 위젯 존재 OK ({len(_)} 개)" if False else "필수 위젯 존재 OK")

    # STT 초기값이 config의 stt 섹션과 일치하는지
    stt_cfg = config.get("stt", {}) or {}
    assert panel._stt_enabled_chk.isChecked() == bool(stt_cfg.get("enabled", False))
    assert panel._stt_weight_combo.currentText() == str(stt_cfg.get("default_weight", "high")).lower()
    assert abs(panel._stt_lookback_spin.value() - float(stt_cfg.get("lookback_seconds", 20.0))) < 1e-6
    print(f"STT 초기값 OK: enabled={panel._stt_enabled_chk.isChecked()}, "
          f"weight={panel._stt_weight_combo.currentText()}, "
          f"lookback={panel._stt_lookback_spin.value()}")

    # 탭 전환 동작 확인
    for i in range(3):
        tabs.setCurrentIndex(i)
        cur = tabs.currentWidget()
        assert cur is not None, f"탭 {i} 위젯 없음"
    print("탭 전환 OK")

    # 0.5초 동안만 표시 후 종료
    from PyQt6.QtCore import QTimer
    QTimer.singleShot(500, app.quit)
    app.exec()
    print("smoke test PASSED")


if __name__ == "__main__":
    main()
