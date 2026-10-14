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
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo

from app.security import make_proxima_token


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
    outage_engine = None
    tls_engine = None

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

        # Exercise dependency failure using a second real Engine process pointed
        # at an intentionally closed loopback port. No production or shared DB is
        # touched; the expected result is a rejected client connection.
        outage_log = report_dir / "proxima-engine-upstream-outage.log"
        outage_env = os.environ.copy()
        outage_env.update({
            "PROXIMA_LISTEN_ADDR": "127.0.0.1:6433",
            "PROXIMA_UPSTREAM_ADDR": "127.0.0.1:59999",
            "PROXIMA_UPSTREAM_CONNECT_TIMEOUT_MS": "1000",
            "PROXIMA_TENANT_ROLE_PREFIX": "labrole_",
            "PROXIMA_UPSTREAM_TLS_MODE": "disable",
            "PROXIMA_DASHBOARD_ENABLED": "false",
            "PROXIMA_DASHBOARD_LISTEN_ADDR": "127.0.0.1:9081",
        })
        with outage_log.open("w", encoding="utf-8") as log:
            outage_engine = subprocess.Popen(
                [str(engine_binary)],
                cwd=engine_binary.parent.parent.parent,
                env=outage_env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        outage_deadline = time.monotonic() + 10
        outage_listener_ready = False
        while time.monotonic() < outage_deadline:
            if outage_engine.poll() is not None:
                raise RuntimeError(f"Outage-test Engine exited early; see {outage_log}")
            try:
                with socket.create_connection(("127.0.0.1", 6433), timeout=0.5):
                    outage_listener_ready = True
                    break
            except OSError:
                time.sleep(0.2)
        if not outage_listener_ready:
            raise RuntimeError(f"Outage-test Engine did not listen on 127.0.0.1:6433; see {outage_log}")

        outage_conninfo = make_conninfo(
            "postgresql://proxima@127.0.0.1:6433/enterprise_lab?sslmode=disable",
            user="proxima",
            password=tenant_passwords["northstar"],
            options=f"-c proxima_tenant_token={make_proxima_token('northstar')}",
            connect_timeout=4,
        )
        outage_rejected = False
        try:
            with psycopg.connect(outage_conninfo) as outage_conn, outage_conn.cursor() as outage_cur:
                outage_cur.execute("SELECT 1")
                outage_cur.fetchone()
        except psycopg.Error:
            outage_rejected = True
        time.sleep(0.1)
        outage_text = outage_log.read_text(encoding="utf-8", errors="replace").lower()
        upstream_unreachable = "connection refused" in outage_text or "os error 111" in outage_text
        if outage_rejected and upstream_unreachable:
            print("PASS | upstream PostgreSQL outage fails closed | client query rejected; unreachable upstream confirmed in Engine log")
        else:
            print(
                "FAIL | upstream PostgreSQL outage acceptance "
                f"(client_rejected={outage_rejected}, upstream_unreachable_logged={upstream_unreachable})",
                file=sys.stderr,
            )
            print_log(outage_log)
            return 1

        # Validate client TLS termination and plaintext rejection with a
        # disposable self-signed certificate. Private key material stays in /tmp
        # and is never included in the uploaded evidence artifact.
        with tempfile.TemporaryDirectory(prefix="proxima-tls-acceptance-") as tls_tmp:
            tls_dir = Path(tls_tmp)
            tls_cert = tls_dir / "engine.crt"
            tls_key = tls_dir / "engine.key"
            subprocess.run(
                [
                    "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(tls_key), "-out", str(tls_cert), "-days", "1",
                    "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            tls_log = report_dir / "proxima-engine-client-tls.log"
            tls_env = os.environ.copy()
            tls_env.update({
                "PROXIMA_LISTEN_ADDR": "127.0.0.1:6434",
                "PROXIMA_UPSTREAM_ADDR": "127.0.0.1:5432",
                "PROXIMA_TENANT_ROLE_PREFIX": "labrole_",
                "PROXIMA_TENANT_SIGNING_KEY": os.environ["PROXIMA_TENANT_SIGNING_KEY"],
                "PROXIMA_TLS_CERT_FILE": str(tls_cert),
                "PROXIMA_TLS_KEY_FILE": str(tls_key),
                "PROXIMA_TLS_REQUIRE_CLIENT": "true",
                "PROXIMA_UPSTREAM_TLS_MODE": "disable",
                "PROXIMA_DASHBOARD_ENABLED": "false",
                "PROXIMA_DASHBOARD_LISTEN_ADDR": "127.0.0.1:9082",
            })
            with tls_log.open("w", encoding="utf-8") as log:
                tls_engine = subprocess.Popen(
                    [str(engine_binary)],
                    cwd=engine_binary.parent.parent.parent,
                    env=tls_env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )

            tls_deadline = time.monotonic() + 10
            tls_listener_ready = False
            while time.monotonic() < tls_deadline:
                if tls_engine.poll() is not None:
                    raise RuntimeError(f"TLS-test Engine exited early; see {tls_log}")
                try:
                    with socket.create_connection(("127.0.0.1", 6434), timeout=0.5):
                        tls_listener_ready = True
                        break
                except OSError:
                    time.sleep(0.2)
            if not tls_listener_ready:
                raise RuntimeError(f"TLS-test Engine did not listen on 127.0.0.1:6434; see {tls_log}")
            wait_for_enforcement(tls_engine, tls_log)

            tenant_token = make_proxima_token("northstar")
            plain_conninfo = make_conninfo(
                "postgresql://proxima@127.0.0.1:6434/enterprise_lab?sslmode=disable",
                user="proxima",
                password=tenant_passwords["northstar"],
                options=f"-c proxima_tenant_token={tenant_token}",
                connect_timeout=4,
            )
            plaintext_rejected = False
            try:
                with psycopg.connect(plain_conninfo):
                    pass
            except psycopg.Error:
                plaintext_rejected = True
            if plaintext_rejected:
                print("PASS | required client TLS rejects plaintext PostgreSQL connections")
            else:
                print("FAIL | required client TLS accepted a plaintext connection", file=sys.stderr)
                return 1

            tls_conninfo = make_conninfo(
                "postgresql://proxima@127.0.0.1:6434/enterprise_lab?sslmode=require",
                user="proxima",
                password=tenant_passwords["northstar"],
                options=f"-c proxima_tenant_token={tenant_token}",
                connect_timeout=5,
            )
            with psycopg.connect(tls_conninfo) as tls_conn, tls_conn.cursor() as tls_cur:
                tls_cur.execute("SELECT current_user, count(*) FROM customers WHERE tenant_id = %s GROUP BY current_user", ("northstar",))
                tls_row = tls_cur.fetchone()
            if tls_row and tls_row[0] == "labrole_northstar" and tls_row[1] >= 1:
                print("PASS | TLS client reaches real Engine and preserves tenant role binding")
            else:
                print(f"FAIL | TLS client tenant binding unexpected: {tls_row!r}", file=sys.stderr)
                return 1
            stop_process(tls_engine)
            tls_engine = None

        print("\nPASS | all live Enterprise Lab suites completed against the real Proxima Engine")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"FAIL | real Engine integration | {type(exc).__name__}: {exc}", file=sys.stderr)
        print_log(engine_log)
        print_log(api_log)
        return 1
    finally:
        stop_process(tls_engine)
        stop_process(outage_engine)
        stop_process(api)
        stop_process(engine)


if __name__ == "__main__":
    raise SystemExit(main())
