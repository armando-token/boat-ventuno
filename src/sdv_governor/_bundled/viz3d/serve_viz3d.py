#!/usr/bin/env python3
"""Servidor ligero para el Gemelo Digital 3D (viz3d) con proxy estricto a la API autoritativa del SDV Resource Governor.

No instancia un segundo motor local en memoria (evita split-brain, ALIGN-T38).
- Si el backend autoritativo está corriendo en http://127.0.0.1:8765, hace proxy transparente de /api/* a :8765.
- Si :8765 no está accesible, devuelve HTTP 503 con {"status": "UPSTREAM_UNAVAILABLE", "connection_state": "STALE", "source": "PROXY_DISCONNECTED"}.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from typing import Any, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

VIZ3D_DIR = Path(__file__).resolve().parent


def forward_or_dispatch_api(
    method: str,
    path: str,
    body_bytes: bytes,
    upstream_url: str,
    local_session: Any = None,
) -> Tuple[int, bytes, str]:
    """Hace proxy estricto a upstream_url (:8765); si no está accesible, devuelve HTTP 503 STALE_DISCONNECTED sin crear una segunda sesión local."""
    target = f"{upstream_url.rstrip('/')}{path}"
    req_headers = {"Accept": "application/json"}
    if body_bytes:
        req_headers["Content-Type"] = "application/json; charset=utf-8"

    try:
        req = Request(target, data=body_bytes if method == "POST" else None, headers=req_headers, method=method)
        with urlopen(req, timeout=1.5) as resp:
            payload = resp.read()
            return resp.status, payload, "LIVE_PROXY_8765"
    except HTTPError as http_err:
        payload = http_err.read()
        return http_err.code, payload, "LIVE_PROXY_8765"
    except (URLError, OSError, TimeoutError) as exc:
        err_payload = {
            "status": "UPSTREAM_UNAVAILABLE",
            "connection_state": "STALE",
            "source": "PROXY_DISCONNECTED",
            "upstream": upstream_url,
            "message": f"Authoritative governor at {upstream_url} is unreachable: {exc}",
        }
        encoded = json.dumps(err_payload, indent=2, sort_keys=True).encode("utf-8")
        return 503, encoded, "STALE_DISCONNECTED"


def run_server(host: str = "127.0.0.1", port: int = 5173, upstream: str = "http://127.0.0.1:8765") -> None:
    class Viz3DHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send_cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._send_cors()
            self.end_headers()

        def do_GET(self) -> None:
            if self.path.startswith("/api/"):
                code, payload, mode = forward_or_dispatch_api("GET", self.path, b"", upstream)
                self.send_response(code)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("X-SDV-Bridge-Mode", mode)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            clean = urlparse(self.path).path
            if clean in ("/", "/index.html"):
                file_path = VIZ3D_DIR / "index.html"
            else:
                rel = clean.lstrip("/")
                file_path = (VIZ3D_DIR / rel).resolve()
                if not str(file_path).startswith(str(VIZ3D_DIR)):
                    self.send_error(403, "Forbidden")
                    return

            if not file_path.exists() or not file_path.is_file():
                self.send_error(404, f"Not found: {clean}")
                return

            ctype, _ = mimetypes.guess_type(str(file_path))
            if file_path.suffix in (".js", ".mjs"):
                ctype = "application/javascript; charset=utf-8"
            elif file_path.suffix == ".css":
                ctype = "text/css; charset=utf-8"
            elif file_path.suffix == ".html":
                ctype = "text/html; charset=utf-8"
            elif file_path.suffix == ".yaml":
                ctype = "text/yaml; charset=utf-8"
            elif file_path.suffix == ".glb":
                ctype = "model/gltf-binary"

            data = file_path.read_bytes()
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", ctype or "application/octet-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if not self.path.startswith("/api/"):
                self.send_error(404, "POST only supported on /api/*")
                return
            length = int(self.headers.get("Content-Length", "0"))
            body_bytes = self.rfile.read(length) if length > 0 else b"{}"
            code, payload, mode = forward_or_dispatch_api("POST", self.path, body_bytes, upstream)
            self.send_response(code)
            self._send_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("X-SDV-Bridge-Mode", mode)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    httpd = ThreadingHTTPServer((host, port), Viz3DHandler)
    print(f"==========================================================================")
    print(f"🚢 SDV 3D Digital Twin & IEC 62288 Bridge Console running at:")
    print(f"   👉 http://{host}:{port}")
    print(f"   🔗 Upstream Governor API: {upstream} (strict authoritative proxy, HTTP 503 if disconnected)")
    print(f"==========================================================================")
    httpd.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serve SDV 3D Digital Twin Console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--upstream", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    run_server(args.host, args.port, args.upstream)
