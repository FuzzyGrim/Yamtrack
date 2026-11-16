"""Answer "starting up" on the public port while the entrypoint prepares the database.

On a release with migrations the entrypoint snapshots the database and migrates
it before Nginx exists, so for minutes nothing listens on the public port and a
proxy in front can only time out. This holds the port during that time with a
fast, honest 503 and a retry hint, and exits when the entrypoint hands the port
to Nginx. It uses only the standard library, like the recovery page, because
nothing else is ready to run.
"""

from __future__ import annotations

import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config.server_port import validate_port

RETRY_AFTER_SECONDS = 5
_SOCKET_TIMEOUT_SECONDS = 10
_PAGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    f'<meta http-equiv="refresh" content="{RETRY_AFTER_SECONDS}">'
    "<title>Floppy is starting</title>"
    "<style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;"
    "display:grid;place-items:center;min-height:100vh;margin:0;text-align:center}"
    "p{color:#aaa;max-width:22rem}</style></head><body><main>"
    "<h1>Floppy is starting</h1>"
    "<p>It is updating its database. This page refreshes on its own.</p>"
    "</main></body></html>"
).encode()


class _Handler(BaseHTTPRequestHandler):
    """Reply 503 to everything, including the health path."""

    timeout = _SOCKET_TIMEOUT_SECONDS
    protocol_version = "HTTP/1.1"

    def _reply(self) -> None:
        # Drain nothing: the connection is closed, so an unread body is harmless.
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.send_header("Retry-After", str(RETRY_AFTER_SECONDS))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(_PAGE)

    # BaseHTTPRequestHandler dispatches by these exact names.
    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _reply  # noqa: N815

    def log_message(self, *_args) -> None:
        """Stay quiet: a retrying client would otherwise fill the startup log."""


def serve(port: object) -> None:
    """Serve until SIGTERM/SIGINT, the entrypoint's signal that Nginx is next."""
    server = ThreadingHTTPServer(("", validate_port(port)), _Handler)
    server.daemon_threads = True
    stop = threading.Event()

    def _shutdown(*_args) -> None:
        # shutdown() blocks until serve_forever returns, so never call it here.
        stop.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Python runs signal handlers only on the main thread, and only when it
    # executes bytecode. The kernel may deliver SIGTERM to the serving thread
    # instead, and then an untimed wait never wakes to run the handler: the
    # server ignored the entrypoint's SIGTERM. Wake regularly so it cannot.
    while not stop.wait(0.2):
        pass
    server.shutdown()
    server.server_close()


if __name__ == "__main__":
    if len(sys.argv) != 2:  # noqa: PLR2004 - one positional argument: the port
        sys.exit("usage: python -m config.startup_server PORT")
    serve(sys.argv[1])
