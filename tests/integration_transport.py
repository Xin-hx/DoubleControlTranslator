"""Real loopback HTTP tests for proxy bypass, redirect refusal, and key containment."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from double_ctrl_translator.model import TranslationError, stream_translate


hits = {"primary": [], "redirect": [], "proxy": []}


def read_request_body(handler: BaseHTTPRequestHandler) -> None:
    length = int(handler.headers.get("Content-Length", "0"))
    if length:
        handler.rfile.read(length)


class RedirectTarget(BaseHTTPRequestHandler):
    def do_GET(self): hits["redirect"].append(dict(self.headers)); self.send_response(204); self.end_headers()
    def do_POST(self): read_request_body(self); self.do_GET()
    def log_message(self, *_): pass


class FakeProxy(BaseHTTPRequestHandler):
    def do_POST(self): read_request_body(self); hits["proxy"].append(dict(self.headers)); self.send_response(502); self.end_headers()
    def log_message(self, *_): pass


class Primary(BaseHTTPRequestHandler):
    redirect_port = 0
    def do_POST(self):
        read_request_body(self)
        hits["primary"].append(dict(self.headers))
        self.send_response(302)
        self.send_header("Location", f"http://127.0.0.1:{self.redirect_port}/stolen")
        self.end_headers()
        self.wfile.write(b"SECRET_ERROR_BODY_MUST_NOT_SURFACE")
    def log_message(self, *_): pass


class SlowStream(BaseHTTPRequestHandler):
    def do_POST(self):
        read_request_body(self)
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        try:
            self.wfile.write(b"data:"); self.wfile.flush()
            time.sleep(3)
        except (BrokenPipeError, ConnectionResetError):
            pass
    def log_message(self, *_): pass


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


redirect = serve(RedirectTarget)
proxy = serve(FakeProxy)
Primary.redirect_port = redirect.server_port
primary = serve(Primary)
slow = serve(SlowStream)
old_proxy = os.environ.get("HTTP_PROXY")
os.environ["HTTP_PROXY"] = f"http://127.0.0.1:{proxy.server_port}"
error = ""
try:
    try:
        stream_translate("public test", {"base_url": f"http://127.0.0.1:{primary.server_port}/v1",
                                         "api_key": "containment-test-key"},
                         "m", lambda _: None, threading.Event(), 2, 2, 4)
    except TranslationError as exc:
        error = str(exc)
    assert "HTTP 302" in error, error
    assert "SECRET_ERROR_BODY" not in error
    assert len(hits["primary"]) == 1
    assert hits["primary"][0].get("Authorization") == "Bearer containment-test-key"
    assert hits["redirect"] == [], hits["redirect"]
    assert hits["proxy"] == [], hits["proxy"]
    started = time.monotonic()
    try:
        stream_translate("deadline", {"base_url": f"http://127.0.0.1:{slow.server_port}/v1"},
                         "m", lambda _: None, threading.Event(), 10, 2, .35)
        raise AssertionError("slow drip exceeded total deadline without failure")
    except TranslationError as exc:
        assert "总时限" in str(exc), str(exc)
    deadline_elapsed = time.monotonic() - started
    assert deadline_elapsed < 1.5, deadline_elapsed
    cancel = threading.Event(); threading.Timer(.2, cancel.set).start(); started = time.monotonic()
    try:
        stream_translate("cancel", {"base_url": f"http://127.0.0.1:{slow.server_port}/v1"},
                         "m", lambda _: None, cancel, 10, 2, 10)
        raise AssertionError("blocked read ignored cancellation")
    except TranslationError as exc:
        assert "取消" in str(exc), str(exc)
    cancel_elapsed = time.monotonic() - started
    assert cancel_elapsed < 1.5, cancel_elapsed
    print(json.dumps({"ok": True, "primary_requests": 1,
                      "redirect_requests": 0, "proxy_requests": 0,
                      "error_body_hidden": True,
                      "hard_deadline_s": round(deadline_elapsed, 3),
                      "blocked_read_cancel_s": round(cancel_elapsed, 3)}))
finally:
    if old_proxy is None: os.environ.pop("HTTP_PROXY", None)
    else: os.environ["HTTP_PROXY"] = old_proxy
    for server in (primary, redirect, proxy, slow):
        server.shutdown(); server.server_close()
