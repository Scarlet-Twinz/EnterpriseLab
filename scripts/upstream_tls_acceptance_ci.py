"""Exercise the real Engine's upstream PostgreSQL TLS verify-full contract in isolated CI."""
from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo

from app.security import make_proxima_token


def run(command: list[str], *, check: bool = True, timeout: int = 30, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=check, capture_output=True, text=True, timeout=timeout, env=env)


def wait_port(host: str, port: int, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"{host}:{port} did not become ready")


def wait_engine(process: subprocess.Popen, log: Path, port: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Engine exited before listening on {port}; see {log}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        raise RuntimeError(f"Engine did not listen on {port}; see {log}")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        normalized = text.replace(" ", "")
        if "tenant_enforcement=true" in normalized:
            return
        if process.poll() is not None:
            raise RuntimeError(f"Engine exited before confirming tenant enforcement; see {log}")
        time.sleep(0.2)
    raise RuntimeError(f"Engine did not explicitly report tenant_enforcement=true; see {log}")


def stop(process: subprocess.Popen | None) -> None:
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
    binary = Path(os.getenv("AGATA_ENGINE_BINARY", str(root / "agata-proxima/target/debug/proxima-engine"))).resolve()
    if not binary.is_file():
        raise RuntimeError(f"Real Proxima Engine binary not found: {binary}")

    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    tenant_passwords = {
        "northstar": os.environ["LAB_TENANT_A_DB_PASSWORD"],
        "bluehaven": os.environ["LAB_TENANT_B_DB_PASSWORD"],
        "cedarstone": os.environ["LAB_TENANT_C_DB_PASSWORD"],
    }
    db_container = "enterprise-lab-upstream-tls-postgres"
    engine_process: subprocess.Popen | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="proxima-upstream-tls-") as tmp:
            tlsdir = Path(tmp)
            ca_key, ca_cert = tlsdir / "ca.key", tlsdir / "ca.crt"
            server_key, server_csr, server_cert = tlsdir / "server.key", tlsdir / "server.csr", tlsdir / "server.crt"
            ext = tlsdir / "server.ext"
            run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", str(ca_key), "-out", str(ca_cert), "-days", "2",
                 "-subj", "/CN=Enterprise Lab Test CA"])
            run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", str(server_key), "-out", str(server_csr),
                 "-subj", "/CN=postgres-tls-test"])
            ext.write_text("subjectAltName=DNS:postgres-tls-test\nextendedKeyUsage=serverAuth\n", encoding="utf-8")
            run(["openssl", "x509", "-req", "-in", str(server_csr), "-CA", str(ca_cert),
                 "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(server_cert),
                 "-days", "2", "-extfile", str(ext)])
            rogue_key, rogue_ca = tlsdir / "rogue-ca.key", tlsdir / "rogue-ca.crt"
            run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", str(rogue_key), "-out", str(rogue_ca), "-days", "2",
                 "-subj", "/CN=Untrusted Lab CA"])

            # Mount a disposable certificate directory with PostgreSQL-compatible key ownership/mode.
            run(["sudo", "chown", "70:70", str(server_key)])
            run(["sudo", "chmod", "600", str(server_key)])
            run(["sudo", "chown", "999:999", str(server_cert)])
            run(["sudo", "chmod", "644", str(server_cert)])
            run(["docker", "create", "--name", db_container,
                 "-e", "POSTGRES_DB=enterprise_lab",
                 "-e", "POSTGRES_USER=lab_admin",
                 "-e", "POSTGRES_PASSWORD=ci-admin-password",
                 "-p", "127.0.0.1:5544:5432",
                 "--mount", f"type=bind,source={tlsdir},target=/certs,readonly",
                 "postgres:16-alpine", "postgres", "-c", "ssl=on",
                 "-c", "ssl_cert_file=/certs/server.crt",
                 "-c", "ssl_key_file=/certs/server.key"])
            run(["docker", "start", db_container])
            wait_port("127.0.0.1", 5544, 40)

            db_env = os.environ.copy()
            db_env.update({"PGHOST": "127.0.0.1", "PGPORT": "5544", "PGUSER": "lab_admin",
                           "PGPASSWORD": "ci-admin-password", "PGDATABASE": "enterprise_lab",
                           "POSTGRES_USER": "lab_admin", "POSTGRES_DB": "enterprise_lab"})
            run(["psql", "-v", "ON_ERROR_STOP=1", "-f", str(root / "db/schema.sql")], env=db_env)
            run(["sh", str(root / "db/02-create-tenant-roles.sh")], env=db_env)
            # Explicit PG* environment ensures setup targets the disposable TLS server.

            cases = [
                ("valid CA and matching hostname", str(ca_cert), "postgres-tls-test", True, 6435),
                ("untrusted CA fails closed", str(rogue_ca), "postgres-tls-test", False, 6436),
                ("hostname mismatch fails closed", str(ca_cert), "wrong-postgres-name", False, 6437),
            ]
            for label, trust_file, server_name, should_succeed, listen_port in cases:
                engine_log = reports / f"upstream-tls-{listen_port}.log"
                engine_env = os.environ.copy()
                engine_env.update({
                    "PROXIMA_LISTEN_ADDR": f"127.0.0.1:{listen_port}",
                    "PROXIMA_UPSTREAM_ADDR": "127.0.0.1:5544",
                    "PROXIMA_TENANT_ROLE_PREFIX": "labrole_",
                    "PROXIMA_TENANT_SIGNING_KEY": os.environ["PROXIMA_TENANT_SIGNING_KEY"],
                    "PROXIMA_UPSTREAM_TLS_MODE": "verify-full",
                    "PROXIMA_UPSTREAM_TLS_CA_FILE": trust_file,
                    "PROXIMA_UPSTREAM_TLS_SERVER_NAME": server_name,
                    "PROXIMA_DASHBOARD_ENABLED": "false",
                    "PROXIMA_DASHBOARD_LISTEN_ADDR": f"127.0.0.1:{9480 + listen_port - 6435}",
                })
                with engine_log.open("w", encoding="utf-8") as log:
                    engine_process = subprocess.Popen([str(binary)], cwd=binary.parent.parent.parent,
                                                       env=engine_env, stdout=log, stderr=subprocess.STDOUT)
                    wait_engine(engine_process, engine_log, listen_port)
                    token = make_proxima_token("northstar")
                    conninfo = make_conninfo(
                        f"postgresql://proxima@127.0.0.1:{listen_port}/enterprise_lab?sslmode=disable",
                        user="proxima", password=tenant_passwords["northstar"],
                        options=f"-c proxima_tenant_token={token}", connect_timeout=5,
                    )
                    query_succeeded = False
                    try:
                        with psycopg.connect(conninfo) as conn, conn.cursor() as cur:
                            cur.execute("SELECT current_user, count(*) FROM customers WHERE tenant_id = %s GROUP BY current_user", ("northstar",))
                            row = cur.fetchone()
                            query_succeeded = bool(row and row[0] == "labrole_northstar" and row[1] >= 1)
                    except psycopg.Error:
                        query_succeeded = False
                    finally:
                        stop(engine_process)
                        engine_process = None
                if query_succeeded != should_succeed:
                    raise RuntimeError(f"FAIL | upstream TLS {label} | expected success={should_succeed}, got {query_succeeded}; see {engine_log}")
                print(f"PASS | upstream TLS verify-full | {label}")
            (reports / "upstream-tls-acceptance.txt").write_text(
                "PASS | valid CA + matching hostname succeeds\n"
                "PASS | untrusted CA fails closed\n"
                "PASS | hostname mismatch fails closed\n", encoding="utf-8")
        print("PASS | all upstream PostgreSQL TLS verify-full cases completed against real Engine")
        return 0
    finally:
        stop(engine_process)
        subprocess.run(["docker", "rm", "-f", db_container], capture_output=True, text=True, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
