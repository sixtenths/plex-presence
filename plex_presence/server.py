"""Loopback-only GUI with strict Host/Origin/CSRF checks."""
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

WEB = Path(__file__).parent / "web"


def handler_for(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PlexPresence"

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass  # OAuth codes and request bodies must never be logged.

        def reply(self, code, value, kind="application/json"):
            raw = json.dumps(value).encode() if kind == "application/json" else value
            self.send_response(code)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https://wsrv.nl; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(raw)

        def allowed(self):
            return self.headers.get("Host") == f"127.0.0.1:{service.port}"

        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {"error": "Use the 127.0.0.1 address shown by the application."})
            url = urlsplit(self.path)
            static = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "application/javascript"), "/style.css": ("style.css", "text/css")}
            if url.path in static:
                file, kind = static[url.path]
                return self.reply(200, (WEB / file).read_bytes(), kind)
            if url.path == "/api/status":
                return self.reply(200, service.status())
            if url.path == "/callback":
                service.finish_oauth(parse_qs(url.query))
                self.send_response(303)
                self.send_header("Location", "/?discord=1")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.reply(404, {"error": "Not found."})

        def do_POST(self):
            csrf = self.headers.get("X-CSRF-Token", "")
            if (not self.allowed() or self.headers.get("Origin") != service.origin
                    or not csrf.isascii() or not hmac.compare_digest(csrf, service.csrf)):
                return self.reply(403, {"error": "Reload the local setup page before making changes."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384 or self.headers.get_content_type() != "application/json":
                    return self.reply(400, {"error": "Send a small JSON request."})
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Invalid request.")
                result = service.action(urlsplit(self.path).path, data)
                self.reply(200, result)
            except ValueError as error:
                # All service validation errors are local strings, never API bodies.
                self.reply(400, {"error": str(error) if not isinstance(error, json.JSONDecodeError) else "Invalid JSON."})
            except (OSError, TypeError, KeyError):
                self.reply(502, {"error": "The service could not be reached or returned an unexpected response. Check your connection and credentials, then retry."})

    return Handler


def make_server(service):
    server = ThreadingHTTPServer(("127.0.0.1", service.port), handler_for(service))
    server.daemon_threads = True
    return server
