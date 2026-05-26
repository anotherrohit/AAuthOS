"""
Tiny static server for the operator console. Stdlib-only so it runs anywhere
Python runs — no pip install, no FastAPI, no Node toolchain.

Default port 9002. Override with PORT env var.

The console (index.html / app.js) is fully client-side; this server only
serves the static files and adds permissive cache headers for dev.
"""

from __future__ import annotations

import http.server
import os
import socketserver
import sys
from functools import partial
from pathlib import Path

PORT = int(os.environ.get("PORT", "9002"))
ROOT = Path(__file__).parent


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        # Dev-mode: never cache so a refresh always pulls fresh JS.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"[console] {self.address_string()} - {fmt % args}\n")


def main() -> int:
    handler = partial(NoCacheHandler, directory=str(ROOT))
    with socketserver.TCPServer(("0.0.0.0", PORT), handler) as srv:
        srv.allow_reuse_address = True
        print(f"AAuth operator console serving at  http://localhost:{PORT}")
        print(f"  registry-service expected at      http://localhost:9000")
        print(f"  mission-service expected at       http://localhost:9001")
        print(f"  default operator credentials      operator / aauth-operator-demo")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
