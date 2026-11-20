"""Local HTTP fixture server for the web_fetch demo (guard session of run_all_demos.ps1).

    python scripts/http_fixture_server.py [--port 18765] [--slow 20]

Routes (127.0.0.1 only):
  /page.html   200, HTML page with a <title>        -> web_fetch success (HTML -> text)
  /notes.txt   200, plain UTF-8 text                -> web_fetch success (text)
  /missing     404                                  -> web_fetch http_error
  /slow        answers after --slow seconds         -> web_fetch timeout (WEB_TIMEOUT_S=10 < 20)
Every request is printed as one line "<time> <path> <status>" (the demo script saves it to results/).
"""
import argparse
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Справка DocSearch: поиск по документации</title>
<script>console.log("скрипты в текст не попадают")</script></head>
<body><h1>Справка DocSearch</h1><p>DocSearch ищет по Markdown-документации курса и возвращает путь к файлу и строки.</p>
<p>Индекс хранится в Elasticsearch, ранжирование BM25.</p></body></html>"""
NOTES = "Заметки стенда: локальный HTTP-сервер для проверки web_fetch.\nСтрока 2.\n"


def make_handler(slow_s: float):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: str, ctype: str) -> None:
            data = body.encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # the client gave up (timeout case)
            print(f"{datetime.now().isoformat(timespec='seconds')} {self.path} {code}", flush=True)

        def do_GET(self):
            if self.path == "/page.html":
                self._send(200, PAGE, "text/html; charset=utf-8")
            elif self.path == "/notes.txt":
                self._send(200, NOTES, "text/plain; charset=utf-8")
            elif self.path == "/slow":
                print(f"{datetime.now().isoformat(timespec='seconds')} /slow received, answering in {slow_s:g}s",
                      flush=True)
                time.sleep(slow_s)
                self._send(200, "слишком поздно", "text/plain; charset=utf-8")
            elif self.path == "/health":
                self._send(200, "ok", "text/plain")
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")
    return Handler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18765)
    ap.add_argument("--slow", type=float, default=20.0)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.slow))
    srv.daemon_threads = True
    print(f"{datetime.now().isoformat(timespec='seconds')} fixture server on http://127.0.0.1:{a.port}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
