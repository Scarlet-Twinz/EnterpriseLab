"""Bounded latency/load sample through the Enterprise Lab API and configured Proxima Engine."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("LAB_BASE_URL", "http://127.0.0.1:8088").rstrip("/")
API_KEY = os.getenv("LAB_API_KEY_A", "")
REQUESTS = int(os.getenv("LAB_PERF_REQUESTS", "100"))
CONCURRENCY = int(os.getenv("LAB_PERF_CONCURRENCY", "10"))


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil((p / 100) * len(ordered)) - 1))
    return round(ordered[index], 2)


def one_read(index: int) -> dict[str, float | int]:
    request = Request(
        f"{BASE_URL}/api/v1/customers",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json",
            "X-Request-ID": f"enterprise-lab-perf-{index}-{uuid.uuid4()}",
        },
        method="GET",
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=10) as response:
            response.read()
            return {"status": response.status, "latency_ms": (time.perf_counter() - started) * 1000}
    except HTTPError as exc:
        exc.read()
        return {"status": exc.code, "latency_ms": (time.perf_counter() - started) * 1000}
    except URLError:
        return {"status": 0, "latency_ms": (time.perf_counter() - started) * 1000}


def main() -> int:
    if not API_KEY:
        print("Set LAB_API_KEY_A to a dedicated Enterprise Lab test key.", file=sys.stderr)
        return 2
    if REQUESTS < 1 or REQUESTS > 1000 or CONCURRENCY < 1 or CONCURRENCY > 50:
        print("Bounds are 1-1000 requests and 1-50 concurrent workers.", file=sys.stderr)
        return 2

    parsed = urlparse(BASE_URL)
    is_local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not is_local and not (
        os.getenv("LAB_PERF_ALLOW_REMOTE") == "YES"
        and os.getenv("LAB_PERF_CONFIRM_ISOLATED") == "YES"
        and os.getenv("LAB_PERF_CONFIRM_TARGET") == BASE_URL
    ):
        print(
            "Remote load is disabled by default. For a dedicated isolated test deployment, "
            "set LAB_PERF_ALLOW_REMOTE=YES, LAB_PERF_CONFIRM_ISOLATED=YES, and "
            "LAB_PERF_CONFIRM_TARGET to the exact LAB_BASE_URL.",
            file=sys.stderr,
        )
        return 2

    started_at = datetime.now(UTC).isoformat()
    start_clock = time.perf_counter()
    results: list[dict[str, float | int]] = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = [pool.submit(one_read, index) for index in range(REQUESTS)]
        for future in as_completed(futures):
            results.append(future.result())
    elapsed = time.perf_counter() - start_clock

    latencies = [float(item["latency_ms"]) for item in results]
    success_count = sum(item["status"] == 200 for item in results)
    error_count = len(results) - success_count
    report = {
        "suite": "enterprise-lab-bounded-performance-sample",
        "target": BASE_URL,
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "requests": REQUESTS,
        "concurrency": CONCURRENCY,
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(REQUESTS / elapsed, 2) if elapsed else 0,
        "successful_requests": success_count,
        "failed_requests": error_count,
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else 0,
            "mean": round(sum(latencies) / len(latencies), 2) if latencies else 0,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "max": round(max(latencies), 2) if latencies else 0,
        },
        "status": "PASS" if error_count == 0 else "FAIL",
        "interpretation": (
            "Bounded API-through-Engine sample only; not an SLA, capacity claim, "
            "or substitute for direct PostgreSQL/TLS comparative benchmarks."
        ),
    }

    output = json.dumps(report, indent=2, sort_keys=True).encode("utf-8")
    folder = Path("reports")
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"performance-sample-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path = folder / filename
    path.write_bytes(output)
    digest = hashlib.sha256(output).hexdigest()
    (folder / f"{filename}.sha256").write_text(f"{digest}  {filename}\n", encoding="utf-8")

    print(
        f"Performance sample: {success_count}/{REQUESTS} successful; "
        f"p50={report['latency_ms']['p50']} ms, "
        f"p95={report['latency_ms']['p95']} ms, "
        f"p99={report['latency_ms']['p99']} ms"
    )
    print(f"Evidence report: {path}")
    print(f"SHA-256: {digest}")
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
