"""Isolated fake NZBGet backend for container smoke tests only.

It never contacts a live service and returns metadata rather than credentials.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    server_version = "fake-nzbget/phase2"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", "backend-cookie=must-not-be-forwarded")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def _route(self) -> None:
        body = self._read_body()
        query = self.path.partition("?")[2]
        if "redirect=1" in query:
            self.send_response(302)
            self.send_header("Location", "http://redirect.invalid/?apikey=must-not-leak")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if "sleep=2" in query:
            time.sleep(2)
        if self.path.startswith("/xmlrpc"):
            self._send(200, body or b"<ok/>", "text/xml")
            return
        if b'"method":"version"' in body or b'"method": "version"' in body:
            payload = {
                "version": "1.1",
                "id": 1,
                "result": "26.3",
                "backend_auth_received": bool(self.headers.get("Authorization")),
                "client_cookie_received": bool(self.headers.get("Cookie")),
            }
            self._send(200, json.dumps(payload).encode(), "application/json")
            return
        self._send(200, body or b"{}", "application/json")


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 18080), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
