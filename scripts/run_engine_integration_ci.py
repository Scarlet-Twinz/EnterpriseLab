"""Start the separately checked-out real Engine and run bounded live acceptance suites.

CI-only orchestration. It starts only the binary path supplied by the workflow, connects
it to the isolated CI PostgreSQL service, and terminates only the child processes it starts.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def wait_for_port(process: subprocess.Popen, log_path: Path, timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Proxima Engine exited early. See {log_path}")
        try:
            with socket.create_connection(("127.0.0.1", 6432), timeout=1):
                return
        except OSError:
            time.sleep(1)
    raise RuntimeError(f"Proxima Engine did not open port 6432. See {log_path}")


def wait_for_api(process: subprocess.Popen, log_path: Path, timeout_seconds: int = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Enterprise Lab API exited early. See {log_path}")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8088/healthz", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    raise RuntimeError(f"Enterprise Lab API did not become healthy. See {log_path}")


def print_log(path: Path) -> None:
    if path.exists():
        print(f"\n===== {path.name} =====", file=sys.stderr)
        print(path.read_text(encoding="utf-8", errors="replace")[-12000:], file=sys.stderr)


def stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    default_binary = root / "agata-proxima" / "target" / "debug" / "proxima-engine"
    engine_binary = Path(os.getenv("AGATA_ENGINE_BINARY", str(default_binary))).resolve()
    if not engine_binary.is_file():
        print(
            "The built Proxima Engine binary was not found. Set AGATA_ENGINE_BINARY or check out and build agata-proxima.",
            file=sys.stderr,
        )
        return 2

    report_dir = root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    engine_log = report_dir / "proxima-engine.log"
    api_log = report_dir / "enterprise-lab-api.log"
    engine = None
    api = None

    try:
        with engine_log.open("w", encoding="utf-8") as log:
            engine = subprocess.Popen(
                [str(engine_binary)],
                cwd=engine_binary.parent.parent.parent,
                env=os.environ.copy(),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            wait_for_port(engine, engine_log)

        with api_log.open("w", encoding="utf-8") as log:
            api = subprocess.Popen(
                [
                    sys.executable, "-m", "uvicorn", "app.main:app",
                    "--host", "127.0.0.1", "--port", "8088",
                ],
                cwd=root,
                env=os.environ.copy(),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            wait_for_api(api, api_log)

        suites = [
            "scripts/proxima_protocol_suite.py",
            "scripts/attack_suite.py",
            "scripts/performance_suite.py",
        ]
        for suite in suites:
            print(f"\n========== {suite} against the real Proxima Engine ==========")
            result = subprocess.run(
                [sys.executable, suite],
                cwd=root,
                env=os.environ.copy(),
                check=False,
                timeout=180,
            )
            if result.returncode != 0:
                print(f"FAIL | {suite} | exit code {result.returncode}", file=sys.stderr)
                print_log(engine_log)
                print_log(api_log)
                return result.returncode or 1
        print("\nPASS | all live Enterprise Lab suites completed against the real Proxima Engine")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"FAIL | real Engine integration | {type(exc).__name__}: {exc}", file=sys.stderr)
        print_log(engine_log)
        print_log(api_log)
        return 1
    finally:
        stop_process(api)
        stop_process(engine)


if __name__ == "__main__":
    raise SystemExit(main())
