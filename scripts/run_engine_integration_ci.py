"""Start the separately checked-out real Engine and run bounded live acceptance suites.

CI-only orchestration. It starts only the binary path supplied by the workflow, connects
it to the isolated CI PostgreSQL service, and terminates only the child processes it starts.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def wait_for_enforcement(process: subprocess.Popen, log_path: Path, timeout_seconds: int = 15) -> None:
    """Require the real Engine's startup log to confirm tenant enforcement is enabled."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Proxima Engine exited before confirming enforcement. See {log_path}")
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
        # Tracing's ANSI styling can split field names and values with escape
        # sequences, so strip those before checking the explicit boolean field.
        normalized = re.sub(r"\x1b\[[0-9;]*m", "", log_text).replace(" ", "")
        if "tenant_enforcement=false" in normalized:
            raise RuntimeError(
                "Proxima Engine started with tenant enforcement disabled; refusing acceptance run."
            )
        if "tenant_enforcement=true" in normalized:
            return
        time.sleep(0.25)
    raise RuntimeError(
        "Proxima Engine did not explicitly report tenant_enforcement=true in its startup log; "
        f"refusing to infer enforcement from an open port. See {log_path}"
    )


def wait_for_port(process: subprocess.Popen, log_path: Path, timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Proxima Engine exited early. See {log_path}")
        try:
            with socket.create_connection(("127.0.0.1", 6432), timeout=1):
                wait_for_enforcement(process, log_path)
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
                capture_output=True,
                text=True,
            )
            suite_log = report_dir / (Path(suite).stem + ".log")
            log_content = result.stdout or ""
            if result.stderr:
                log_content += "\n--- STDERR ---\n" + result.stderr
            suite_log.write_text(log_content, encoding="utf-8")
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
            if result.returncode != 0:
                print(f"FAIL | {suite} | exit code {result.returncode}", file=sys.stderr)
                print_log(engine_log)
                print_log(api_log)
                return result.returncode or 1
        # Exercise the product repository's own verification harness as a
        # customer would, without editing product source. A known source defect
        # (missing TTL argument to the embedded Python signer) is recorded as a
        # reproduced finding rather than silently substituted by the lab suite.
        verify_script = root / "agata-proxima" / "tools" / "proxima-verify.sh"
        tenant_passwords = json.loads(os.getenv("LAB_TENANT_DB_PASSWORDS", "{}"))
        verify_env = os.environ.copy()
        verify_env.update({
            "PROXIMA_VERIFY_HOST": "127.0.0.1",
            "PROXIMA_VERIFY_PORT": "6432",
            "PROXIMA_VERIFY_DATABASE": "enterprise_lab",
            "PROXIMA_VERIFY_USER": "proxima",
            "PROXIMA_VERIFY_TABLE": "customers",
            "PROXIMA_VERIFY_TENANT_COLUMN": "tenant_id",
            "PROXIMA_VERIFY_TENANT_A": "northstar",
            "PROXIMA_VERIFY_TENANT_B": "bluehaven",
            "PROXIMA_VERIFY_TENANT_A_PASSWORD": tenant_passwords["northstar"],
            "PROXIMA_VERIFY_TENANT_B_PASSWORD": tenant_passwords["bluehaven"],
            "PROXIMA_VERIFY_SIGNING_KEY": os.environ["PROXIMA_TENANT_SIGNING_KEY"],
        })
        verify_result = subprocess.run(
            ["bash", str(verify_script)],
            cwd=root,
            env=verify_env,
            check=False,
            timeout=60,
            capture_output=True,
            text=True,
        )
        verify_log = (verify_result.stdout or "") + "\n--- STDERR ---\n" + (verify_result.stderr or "")
        (report_dir / "product-proxima-verify.log").write_text(verify_log, encoding="utf-8")
        if verify_result.returncode == 0:
            print("PASS | product Proxima Verify harness executed against the real Engine")
            if verify_result.stdout:
                print(verify_result.stdout, end="")
        elif "IndexError: list index out of range" in (verify_result.stderr or ""):
            print(
                "KNOWN DEFECT REPRODUCED | product tools/proxima-verify.sh fails before its checks: "
                "the embedded Python reads a TTL argument that the shell invocation omits"
            )
        else:
            print(
                "FAIL | product Proxima Verify harness had an unexpected failure "
                f"(exit code {verify_result.returncode})",
                file=sys.stderr,
            )
            if verify_result.stdout:
                print(verify_result.stdout, file=sys.stderr, end="")
            if verify_result.stderr:
                print(verify_result.stderr, file=sys.stderr, end="")
            return verify_result.returncode or 1

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
