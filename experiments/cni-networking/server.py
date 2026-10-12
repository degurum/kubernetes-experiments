"""Readiness fixture: /whoami and /livez stay healthy when /readyz fails."""

import itertools
import json
import os
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

INSTANCE = uuid.uuid4().hex[:12]
CONNECTIONS = itertools.count(1)
FLAG = Path(os.environ.get("READY_FLAG", "/state/not-ready"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection_id = f"{INSTANCE}-{next(CONNECTIONS)}"

    def do_GET(self):
        route = self.path.split("?", 1)[0]
        status = 503 if route == "/readyz" and FLAG.exists() else 200
        if route not in ("/whoami", "/readyz", "/livez"):
            status = 404
        payload = json.dumps({
            "time": datetime.now(timezone.utc).isoformat(),
            "pod": os.environ.get("POD_NAME", "local-fixture"),
            "uid": os.environ.get("POD_UID", "local-fixture"),
            "pod_ip": os.environ.get("POD_IP", "127.0.0.1"),
            "node": os.environ.get("NODE_NAME", "local-fixture"),
            "connection_id": self.connection_id,
            "path": route,
            "readiness_http_ok": not FLAG.exists(),
        }).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)
        if route == "/readyz" and self.headers.get("User-Agent", "").startswith("kube-probe/"):
            print(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                              "event": "readiness-response", "status": status,
                              "pod": os.environ.get("POD_NAME", "local-fixture")}), flush=True)

    def log_message(self, format, *args):
        # Use the structured readiness log above; silence default access logs.
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
