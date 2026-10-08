"""Offline helper checks: no kubectl, Kubernetes, AWS or network access."""
import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SHARED = Path(__file__).resolve().parents[1] / "experiments" / "shared"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SHARED / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load("auth_probe")
summary = load("audit_summary")


class ProbeChecks(unittest.TestCase):
    def test_only_cluster_data_is_requested(self):
        data = [{"server": "https://lab.example.invalid:6443"}]
        with patch.object(probe, "kubectl", return_value=json.dumps(data)) as command:
            cluster, endpoint = probe.cluster_config("lab")
        self.assertEqual(endpoint.port, 6443)
        self.assertEqual(command.call_args.args[-1], "jsonpath-as-json={.clusters[0].cluster}")
        self.assertNotIn("users", command.call_args.args[-1])

    def test_unsafe_or_unsupported_connection_fails(self):
        variants = ({"insecure-skip-tls-verify": True}, {"proxy-url": "http://proxy.invalid"},
                    {"tls-server-name": "override.invalid"}, {"server": "http://lab.invalid"},
                    {"server": "https://lab.invalid/prefix"}, {"server": "https://user@lab.invalid"})
        for variant in variants:
            cluster = {"server": "https://lab.example.invalid", **variant}
            with self.subTest(variant=variant), patch.object(probe, "kubectl", return_value=json.dumps([cluster])):
                with self.assertRaises(ValueError):
                    probe.cluster_config("lab")

    def test_tls_negative_case_keeps_verification(self):
        context = probe.tls_context({}, "tls-untrusted")
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(context.cert_store_stats()["x509_ca"], 0)

    def test_token_is_captured_without_argv_or_output(self):
        result = subprocess.CompletedProcess([], 0, "synthetic-test-credential", "")
        with patch.object(probe.subprocess, "run", return_value=result) as run, redirect_stdout(io.StringIO()) as output:
            token = probe.kubectl("lab", "-n", probe.NAMESPACE, "create", "token", "reader", "--duration=10m")
        self.assertEqual(token, "synthetic-test-credential")
        self.assertNotIn(token, str(run.call_args))
        self.assertEqual(output.getvalue(), "")
        self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_plugin_error_does_not_forward_output(self):
        result = subprocess.CompletedProcess([], 1, "synthetic-sensitive-stdout", "synthetic-sensitive-stderr")
        with patch.object(probe.subprocess, "run", return_value=result):
            with self.assertRaises(ValueError) as error:
                probe.kubectl("lab", "config", "view")
        self.assertNotIn("synthetic-sensitive", str(error.exception))

    def test_http_request_has_no_client_certificate_or_body_dump(self):
        with patch.object(probe.http.client, "HTTPSConnection") as constructor:
            connection = constructor.return_value
            connection.getresponse.return_value.status = 403
            connection.getresponse.return_value.getheader.return_value = "synthetic-audit-id"
            result = probe.request_once("lab.example.invalid", 6443, object(), "sa-denied", "synthetic-test-credential")
        self.assertEqual(result, {"http_code": 403, "audit_id": "synthetic-audit-id"})
        self.assertEqual(connection.request.call_args.args[0], "DELETE")
        headers = connection.request.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer synthetic-test-credential")
        self.assertEqual(set(headers), {"Authorization", "Accept", "User-Agent"})
        connection.getresponse.return_value.read.assert_not_called()
        connection.close.assert_called_once()

    def run_main(self, case, expected=None, result=None, error=None):
        argv = ["auth_probe", "--context", "lab", "--case", case]
        if expected is not None:
            argv.extend(["--expect", str(expected)])
        with patch.object(sys, "argv", argv), patch.object(probe, "probe", return_value=result, side_effect=error), redirect_stdout(io.StringIO()) as output:
            code = probe.main()
        return code, output.getvalue()

    def test_wrong_http_status_fails(self):
        code, output = self.run_main("sa-denied", 403, {"http_code": 200, "audit_id": None})
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["http_code"], 200)

    def test_anonymous_defaults_reject_unexpected_status(self):
        for status in (200, 500):
            code, _ = self.run_main("anonymous", result={"http_code": status, "audit_id": None})
            self.assertEqual(code, 1)
        for status in (401, 403):
            code, _ = self.run_main("anonymous", result={"http_code": status, "audit_id": None})
            self.assertEqual(code, 0)

    def test_expected_pre_http_failure_succeeds_but_other_failure_fails(self):
        for case, error in (("tls-untrusted", ssl.SSLCertVerificationError(1, "synthetic-sensitive")),
                            ("connection-refused", ConnectionRefusedError("synthetic-sensitive"))):
            code, output = self.run_main(case, error=error)
            self.assertEqual(code, 0)
            self.assertIsNone(json.loads(output)["http_code"])
            self.assertNotIn("synthetic-sensitive", output)
        code, output = self.run_main("tls-untrusted", error=TimeoutError("synthetic-sensitive"))
        self.assertEqual(code, 1)
        self.assertNotIn("synthetic-sensitive", output)

    def test_unexpected_http_in_pre_http_case_fails(self):
        code, _ = self.run_main("tls-untrusted", result={"http_code": 200, "audit_id": None})
        self.assertEqual(code, 1)


class AuditChecks(unittest.TestCase):
    def test_401_without_objectref_is_kept_at_response_started(self):
        event = {"auditID": "synthetic-audit-id", "stage": "ResponseStarted",
                 "userAgent": "kubernetes-experiments/invalid", "responseStatus": {"code": 401}}
        args = argparse.Namespace(audit_id=None, code=401, namespace=None, user_agent=event["userAgent"])
        self.assertTrue(summary.select(event, args))
        args.namespace = "api-audit-lab"
        self.assertFalse(summary.select(event, args))

    def test_all_stages_are_retained_and_body_is_dropped(self):
        events = [{"auditID": "synthetic-audit-id", "stage": stage,
                   "requestObject": {"token": "synthetic-sensitive"},
                   "responseObject": {"token": "synthetic-sensitive"}}
                  for stage in ("RequestReceived", "ResponseStarted", "ResponseComplete")]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.jsonl"
            path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
            with patch.object(sys, "argv", ["audit_summary", str(path), "--audit-id", "synthetic-audit-id"]), redirect_stdout(io.StringIO()) as output:
                code = summary.main()
        self.assertEqual(code, 0)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([line["stage"] for line in lines], [event["stage"] for event in events])
        self.assertNotIn("synthetic-sensitive", output.getvalue())

    def test_empty_selection_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.jsonl"
            path.write_text('{"responseStatus":{"code":200}}\n', encoding="utf-8")
            with patch.object(sys, "argv", ["audit_summary", str(path), "--code", "401"]), redirect_stdout(io.StringIO()):
                self.assertEqual(summary.main(), 1)


if __name__ == "__main__":
    unittest.main()
