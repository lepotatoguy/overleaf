"""Localhost-only HTTP API and dashboard. Every /api call needs the private token."""

import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import clients

HOST, PORT = "127.0.0.1", 8790
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
TOKEN_FILE = clients.STATE_DIR / "api.token"
UI_DIR = Path(__file__).parent / "ui"
MAX_BODY = 64 * 1024


def load_token():
    """Create the token on first use, readable only by the current user."""
    if not TOKEN_FILE.exists():
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    return TOKEN_FILE.read_text().strip()


def query_params(query):
    return dict(p.split("=", 1) for p in query.split("&") if "=" in p)


NONCE_TTL_S = 60


def cookie_value(header, name):
    for part in (header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


def make_handler(bridge, token):
    nonces = {}  # one-time codes handed to `olbridge open`, swapped for the session cookie

    def take_nonce(code):
        expires = nonces.pop(code, 0)
        return time.time() < expires

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # never log request lines
            pass

        def _host_ok(self):
            return self.headers.get("Host") in ALLOWED_HOSTS

        def _authed(self):
            given = self.headers.get("X-OLB-Token") or cookie_value(self.headers.get("Cookie"), "olb")
            return secrets.compare_digest(given, token)

        def _login_redirect(self):
            self.send_response(302)
            self.send_header("Set-Cookie", f"olb={token}; HttpOnly; SameSite=Strict; Path=/")
            self.send_header("Location", "/")
            self.end_headers()

        def _json(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ValueError("body too large")
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if not self._host_ok():
                return self._json(403, {"error": "bad host"})
            params = query_params(query)
            if path == "/inject.js":  # public on purpose: contains no secrets
                return self._asset("inject.js", "text/javascript; charset=utf-8")
            if path == "/" and take_nonce(params.get("n", "")):
                return self._login_redirect()
            if not self._authed():
                return self._json(401, {"error": "run `olbridge open` to sign in"})
            if path == "/":
                return self._page()
            if path == "/api/state":
                return self._json(200, bridge.state())
            if path == "/api/events":
                return self._stream(int(params.get("after", 0)))
            if path == "/api/page":
                return self._json(200, bridge.page(params.get("id", "")))
            self._json(404, {"error": "not found"})

        def _asset(self, name, content_type, extra_headers=()):
            body = (UI_DIR / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in extra_headers:
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _page(self):
            self._asset("index.html", "text/html; charset=utf-8", [
                ("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"),
                ("Referrer-Policy", "no-referrer"),
            ])

        def _stream(self, last):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    events = bridge.events_after(last)
                    for e in events:
                        last = e["id"]
                        self.wfile.write(f"id: {last}\ndata: {json.dumps(e)}\n\n".encode())
                    if not events:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_POST(self):
            origins = {f"http://{h}" for h in ALLOWED_HOSTS}
            if not self._host_ok() or self.headers.get("Origin") not in origins | {None}:
                return self._json(403, {"error": "forbidden"})
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            try:
                self._json(200, self._dispatch(self.path, self._body()))
            except (ValueError, KeyError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - keep the server alive, report only the type
                self._json(500, {"error": type(exc).__name__})

        def _dispatch(self, path, data):
            if path == "/api/import":
                return bridge.import_project(data["id"])
            if path == "/api/sync":
                return bridge.sync(data["id"], push=True)
            if path == "/api/pull":  # pull-only; used when a project is opened
                link_id = data.get("id") or bridge.link_id_for_local(data["local_id"])
                return bridge.sync(link_id, push=False)
            if path == "/api/resolve":
                bridge.resolve(data["id"], data["path"], data["choice"])
                return {"ok": True}
            if path == "/api/push-setting":
                bridge.set_push(data["id"], data["enabled"])
                return {"ok": True}
            if path == "/api/poke":
                bridge.poke.set()
                return {"ok": True}
            if path == "/api/nonce":
                code = secrets.token_urlsafe(24)
                nonces[code] = time.time() + NONCE_TTL_S
                return {"nonce": code}
            raise ValueError("unknown endpoint")

    return Handler


def serve(bridge):
    token = load_token()
    threading.Thread(target=bridge.run_forever, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), make_handler(bridge, token))
    httpd.daemon_threads = True
    httpd.serve_forever()
