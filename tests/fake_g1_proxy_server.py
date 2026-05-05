from __future__ import annotations

import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


HOST = "127.0.0.1"
PORT = 9001


class FakeG1ProxyHandler(BaseHTTPRequestHandler):
    server_version = "FakeG1Proxy/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"_raw": raw.decode("utf-8", errors="replace")}

    def _print_request(self, body: Any) -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        print("=" * 72, flush=True)
        print(f"[{ts}] fake_g1_proxy request", flush=True)
        print(f"method: {self.command}", flush=True)
        print(f"path:   {self.path}", flush=True)
        print(f"json:   {json.dumps(body, ensure_ascii=False)}", flush=True)

    def _send_json(self, status: int = 200) -> None:
        payload = {
            "ok": True,
            "source": "fake_g1_proxy",
            "path": self.path,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        body = self._read_json_body()
        self._print_request(body)
        allowed = {
            ("GET", "/health"),
            ("GET", "/api/robot/status"),
            ("POST", "/api/robot/action"),
            ("POST", "/api/robot/loco"),
            ("POST", "/api/robot/stop"),
            ("POST", "/api/robot/speak"),
        }
        status = 200 if (self.command, self.path) in allowed else 404
        self._send_json(status=status)

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()


def main() -> None:
    httpd = HTTPServer((HOST, PORT), FakeG1ProxyHandler)
    print(f"fake_g1_proxy listening on http://{HOST}:{PORT}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nfake_g1_proxy stopped.", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
