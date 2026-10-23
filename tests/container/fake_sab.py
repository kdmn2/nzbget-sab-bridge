"""Minimal isolated SAB API used only for container packaging checks."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


class Handler(BaseHTTPRequestHandler):
    server_version = "fake-sab/packaging"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        form = {key: values[0] for key, values in parse_qs(body.decode()).items()}
        mode = form.get("mode", "")
        if mode == "version":
            payload: dict[str, object] = {"version": "5.1.3"}
        elif mode == "get_cats":
            payload = {"categories": ["*", "tv"]}
        elif mode == "queue":
            payload = {
                "queue": {
                    "status": "Idle",
                    "paused": False,
                    "slots": [],
                    "noofslots": 0,
                    "noofslots_total": 0,
                    "mb": "0.00",
                    "mbleft": "0.00",
                    "speed": "0 ",
                    "kbpersec": "0.00",
                    "version": "5.1.3",
                }
            }
        elif mode == "status":
            payload = {
                "status": {
                    "version": "5.1.3",
                    "paused": False,
                    "speedlimit": "0",
                    "speedlimit_abs": "0",
                    "servers": [],
                }
            }
        elif mode == "server_stats":
            payload = {"day": 0, "week": 0, "month": 0, "total": 0, "servers": {}}
        else:
            payload = {"status": True}
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", 18080), Handler).serve_forever()


if __name__ == "__main__":
    main()
