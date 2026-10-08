#!/usr/bin/env python3
"""Select audit events locally. Output remains private; this is NOT anonymization."""
import argparse
import json
from pathlib import Path


def select(event, args):
    if args.audit_id and event.get("auditID") != args.audit_id:
        return False
    if args.code is not None and event.get("responseStatus", {}).get("code") != args.code:
        return False
    if args.namespace and event.get("objectRef", {}).get("namespace") != args.namespace:
        return False
    if args.user_agent and event.get("userAgent") != args.user_agent:
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="+", help="private JSONL files, including rotations")
    parser.add_argument("--audit-id")
    parser.add_argument("--code", type=int)
    parser.add_argument("--namespace")
    parser.add_argument("--user-agent")
    args = parser.parse_args()
    count = 0
    for path in args.files:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    parser.error(f"invalid JSON at input line {line_number}; inspect privately")
                if not select(event, args):
                    continue
                fields = ("auditID", "stage", "requestReceivedTimestamp", "stageTimestamp",
                          "verb", "requestURI", "userAgent", "user", "impersonatedUser",
                          "objectRef", "responseStatus")
                summary = {key: event[key] for key in fields if key in event}
                summary["annotations"] = {
                    key: value for key, value in event.get("annotations", {}).items()
                    if key in ("authorization.k8s.io/decision", "authorization.k8s.io/reason")
                }
                print(json.dumps(summary, ensure_ascii=True))
                count += 1
    return 0 if count else 1  # Empty selection is not proof of no audit.


if __name__ == "__main__":
    raise SystemExit(main())
