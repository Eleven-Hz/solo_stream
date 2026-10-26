"""solo_stream 현황판 로컬 서버 — open-dashboard.bat 이 띄운다. http://127.0.0.1:8767/

열 때마다 build.py 로 새로 만들어(코드·검토 요청을 그때 읽는다) 보여 주고, 검토 요청 승인·반려를 받는다.
이 컴퓨터 안에서만 열린다(127.0.0.1). 다른 사이트에서 보낸 POST 는 Origin 을 보고 막는다.
포트 8765 는 채팅 WebSocket 출력(export.websocket)이 쓰므로 피한다.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402
import proposals  # noqa: E402

PORT = int(os.environ.get("SOLO_DASH_PORT") or 8767)
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes | str, ctype: str = "text/plain; charset=utf-8") -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def log_message(self, fmt, *args):  # 요청마다 찍히는 기본 로그는 끈다
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/":
                # build() 는 부를 때마다 파일·git 을 새로 읽는다
                return self._send(200, build.build(), "text/html; charset=utf-8")
            # 검토 요청 스크린샷: /proposals/<id>/<파일>
            m = re.fullmatch(r"/proposals/([^/]+)/([^/]+)", path)
            if m:
                pid, name = unquote(m.group(1)), unquote(m.group(2))
                ctype = TYPES.get(Path(name).suffix.lower())
                p = proposals.PROPOSAL_DIR / pid / name
                if not proposals.valid_id(pid) or not ctype or ".." in name or not p.is_file():
                    return self._send(404, "없음")
                return self._send(200, p.read_bytes(), ctype)
            self._send(404, "없음")
        except Exception as e:
            self._send(500, f"현황판을 만들지 못했습니다\n\n{e!r}")

    def do_POST(self):
        # 승인·반려: POST /api/proposals/<id>/approve|reject  {comment}
        m = re.fullmatch(r"/api/proposals/([^/]+)/(approve|reject)", urlparse(self.path).path)
        if not m:
            return self._send(404, "없음")
        origin = self.headers.get("Origin")
        if origin and origin not in ORIGINS:
            return self._send(403, "다른 사이트에서 온 요청")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 20000:
                raise ValueError("너무 큼")
            body = json.loads(self.rfile.read(length) or b"{}")
            comment = str(body.get("comment") or "")[:2000]
            pid, action = unquote(m.group(1)), m.group(2)
            p = (proposals.approve if action == "approve" else proposals.reject)(pid, comment)
            print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "승인" if action == "approve" else "반려", pid, comment, flush=True)
            self._json(200, {"ok": True, "status": p["status"]})
        except Exception as e:
            # 실패는 대개 충돌·이미 처리함(409). 화면이 error 문구를 그대로 보여 준다
            self._json(409, {"ok": False, "error": str(e)})


if __name__ == "__main__":
    # Windows 의 SO_REUSEADDR 은 이미 쓰는 포트에도 붙어 버린다 — 두 번째 실행이 '이미 켜져 있음'으로 끝나게 끈다
    ThreadingHTTPServer.allow_reuse_address = False
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"이미 켜져 있습니다: http://127.0.0.1:{PORT}/")
        sys.exit(0)
    print(f"solo_stream 현황판: http://127.0.0.1:{PORT}/  (이 창을 닫으면 꺼집니다)", flush=True)
    server.serve_forever()
