"""외부 채팅 출력 — WebSocket 브로드캐스트.

solo_stream 의 채팅은 원래 ui_queue 를 통해 PyQt 오버레이에만 흐른다. 이 모듈은
그 채팅을 외부(예: project_ns 의 AI 스트리머)로도 내보내는 **부가 출력**이다.

원칙:
  - **기본 비활성.** config 의 export.websocket.enabled 가 true 일 때만 동작한다.
  - **오버레이를 침범하지 않는다.** 브로드캐스트 실패는 조용히 삼킨다. 외부
    소비자가 없거나 죽어도 방송(오버레이)은 그대로 돌아야 한다.
  - **비침습.** schedule_chats 의 ui_queue.put(chat) 뒤에 broadcast(chat) 한 줄만
    붙는다. 끄면 그 한 줄은 즉시 return 하는 no-op 이 된다.

schedule_chats 는 동기 스레드 코드다. 그래서 여기서는 전용 데몬 스레드에 asyncio
이벤트 루프를 하나 띄우고 그 안에서 WS 서버를 돌린다. broadcast() 는 아무 스레드
에서나 불러도 안전하다(run_coroutine_threadsafe 로 그 루프에 넘긴다).

와이어 포맷은 solo_stream 의 chat dict 를 그대로 JSON 으로 실은 것이다
(`{"name": ..., "message": ...}`, 그 외 필드는 소비자가 무시). project_ns 의
SoloStreamSource 가 이 포맷에 맞춰져 있다.
"""
import asyncio
import json
import logging
import threading
from typing import Any

log = logging.getLogger("solo_stream.export")

# AI 프로세스마다 하나. spawn 방식이라 프로세스별로 새로 초기화된다.
_active: "ChatBroadcaster | None" = None


class ChatBroadcaster:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: set = set()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        """전용 데몬 스레드에서 이벤트 루프 + WS 서버를 띄운다. 서버 기동까지 기다린다."""
        self._thread = threading.Thread(target=self._run, name="ws-export", daemon=True)
        self._thread.start()
        # 서버가 실제로 listen 을 시작할 때까지 잠깐 기다린다(최대 5초). 실패해도
        # 방송은 진행돼야 하므로 예외로 죽이지 않고 경고만 남긴다.
        if not self._ready.wait(timeout=5.0):
            log.warning("WS 익스포트 서버 기동 확인 실패(계속 진행). host=%s port=%s", self.host, self.port)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
            loop.run_forever()
        except Exception:
            log.exception("WS 익스포트 이벤트 루프 종료")
        finally:
            loop.close()

    async def _serve(self) -> None:
        import websockets

        async def handler(ws):
            # 소비자는 받기만 한다. 연결을 등록해두고, 끊길 때까지 유지한다.
            self._clients.add(ws)
            peer = getattr(ws, "remote_address", "?")
            log.info("WS 익스포트 소비자 연결: %s (총 %d)", peer, len(self._clients))
            try:
                await ws.wait_closed()
            finally:
                self._clients.discard(ws)
                log.info("WS 익스포트 소비자 해제: %s (총 %d)", peer, len(self._clients))

        await websockets.serve(handler, self.host, self.port)
        log.info("WS 익스포트 서버 시작: ws://%s:%d", self.host, self.port)
        self._ready.set()

    def broadcast(self, chat: dict[str, Any]) -> None:
        """chat dict 를 연결된 모든 소비자에게 보낸다. 스레드 안전, 실패는 삼킨다."""
        loop = self._loop
        if loop is None or not self._clients:
            return
        try:
            payload = json.dumps(chat, ensure_ascii=False)
        except (TypeError, ValueError):
            log.debug("직렬화 불가한 chat 스킵: %r", chat)
            return
        # 이벤트 루프 스레드에 실제 전송을 위임한다. 결과는 기다리지 않는다
        # (fire-and-forget) — 소비자 지연이 채팅 스케줄링을 막으면 안 된다.
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._send_all(payload)))

    async def _send_all(self, payload: str) -> None:
        for ws in list(self._clients):
            try:
                await ws.send(payload)
            except Exception:
                self._clients.discard(ws)  # 죽은 연결은 조용히 버린다


def start_broadcaster(config: dict) -> "ChatBroadcaster | None":
    """config 에 따라 브로드캐스터를 시작한다. 비활성이면 None(그리고 no-op).

    AI 프로세스 시작 시 한 번 호출한다.
    """
    global _active
    ws_cfg = ((config.get("export") or {}).get("websocket") or {})
    if not ws_cfg.get("enabled", False):
        _active = None
        return None
    _active = ChatBroadcaster(
        host=ws_cfg.get("host", "127.0.0.1"),
        port=int(ws_cfg.get("port", 8765)),
    )
    _active.start()
    return _active


def broadcast(chat: dict[str, Any]) -> None:
    """활성 브로드캐스터가 있으면 채팅을 내보낸다. 없으면 즉시 return.

    schedule_chats 의 ui_queue.put(chat) 뒤에서 호출된다.
    """
    if _active is not None:
        _active.broadcast(chat)
