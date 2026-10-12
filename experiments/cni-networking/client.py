"""Record new TCP connections or one existing connection; never silently reconnect."""

import argparse
import http.client
import json
import sys
import time
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--path", default="/whoami")
    parser.add_argument("--mode", choices=("new", "keepalive"), default="new")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--interval", type=float, default=1)
    args = parser.parse_args()
    if args.count < 1 or args.interval < 0:
        parser.error("count must be positive and interval must be non-negative")

    connection = None
    first_identity = None
    failed = False
    try:
        for sequence in range(args.count):
            if connection is None:
                connection = http.client.HTTPConnection(args.host, args.port, timeout=3)
            record = {"time": datetime.now(timezone.utc).isoformat(),
                      "sequence": sequence, "mode": args.mode, "host": args.host}
            try:
                connection.request("GET", args.path)
                response = connection.getresponse()
                body = json.loads(response.read())
                record.update(status=response.status, backend=body)
                if connection.sock:
                    record["local_socket"] = connection.sock.getsockname()
                    record["application_peer"] = connection.sock.getpeername()
                identity = (body["pod"], body["uid"], body["connection_id"])
                if args.mode == "keepalive":
                    if first_identity is None:
                        first_identity = identity
                    elif first_identity != identity:
                        raise RuntimeError("backend or connection ID changed; stop this observation")
                    if connection.sock is None:
                        raise RuntimeError("persistent socket closed; no automatic reconnect")
                print(json.dumps(record), flush=True)
            except (OSError, http.client.HTTPException, ValueError, KeyError, RuntimeError) as error:
                record["error"] = str(error)
                print(json.dumps(record), flush=True)
                failed = True
                connection.close()
                connection = None
                if args.mode == "keepalive":
                    break
            if args.mode == "new" and connection is not None:
                connection.close()
                connection = None
            if sequence + 1 < args.count:
                time.sleep(args.interval)
    finally:
        if connection is not None:
            connection.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
