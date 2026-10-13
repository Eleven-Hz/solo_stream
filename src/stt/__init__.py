"""STT (음성 인식) 패키지.

다른 모듈에서 사용하는 진입점:
  from src.stt import STTPipeline, UtteranceEvent
"""
from src.stt.pipeline import STTPipeline, UtteranceEvent

__all__ = ["STTPipeline", "UtteranceEvent"]
