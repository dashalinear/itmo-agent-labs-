"""web_fetch against a local HTTP fixture server: text, HTML, redirect, HTTP error, timeout, size."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from organizer.results import run_tool
from organizer.tools_web import WebTools


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/text":
            self._send(200, "Привет, мир".encode())
        elif self.path == "/html":
            self._send(200, b"<html><head><title>T</title><script>x()</script></head><body><h1>Head</h1><p>Para</p></body></html>",
                       "text/html; charset=utf-8")
        elif self.path == "/redirect":
            self._send(302, b"", extra={"Location": "/text"})
        elif self.path == "/missing":
            self._send(404, b"not found")
        elif self.path == "/slow":
            time.sleep(2)
            self._send(200, b"late")
        elif self.path == "/big":
            self._send(200, b"a" * 50_000)
        elif self.path == "/bin":
            self._send(200, b"\x00\x01", "application/octet-stream")
        else:
            self._send(500, b"err")


@pytest.fixture(scope="module")
def base():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def fetch(url, **kw):
    w = WebTools(timeout_s=kw.get("timeout", 1.0), max_bytes=kw.get("max_bytes", 10_000), max_chars=kw.get("max_chars", 5000))
    return json.loads(run_tool("web_fetch", w.web_fetch, url))


def test_text(base):
    r = fetch(base + "/text")
    assert r["ok"] and r["data"]["status"] == 200 and r["data"]["text"] == "Привет, мир" and not r["data"]["truncated"]


def test_html_converted(base):
    d = fetch(base + "/html")["data"]
    assert d["title"] == "T" and "Head" in d["text"] and "Para" in d["text"] and "x()" not in d["text"]


def test_redirect_final_url(base):
    d = fetch(base + "/redirect")["data"]
    assert d["url"].endswith("/redirect") and d["final_url"].endswith("/text") and d["redirects"]


def test_http_error(base):
    r = fetch(base + "/missing")
    assert r["ok"] is False and r["error"]["type"] == "http_error" and r["error"]["status"] == 404


def test_timeout(base):
    r = fetch(base + "/slow", timeout=0.5)
    assert r["ok"] is False and r["error"]["type"] == "timeout"


def test_size_limit(base):
    d = fetch(base + "/big", max_bytes=1000)["data"]
    assert d["truncated"] and d["bytes_read"] == 1000


def test_binary_and_bad_url(base):
    assert fetch(base + "/bin")["error"]["type"] == "unsupported_content_type"
    assert fetch("file:///etc/passwd")["error"]["type"] == "invalid_url"
    assert fetch("http://127.0.0.1:1/")["error"]["type"] in ("network_error", "timeout")
