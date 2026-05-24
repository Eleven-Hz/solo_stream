"""SettingsPanel 미리보기 — 5초 동안 창에 띄움."""
from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from src.utils.config_loader import load_config
from src.ui.overlay import SettingsPanel


def main():
    duration_ms = int(float(sys.argv[1]) * 1000) if len(sys.argv) > 1 else 5000
    app = QApplication.instance() or QApplication(sys.argv)
    config = load_config()
    settings_queue: multiprocessing.Queue = multiprocessing.Queue()

    panel = SettingsPanel(settings_queue, config)
    panel.resize(420, 700)
    panel.setWindowTitle("Settings Preview")
    panel.show()

    QTimer.singleShot(duration_ms, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
