#!/usr/bin/env python3
"""One lab HTTP request. Tokens stay in memory; no response body/header dump."""
import argparse
import base64
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import socket
import ssl
import subprocess
from urllib.parse import urlsplit

NAMESPACE = "api-audit-lab"
CASES = ("sa-list", "sa-denied", "invalid", "anonymous", "tls-untrusted", "connection-refused")


def kubectl(context, *args):
    result = subprocess.run(
        ["kubectl", "--context", context, "--request-timeout=10s", *args],
        capture_output=True, text=True, check=False, timeout=20,
    )
    if result.returncode:
        # Do not forward stdout/stderr: an exec credential plugin may print secrets.
        raise ValueError("kubectl prerequisite failed; inspect privately")
    return result.stdout.strip()


def cluster_config(context):
    # Select only cluster connection data, never users/admin credentials.
    raw = kubectl(context, "config", "view", "--minify", "--raw",
                  "-o", "jsonpath-as-json={.clusters[0].cluster}")
    cluster = json.loads(raw)[0]
    if cluster.get("insecure-skip-tls-verify"):
        raise ValueError("verified TLS is required")
    if cluster.get("proxy-url") or cluster.get("tls-server-name"):
        raise ValueError("probe requires a direct endpoint with its certificate hostname")
    endpoint = urlsplit(cluster["server"])
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username
            or endpoint.password or endpoint.query or endpoint.fragment
            or endpoint.path not in ("", "/")):
        raise ValueError("probe requires a direct HTTPS API endpoint")
    return cluster, endpoint


def tls_context(cluster, case):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if case == "tls-untrusted":
        return context  # No trusted CA loaded. Verification remains enabled.
    if cluster.get("certificate-authority-data"):
        context.load_verify_locations(
            cadata=base64.b64decode(cluster["certificate-authority-data"], validate=True).decode("ascii")
        )
    elif cluster.get("certificate-authority"):
        ca = Path(cluster["certificate-authority"])
        if not ca.is_absolute():
            raise ValueError("use embedded CA data or an absolute CA file path")
        context.load_verify_locations(cafile=str(ca))
    else:
        context.load_default_certs()
    return context


def request_once(host, port, context, case, token=None):
    path = f"/api/v1/namespaces/{NAMESPACE}/configmaps"
    if case == "sa-denied":
        path += "/sample"
    headers = {"User-Agent": f"kubernetes-experiments/{case}", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    connection = http.client.HTTPSConnection(host, port, context=context, timeout=10)
    try:
        connection.request("DELETE" if case == "sa-denied" else "GET", path, headers=headers)
        response = connection.getresponse()
        # No redirects, response dumps or further API requests.
        return {"http_code": response.status, "audit_id": response.getheader("Audit-Id")}
    finally:
        connection.close()


def probe(context_name, case):
    token = None
    if case == "connection-refused":
        # Bound but NOT listening; no traffic reaches the cluster or another service.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as guard:
            guard.bind(("127.0.0.1", 0))
            return request_once("127.0.0.1", guard.getsockname()[1],
                                ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT), case)
    cluster, endpoint = cluster_config(context_name)
    context = tls_context(cluster, case)
    if case in ("sa-list", "sa-denied"):
        token = kubectl(context_name, "-n", NAMESPACE, "create", "token", "reader", "--duration=10m")
        if not token or "\n" in token:
            raise ValueError("TokenRequest did not return a single token")
    elif case == "invalid":
        token = "synthetic-invalid-token"  # Deliberately not a real credential.
    try:
        return request_once(endpoint.hostname, endpoint.port or 443, context, case, token)
    finally:
        token = None  # Process exit ends the lifetime; this is not memory zeroization.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="explicit disposable lab context")
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--expect", type=int, help="expected HTTP code; mismatch exits 1")
    args = parser.parse_args()
    if args.expect is not None and args.case in ("tls-untrusted", "connection-refused"):
        parser.error("pre-HTTP cases have no expected HTTP code")
    record = {"case": args.case, "started_utc": datetime.now(timezone.utc).isoformat()}
    result_code = 0
    try:
        record.update(probe(args.context, args.case))
        if args.case in ("tls-untrusted", "connection-refused"):
            result_code = 1  # Receiving HTTP would violate the case hypothesis.
        else:
            defaults = {"sa-list": {200}, "sa-denied": {403}, "invalid": {401},
                        "anonymous": {401, 403}}
            expected_codes = {args.expect} if args.expect is not None else defaults[args.case]
            if record["http_code"] not in expected_codes:
                result_code = 1
    except (ssl.SSLCertVerificationError, ConnectionRefusedError) as exc:
        record.update(http_code=None, audit_id=None, error=type(exc).__name__)
        expected = {"tls-untrusted": ssl.SSLCertVerificationError,
                    "connection-refused": ConnectionRefusedError}.get(args.case)
        result_code = 0 if expected and isinstance(exc, expected) else 1
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        # Only the class, not exception text, URLs, tokens, headers or plugin output.
        record.update(http_code=None, audit_id=None, error=type(exc).__name__)
        result_code = 1
    record["finished_utc"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(record, ensure_ascii=True))
    return result_code


if __name__ == "__main__":
    raise SystemExit(main())
