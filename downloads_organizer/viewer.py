"""Read-only loopback trace viewer. No model client or command runner is used."""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ASSETS = Path(__file__).parent / "viewer_assets"
MAX_FILE_BYTES = 100_000_000
SIDECARS = ("manifest.json", "cost_estimate.json", "verification.json",
            "demo_settings.json", "demo_summary.json")
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/trace-core.js": ("trace-core.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/network.js": ("network.js", "text/javascript; charset=utf-8"),
    "/network.css": ("network.css", "text/css; charset=utf-8"),
    "/chat.js": ("chat.js", "text/javascript; charset=utf-8"),
    "/chat.css": ("chat.css", "text/css; charset=utf-8"),
    "/demo.json": ("demo.json", "application/json; charset=utf-8"),
}


def read_text(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"Not a regular file or larger than 100 MB: {path.name}")
    return path.read_text(encoding="utf-8-sig")


def load_run(path: Path) -> dict:
    """Read only an explicitly selected trace and its fixed-name sidecars.

    Paths mentioned inside the trace or sidecars are never followed.
    """
    path = Path(path).expanduser().resolve(strict=True)
    trace = path / "trace.jsonl" if path.is_dir() else path
    if trace.is_symlink():
        raise ValueError("Symbolic-link traces inside run directories are not loaded.")
    bundle = {"name": trace.parent.name, "trace_text": read_text(trace), "sidecars": {}, "warnings": []}
    for name in SIDECARS:
        candidate = trace.parent / name
        if candidate.exists():
            try:
                if candidate.is_symlink():
                    raise ValueError("Symbolic-link sidecars are not loaded.")
                bundle["sidecars"][name] = json.loads(read_text(candidate))
            except (ValueError, OSError, UnicodeError):
                bundle["warnings"].append(f"Could not load optional {name}.")
    return bundle


class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "LocalTraceViewer/1.0"

    def do_GET(self):
        # Fixed routes, loopback binding and no CORS: no file-browsing endpoint.
        allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in allowed_hosts:
            self.send_error(403, "Loopback host required")
            return
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in allowed_hosts}:
            self.send_error(403, "Same origin required")
            return
        route = urlsplit(self.path).path
        if route == "/api/bootstrap":
            body = self.server.bootstrap
            content_type = "application/json; charset=utf-8"
        elif route in STATIC:
            filename, content_type = STATIC[route]
            body = (ASSETS / filename).read_bytes()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Request payloads and trace contents are never logged by the server.
        return


def make_server(runs=(), port: int = 8765) -> ThreadingHTTPServer:
    bundles = [load_run(Path(path)) for path in runs]
    server = ThreadingHTTPServer(("127.0.0.1", port), ViewerHandler)
    server.bootstrap = json.dumps({"bundles": bundles}, ensure_ascii=False).encode("utf-8")
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, action="append", default=[],
                        help="Explicit run directory or JSONL file; repeat to inspect linked runs")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535.")
    try:
        server = make_server(args.run, args.port)
    except (OSError, ValueError, UnicodeError) as error:
        parser.exit(1, f"Cannot start trace viewer: {error}\n")
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Trace viewer: {url}\nRead-only local replay. No API calls. Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        threading.Timer(0.4, partial(webbrowser.open, url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
