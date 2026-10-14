"""Read-only acceptance checks for an authenticated Agata Proxima Control Plane.

This script does not create or modify organizations, tenants, policies, nodes, billing,
or deployment state. It records only HTTP status, response shape/counts, and readiness
state; it never writes cookies, passwords, CSRF tokens, API keys, or response bodies.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener

from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("AGATA_CONTROL_PLANE_URL", "").rstrip("/")
EMAIL = os.getenv("AGATA_LAB_EMAIL", "")
PASSWORD = os.getenv("AGATA_LAB_PASSWORD", "")
RESULTS: list[dict[str, Any]] = []


def parse_body(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_non_json": True, "_length": len(raw)}


def call(opener, method: str, path: str, payload: object | None = None, csrf: str | None = None):
    headers = {"Accept": "application/json", "X-Request-ID": f"enterprise-lab-{uuid.uuid4()}"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if csrf:
        headers["x-csrf-token"] = csrf
    request = Request(urljoin(f"{BASE_URL}/", path.lstrip("/")), data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=12) as response:
            return response.status, parse_body(response.read())
    except HTTPError as exc:
        return exc.code, parse_body(exc.read())
    except URLError as exc:
        raise RuntimeError(f"network request failed ({type(exc.reason).__name__})") from exc


def record(name: str, passed: bool, detail: str) -> None:
    RESULTS.append({
        "name": name,
        "status": "PASS" if passed else "FAIL",
        "detail": detail,
        "recorded_at": datetime.now(UTC).isoformat(),
    })
    print(f'{"PASS" if passed else "FAIL"} | {name} | {detail}')


def find_value(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item not in (None, ""):
                return item
        for item in value.values():
            found = find_value(item, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_value(item, keys)
            if found not in (None, ""):
                return found
    return None


def shape(value: Any) -> str:
    if isinstance(value, list):
        return f"JSON array; items={len(value)}"
    if isinstance(value, dict):
        keys = sorted(key for key in value if not key.startswith("_"))
        return "JSON object; keys=" + ",".join(keys[:12])
    if value is None:
        return "empty response"
    return type(value).__name__


def lab_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def write_report(started_at: str, readiness: str) -> None:
    failed = sum(item["status"] == "FAIL" for item in RESULTS)
    blocked = readiness != "ready"
    status = "FAIL" if failed else ("BLOCKED" if blocked else "PASS")
    report = {
        "suite": "agata-proxima-control-plane-read-only-acceptance",
        "target_origin": BASE_URL,
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "enterprise_lab_commit": lab_commit(),
        "product_commit": os.getenv("AGATA_PROXIMA_COMMIT") or None,
        "status": status,
        "failed_checks": failed,
        "production_readiness": readiness,
        "results": RESULTS,
        "interpretation": (
            "This is a read-only API acceptance report. It does not prove live Engine tenant "
            "isolation, provider checkout/webhook acceptance, SSO, backups, or production readiness."
        ),
    }
    output = json.dumps(report, indent=2, sort_keys=True).encode("utf-8")
    folder = Path("reports")
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"control-plane-acceptance-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path = folder / filename
    path.write_bytes(output)
    digest = hashlib.sha256(output).hexdigest()
    (folder / f"{filename}.sha256").write_text(f"{digest}  {filename}\n", encoding="utf-8")
    print(f"Evidence report: {path}")
    print(f"SHA-256: {digest}")
    print(f"Overall result: {status}")


def main() -> int:
    started_at = datetime.now(UTC).isoformat()
    if not BASE_URL or not EMAIL or not PASSWORD:
        print(
            "Set AGATA_CONTROL_PLANE_URL, AGATA_LAB_EMAIL, and AGATA_LAB_PASSWORD in the local environment.",
            file=sys.stderr,
        )
        return 2

    try:
        anonymous = build_opener(HTTPCookieProcessor())
        for path in ("/healthz", "/api/v1/health"):
            try:
                status, body = call(anonymous, "GET", path)
                record(f"public endpoint {path}", status == 200, f"HTTP {status}; {shape(body)}")
            except RuntimeError as exc:
                record(f"public endpoint {path}", False, str(exc))

        status, _ = call(anonymous, "GET", "/api/v1/session")
        record("unauthenticated session rejected", status == 401, f"HTTP {status}")

        cookies = HTTPCookieProcessor()
        opener = build_opener(cookies)
        status, login_body = call(
            opener,
            "POST",
            "/api/v1/auth/login",
            {"email": EMAIL, "password": PASSWORD},
        )
        record("login with dedicated lab account", status == 200, f"HTTP {status}; {shape(login_body)}")
        if status != 200:
            write_report(started_at, "unknown")
            return 1

        session_status, session = call(opener, "GET", "/api/v1/session")
        csrf = find_value(session, {"csrf_token", "csrfToken", "csrf"})
        record("authenticated session established", session_status == 200, f"HTTP {session_status}; {shape(session)}")
        record("CSRF token available for future mutations", isinstance(csrf, str), "token value intentionally not recorded")

        paths = [
            "/api/v1/organizations",
            "/api/v1/organization/team",
            "/api/v1/organization/invitations",
            "/api/v1/tenants",
            "/api/v1/policies",
            "/api/v1/nodes",
            "/api/v1/deployments",
            "/api/v1/verifications",
            "/api/v1/audit",
            "/api/v1/support",
            "/api/v1/developer/api-keys",
            "/api/v1/developer/webhooks",
            "/api/v1/billing",
            "/api/v1/billing/plans",
            "/api/v1/billing/entitlements",
        ]
        for path in paths:
            try:
                code, body = call(opener, "GET", path, csrf=csrf if isinstance(csrf, str) else None)
                record(f"authenticated API {path}", code == 200, f"HTTP {code}; {shape(body)}")
            except RuntimeError as exc:
                record(f"authenticated API {path}", False, str(exc))

        readiness_status, readiness_body = call(anonymous, "GET", "/api/v1/production/readiness")
        readiness_value = find_value(readiness_body, {"status", "readiness", "state"})
        readiness_ready = find_value(readiness_body, {"ready"})
        if readiness_status == 200 and (readiness_ready is True or (isinstance(readiness_value, str) and readiness_value.lower() == "ready")):
            readiness = "ready"
        elif readiness_status == 200:
            readiness = str(readiness_value or "needs_review")
        else:
            readiness = "unknown"
        record(
            "production readiness endpoint responds",
            readiness_status == 200,
            f"HTTP {readiness_status}; state={readiness}; {shape(readiness_body)}",
        )
        write_report(started_at, readiness)
        return 1 if any(item["status"] == "FAIL" for item in RESULTS) else (0 if readiness == "ready" else 3)
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        print(f"FATAL | {type(exc).__name__}: {exc}", file=sys.stderr)
        write_report(started_at, "unknown")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
