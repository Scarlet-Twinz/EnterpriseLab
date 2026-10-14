#!/usr/bin/env python3
"""Controlled attack suite for an isolated Enterprise Lab deployment."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("LAB_BASE_URL", "http://127.0.0.1:8088").rstrip("/")
KEY_A = os.getenv("LAB_API_KEY_A", "")
KEY_B = os.getenv("LAB_API_KEY_B", "")
RESULTS: list[dict[str, object]] = []


def request(method: str, path: str, *, key: str | None = None, payload: object | None = None):
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "X-Request-ID": f"enterprise-lab-{time.time_ns()}"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = Request(f"{BASE_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=8) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except HTTPError as exc:
        raw = exc.read()
        try:
            data = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            data = {"raw": raw[:300].decode(errors="replace")}
        return exc.code, data
    except URLError as exc:
        raise RuntimeError(f"Could not reach Enterprise Lab at {BASE_URL}: {exc.reason}") from exc


def check(label: str, condition: bool, detail: str) -> bool:
    RESULTS.append({
        "name": label,
        "status": "PASS" if condition else "FAIL",
        "detail": detail,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f'{"PASS" if condition else "FAIL"} | {label} | {detail}')
    return condition


def write_report(started_at: str, failures: int) -> None:
    report_dir = Path("reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    completed_at = datetime.now(timezone.utc).isoformat()
    report = {
        "suite": "enterprise-lab-attack-suite",
        "target": BASE_URL,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": "PASS" if failures == 0 else "FAIL",
        "passed": sum(item["status"] == "PASS" for item in RESULTS),
        "failed": failures,
        "results": RESULTS,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True).encode("utf-8")
    filename = f"attack-suite-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    report_path = report_dir / filename
    report_path.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    (report_dir / f"{filename}.sha256").write_text(f"{digest}  {filename}\n", encoding="utf-8")
    print(f"Evidence report: {report_path}")
    print(f"SHA-256: {digest}")


def main() -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    if not KEY_A or not KEY_B:
        print("Set LAB_API_KEY_A and LAB_API_KEY_B to dedicated test API keys.", file=sys.stderr)
        return 2

    failures = 0
    status, _ = request("GET", "/healthz")
    failures += not check("liveness", status == 200, f"HTTP {status}")

    status, _ = request("GET", "/api/v1/me")
    failures += not check("missing authentication rejected", status == 401, f"HTTP {status}")

    status, _ = request("GET", "/api/v1/me", key="definitely-invalid-enterprise-lab-key")
    failures += not check("unknown API key rejected", status == 401, f"HTTP {status}")

    status, identity_a = request("GET", "/api/v1/me", key=KEY_A)
    status_b, identity_b = request("GET", "/api/v1/me", key=KEY_B)
    distinct = (
        status == 200 and status_b == 200 and identity_a.get("tenant_id") != identity_b.get("tenant_id")
        if isinstance(identity_a, dict) and isinstance(identity_b, dict)
        else False
    )
    failures += not check("test identities are distinct", distinct, "server-side key mapping checked")

    # Create a unique tenant-B customer, then attempt to read it using tenant A's identity.
    marker = f"ELAB-CROSS-TENANT-{time.time_ns()}"
    status, created = request(
        "POST", "/api/v1/customers", key=KEY_B,
        payload={"name": marker, "email": f"{marker.lower()}@example.test", "status": "active"},
    )
    created_ok = status == 201 and isinstance(created, dict) and "id" in created
    failures += not check("tenant B can create a synthetic customer", created_ok, f"HTTP {status}")
    if created_ok:
        record_id = created["id"]
        own_status, _ = request("GET", f"/api/v1/customers/{record_id}", key=KEY_B)
        cross_status, _ = request("GET", f"/api/v1/customers/{record_id}", key=KEY_A)
        failures += not check("tenant B can read its own customer", own_status == 200, f"HTTP {own_status}")
        failures += not check(
            "tenant A cannot read tenant B customer",
            cross_status in (403, 404),
            f"HTTP {cross_status}",
        )

        # Exercise the other business tables using tenant B data, then check it is
        # absent from tenant A's API responses. All data is synthetic.
        suffix = time.time_ns()
        business_cases = [
            (
                "employee",
                "/api/v1/employees",
                {
                    "name": f"{marker} Employee",
                    "email": f"employee-{suffix}@example.test",
                    "department": "Operations",
                    "job_title": "Analyst",
                    "status": "active",
                },
                f"{marker} Employee",
            ),
            (
                "project",
                "/api/v1/projects",
                {
                    "name": f"{marker} Project",
                    "description": "Synthetic tenant-isolation test project",
                    "budget_cents": 100000,
                    "status": "active",
                },
                f"{marker} Project",
            ),
            (
                "document",
                "/api/v1/documents",
                {
                    "title": f"{marker} Document",
                    "classification": "confidential",
                    "body": f"{marker} synthetic confidential test body",
                },
                f"{marker} Document",
            ),
            (
                "invoice",
                "/api/v1/invoices",
                {
                    "customer_id": record_id,
                    "amount_cents": 10000,
                    "currency": "USD",
                    "description": f"{marker} Invoice",
                },
                f"{marker} Invoice",
            ),
        ]
        for label, path, payload, marker_text in business_cases:
            create_status, _ = request("POST", path, key=KEY_B, payload=payload)
            failures += not check(f"tenant B can create synthetic {label}", create_status == 201, f"HTTP {create_status}")
            if create_status == 201:
                list_status, records = request("GET", path, key=KEY_A)
                leaked = marker_text in json.dumps(records, sort_keys=True)
                failures += not check(
                    f"tenant A cannot list tenant B {label}",
                    list_status == 200 and not leaked,
                    f"HTTP {list_status}; marker_visible={leaked}",
                )

    # Attempt to smuggle a tenant ID into a strict request body.
    status, _ = request(
        "POST", "/api/v1/customers", key=KEY_A,
        payload={
            "tenant_id": identity_b.get("tenant_id", "tenant_b") if isinstance(identity_b, dict) else "tenant_b",
            "name": "Forged Tenant Assignment",
            "email": f"forged-{time.time_ns()}@example.test",
        },
    )
    failures += not check("client-supplied tenant assignment rejected", status == 422, f"HTTP {status}")

    # Controlled concurrency against a read-only endpoint; this is not an unbounded DoS test.
    def one_read(_index: int):
        return request("GET", "/api/v1/customers", key=KEY_A)[0]

    started = time.perf_counter()
    statuses: list[int] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(one_read, index) for index in range(32)]
        for future in as_completed(futures):
            statuses.append(future.result())
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    successful = sum(code == 200 for code in statuses)
    failures += not check(
        "bounded concurrent read workload",
        successful == len(statuses),
        f"{successful}/{len(statuses)} succeeded; {elapsed_ms} ms total",
    )

    write_report(started_at, failures)
    print(f"\nEnterprise Lab attack suite: {'PASS' if failures == 0 else 'FAIL'} ({failures} failed checks)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
