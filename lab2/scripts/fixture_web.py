"""Local HTTP server for the fixed web corpus and the HTTP edge cases.

Routes (all GET):
  /p/<slug>[/][?utm...]   page from fixtures/web/pages (duplicates differ only by URL form)
  /go/<slug>              302 -> /p/<slug>
  /go2/<slug>             301 -> /go/<slug>  (redirect chain of two hops)
  /loop                   302 -> /loop       (redirect loop)
  /missing                404
  /flaky/<n>/<slug>       503 for the first n requests of this path, then the page
  /down                   always 503
  /slow                   sleeps SLOW_SECONDS (default 20) before answering
  /large                  ~3 MB HTML page
  /binary                 application/octet-stream

    python scripts/fixture_web.py --port 8765
"""
from __future__ import annotations

import argparse
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PAGES = Path(__file__).resolve().parent.parent / "fixtures" / "web" / "pages"
SLOW_SECONDS = 20.0


class Handler(BaseHTTPRequestHandler):
    flaky_counts: dict[str, int] = {}
    lock = threading.Lock()
    hits: list[str] = []

    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, code: int, body: bytes = b"", ctype: str = "text/html; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    def _page(self, slug: str):
        f = PAGES / f"{slug}.html"
        if not f.is_file():
            return self._send(404, b"<h1>404</h1>")
        return self._send(200, f.read_bytes())

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        with self.lock:
            self.hits.append(self.path)
        parts = [p for p in path.split("/") if p]
        if len(parts) == 2 and parts[0] == "p":
            return self._page(parts[1])
        if len(parts) == 2 and parts[0] == "go":
            return self._send(302, headers={"Location": f"/p/{parts[1]}"})
        if len(parts) == 2 and parts[0] == "go2":
            return self._send(301, headers={"Location": f"/go/{parts[1]}"})
        if parts == ["loop"]:
            return self._send(302, headers={"Location": "/loop"})
        if parts == ["missing"]:
            return self._send(404, b"<h1>Not found</h1>")
        if len(parts) == 3 and parts[0] == "flaky":
            with self.lock:
                n = self.flaky_counts.get(path, 0)
                self.flaky_counts[path] = n + 1
            if n < int(parts[1]):
                return self._send(503, b"<h1>Service Unavailable</h1>")
            return self._page(parts[2])
        if parts == ["down"]:
            return self._send(503, b"<h1>Service Unavailable</h1>")
        if parts == ["slow"]:
            time.sleep(SLOW_SECONDS)
            return self._page("compaction-1")
        if parts == ["large"]:
            para = "<p>" + "Обсуждение хранения состояния агентов. " * 50 + "</p>"
            body = ("<html><head><title>Архив обсуждений</title></head><body>" + para * 900 + "</body></html>").encode("utf-8")
            return self._send(200, body)
        if parts == ["binary"]:
            return self._send(200, b"SQLite format 3\x00" + bytes(range(256)) * 40, ctype="application/octet-stream")
        return self._send(404, b"<h1>404</h1>")


def start(port: int = 0, host: str = "127.0.0.1") -> tuple[ThreadingHTTPServer, str]:
    """Start the server in a daemon thread; returns (server, base_url)."""
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://{host}:{srv.server_address[1]}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print(f"fixture web server on http://{args.host}:{args.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
