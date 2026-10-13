"""설정 창의 백그라운드 작업(QThread 워커). UI 스레드를 막지 않도록 분리한다."""
from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, pyqtSignal


class MicLevelWorker(QObject):
    """마이크 입력 레벨을 측정하는 워커 (UI 프로세스 내 독립 스트림).

    STT 프로세스와 무관하게 sounddevice InputStream 만 잠깐 열어 RMS 레벨을
    계산해 시그널로 흘린다. silero-vad 로딩 없이 가벼워서 '내 목소리가 잡히나'를
    시작 전에도 즉시 확인할 수 있다.
    """
    level = pyqtSignal(float)   # 0.0 ~ 1.0 정규화 레벨
    error = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, device, sample_rate: int = 16000):
        super().__init__()
        self._device = device
        self._sample_rate = sample_rate
        self._running = False

    def stop(self):
        self._running = False

    def run(self):
        try:
            import numpy as np
            import sounddevice as sd
        except Exception as e:
            self.error.emit(f"오디오 라이브러리 로드 실패: {e}")
            self.stopped.emit()
            return
        block = 1024
        try:
            stream = sd.InputStream(samplerate=self._sample_rate, channels=1, dtype="float32",
                                    device=self._device, blocksize=block)
        except Exception as e:
            self.error.emit(f"마이크 열기 실패: {e}")
            self.stopped.emit()
            return
        self._running = True
        try:
            with stream:
                while self._running:
                    try:
                        data, _ = stream.read(block)
                    except Exception as e:
                        self.error.emit(f"마이크 읽기 오류: {e}")
                        break
                    pcm = data.reshape(-1)
                    if pcm.size == 0:
                        continue
                    # 말소리 RMS 가 보통 0.02~0.2 라 ×8 로 키워 막대가 의미 있게 움직이게 한다.
                    rms = float(np.sqrt(np.mean(np.square(pcm))))
                    self.level.emit(min(rms * 8.0, 1.0))
        except Exception as e:
            self.error.emit(f"마이크 스트림 오류: {e}")
        finally:
            self._running = False
            self.stopped.emit()


class STTTestWorker(QObject):
    """선택한 장치로 N초 녹음 후 지정 provider/model 로 받아쓰기하는 워커.

    실제 STTPipeline/VAD 없이 단발 녹음 → create_transcriber().transcribe() 로
    '받아쓰기가 되는지'를 확인한다. API 키·요금이 실제로 사용된다.
    """
    progress = pyqtSignal(str)
    finished = pyqtSignal(str, float)       # (transcript, elapsed_s)
    error = pyqtSignal(str)

    def __init__(self, *, device, sample_rate, record_seconds, provider, model, language, prompt,
                 gemini_key, openai_key, normalize_target_level, normalize_percentile, normalize_max_gain):
        super().__init__()
        self._device = device
        self._sample_rate = sample_rate
        self._record_seconds = record_seconds
        self._provider = provider
        self._model = model
        self._language = language
        self._prompt = prompt
        self._gemini_key = gemini_key
        self._openai_key = openai_key
        self._norm = (normalize_target_level, normalize_percentile, normalize_max_gain)

    def run(self):
        try:
            import numpy as np
            import sounddevice as sd
            from src.stt.transcriber import create_transcriber
        except Exception as e:
            self.error.emit(f"라이브러리 로드 실패: {e}")
            return
        self.progress.emit("녹음 중… 지금 말하세요")
        sr = self._sample_rate
        try:
            rec = sd.rec(int(sr * self._record_seconds), samplerate=sr, channels=1,
                         dtype="float32", device=self._device)
            sd.wait()
        except Exception as e:
            self.error.emit(f"녹음 실패: {e}")
            return
        pcm = np.asarray(rec, dtype=np.float32).reshape(-1)
        if pcm.size == 0:
            self.error.emit("녹음된 오디오가 없습니다. 입력 장치를 확인하세요.")
            return
        self.progress.emit("받아쓰는 중…")
        try:
            transcriber = create_transcriber(
                self._provider,
                gemini_api_key=self._gemini_key,
                openai_api_key=self._openai_key,
                model=self._model,
                language=self._language,
                prompt=self._prompt or None,
                normalize_target_level=self._norm[0],
                normalize_percentile=self._norm[1],
                normalize_max_gain=self._norm[2],
            )
            text, elapsed = transcriber.transcribe(pcm, sr)
        except Exception as e:
            self.error.emit(f"받아쓰기 실패: {e}")
            return
        self.finished.emit(text, elapsed)


class KeyCheckWorker(QObject):
    """API 키 연결 확인(모델 목록 조회, 과금 없음)."""
    done = pyqtSignal(str, bool, str)    # (provider, ok, message)

    def __init__(self, provider: str, key: str):
        super().__init__()
        self._provider = provider
        self._key = key

    def run(self):
        try:
            if self._provider == "openai":
                from src.stt.transcriber import check_openai_key
                ok, msg = check_openai_key(self._key)
            else:
                from src.ai.model_client import check_api_key
                ok, msg = check_api_key(self._provider, self._key)
        except Exception as e:
            ok, msg = False, str(e)[:120]
        self.done.emit(self._provider, ok, msg)


class CapturePreviewWorker(QObject):
    """캡처 프로세스와 같은 방식(mss)으로 대상 창을 한 장 찍는다(오버레이 제외 확인용)."""
    done = pyqtSignal(bytes, str)        # (jpeg bytes, 설명)
    error = pyqtSignal(str)

    def __init__(self, target_window: str | None):
        super().__init__()
        self._target = target_window

    def run(self):
        try:
            from src.capture.screen import capture_screen
            data, desc = capture_screen(target_window=self._target or None, resize_width=640)
            self.done.emit(data, desc)
        except Exception as e:
            self.error.emit(f"캡처 실패: {e}")


def start_worker(owner: QObject, worker: QObject) -> QThread:
    """worker.run 을 새 QThread 에서 실행. 끝나면 스레드는 스스로 정리된다.

    호출자는 반환된 스레드와 worker 참조를 보관해야 한다(GC 방지).
    """
    thread = QThread(owner)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    for sig_name in ("finished", "error", "done", "stopped"):
        sig = getattr(worker, sig_name, None)
        if sig is not None:
            sig.connect(thread.quit)
    thread.start()
    return thread
