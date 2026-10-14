"""Direct adversarial checks against the real Proxima Engine PostgreSQL listener.

Run only against the isolated Enterprise Lab database through the Engine endpoint.
The cross-tenant write probe is always rolled back, including when it unexpectedly
succeeds, so a failed policy check does not leave a persistent test row.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import struct
import sys
import time
from dataclasses import dataclass

import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict, make_conninfo

load_dotenv()


@dataclass
class Results:
    passed: int = 0
    failed: int = 0

    def check(self, label: str, ok: bool, detail: str) -> None:
        print(f'{"PASS" if ok else "FAIL"} | {label} | {detail}')
        if ok:
            self.passed += 1
        else:
            self.failed += 1


def make_token(tenant: str, *, ttl: int = 300, tamper: bool = False) -> str:
    secret = os.getenv("PROXIMA_TENANT_SIGNING_KEY", "")
    if len(secret.encode()) < 32:
        raise RuntimeError("PROXIMA_TENANT_SIGNING_KEY must be at least 32 bytes")
    payload = f"v1.{tenant}.{int(time.time()) + ttl}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if tamper:
        signature = ("0" if signature[0] != "0" else "1") + signature[1:]
    return f"{payload}.{signature}"


def connect(token: str, tenant: str = "northstar"):
    dsn = os.getenv("PROXIMA_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("PROXIMA_DATABASE_URL must point to the real Proxima Engine listener")
    try:
        passwords = json.loads(os.getenv("LAB_TENANT_DB_PASSWORDS", "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("LAB_TENANT_DB_PASSWORDS must be a JSON object") from exc
    password = passwords.get(tenant)
    if not isinstance(password, str) or not password:
        raise RuntimeError(f"No database password configured for {tenant}")
    conninfo = make_conninfo(
        dsn, user="proxima", password=password,
        options=f"-c proxima_tenant_token={token}", connect_timeout=5,
    )
    return psycopg.connect(conninfo)


def raw_probe_closes(packet: bytes) -> bool:
    """Send one bounded raw startup probe and require the Engine to close it."""
    dsn = os.getenv("PROXIMA_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("PROXIMA_DATABASE_URL must point to the real Proxima Engine listener")
    parsed = conninfo_to_dict(dsn)
    host = parsed.get("host") or "127.0.0.1"
    port = int(parsed.get("port") or 6432)
    try:
        with socket.create_connection((host, port), timeout=3) as sock:
            sock.settimeout(3)
            sock.sendall(packet)
            sock.shutdown(socket.SHUT_WR)
            received = 0
            while received < 4096:
                chunk = sock.recv(min(1024, 4096 - received))
                if not chunk:
                    return True
                received += len(chunk)
            return False
    except (ConnectionResetError, BrokenPipeError):
        return True
    except TimeoutError:
        return False


def main() -> int:
    results = Results()
    try:
        token_a = make_token("northstar")
        with connect(token_a) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM customers WHERE tenant_id = %s", ("northstar",))
            own_count = cur.fetchone()[0]
            results.check("tenant A reads its own records", own_count >= 1, f"visible rows={own_count}")
            cur.execute("SELECT current_user")
            current_role = cur.fetchone()[0]
            results.check(
                "signed tenant A selects the expected PostgreSQL role",
                current_role == "labrole_northstar",
                f"current_user={current_role}",
            )

            cur.execute("SELECT count(*) FROM customers WHERE tenant_id = %s", ("bluehaven",))
            cross_count = cur.fetchone()[0]
            results.check("tenant A cannot read tenant B records", cross_count == 0, f"visible rows={cross_count}")

            cur.execute("PREPARE elab_cross_read(text) AS SELECT count(*) FROM customers WHERE tenant_id = $1")
            cur.execute("EXECUTE elab_cross_read('bluehaven')")
            prepared_count = cur.fetchone()[0]
            results.check(
                "prepared statement cannot cross tenant boundary",
                prepared_count == 0,
                f"visible rows={prepared_count}",
            )
            cur.execute("DEALLOCATE elab_cross_read")

            cur.execute("UPDATE customers SET status = 'suspended' WHERE tenant_id = %s", ("bluehaven",))
            results.check(
                "tenant A cannot update tenant B records",
                cur.rowcount == 0,
                f"affected rows={cur.rowcount}",
            )
            cur.execute("DELETE FROM customers WHERE tenant_id = %s", ("bluehaven",))
            results.check(
                "tenant A cannot delete tenant B records",
                cur.rowcount == 0,
                f"affected rows={cur.rowcount}",
            )

            cur.execute("SAVEPOINT rollback_probe")
            cur.execute(
                """INSERT INTO customers (tenant_id, name, email, status)
                   VALUES ('northstar', 'ROLLBACK-ONLY PROBE', %s, 'active')""",
                (f"rollback-probe-own-{time.time_ns()}@example.test",),
            )
            cur.execute("ROLLBACK TO SAVEPOINT rollback_probe")
            cur.execute("RELEASE SAVEPOINT rollback_probe")
            cur.execute("SELECT count(*) FROM customers WHERE name = 'ROLLBACK-ONLY PROBE'")
            rollback_count = cur.fetchone()[0]
            results.check(
                "transaction rollback leaves no probe record",
                rollback_count == 0,
                f"visible probe rows={rollback_count}",
            )

            # Always roll back this write probe, even if a broken boundary allows it.
            cur.execute("SAVEPOINT cross_tenant_write_probe")
            write_blocked = False
            try:
                cur.execute(
                    """INSERT INTO customers (tenant_id, name, email, status)
                       VALUES ('bluehaven', 'ROLLBACK-ONLY SECURITY PROBE',
                               %s, 'active')""",
                    (f"rollback-probe-{time.time_ns()}@example.test",),
                )
            except psycopg.Error as exc:
                # SQLSTATE 42501 is PostgreSQL's insufficient_privilege response,
                # which is the expected RLS WITH CHECK rejection.
                write_blocked = exc.sqlstate == "42501"
            finally:
                cur.execute("ROLLBACK TO SAVEPOINT cross_tenant_write_probe")
                cur.execute("RELEASE SAVEPOINT cross_tenant_write_probe")
            results.check(
                "tenant A cannot insert a tenant B record",
                write_blocked,
                "RLS rejected the cross-tenant insert" if write_blocked else "insert was unexpectedly allowed (rolled back)",
            )

            # Expand the database-level boundary checks beyond customers. These
            # probes use only synthetic values and every cross-tenant write is
            # isolated behind a savepoint so an unexpected success is rolled back.
            cross_tables = {
                "invoices": "SELECT count(*) FROM invoices WHERE tenant_id = %s",
                "employees": "SELECT count(*) FROM employees WHERE tenant_id = %s",
                "projects": "SELECT count(*) FROM projects WHERE tenant_id = %s",
                "documents": "SELECT count(*) FROM documents WHERE tenant_id = %s",
            }
            for table, query in cross_tables.items():
                cur.execute(query, ("bluehaven",))
                visible = cur.fetchone()[0]
                results.check(
                    f"tenant A cannot read tenant B {table}",
                    visible == 0,
                    f"visible rows={visible}",
                )
                cur.execute(f"UPDATE {table} SET tenant_id = %s WHERE tenant_id = %s", ("northstar", "bluehaven"))
                results.check(
                    f"tenant A cannot update tenant B {table}",
                    cur.rowcount == 0,
                    f"affected rows={cur.rowcount}",
                )
                cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", ("bluehaven",))
                results.check(
                    f"tenant A cannot delete tenant B {table}",
                    cur.rowcount == 0,
                    f"affected rows={cur.rowcount}",
                )

            # Obtain a legitimate Bluehaven customer ID using Bluehaven's own
            # context so the invoice probe cannot fail merely due to a bad FK.
            with connect(make_token("bluehaven"), "bluehaven") as blue_conn, blue_conn.cursor() as blue_cur:
                blue_cur.execute("SELECT id FROM customers ORDER BY id LIMIT 1")
                blue_customer = blue_cur.fetchone()[0]

            write_probes = [
                (
                    "invoices",
                    """INSERT INTO invoices
                       (tenant_id, customer_id, amount_cents, currency, description)
                       VALUES ('bluehaven', %s, 12345, 'USD', %s)""",
                    (blue_customer, f"RLS probe {time.time_ns()}"),
                ),
                (
                    "employees",
                    """INSERT INTO employees
                       (tenant_id, name, email, department, job_title, status)
                       VALUES ('bluehaven', 'RLS Probe', %s, 'Security', 'Tester', 'active')""",
                    (f"rls-probe-{time.time_ns()}@example.test",),
                ),
                (
                    "projects",
                    """INSERT INTO projects
                       (tenant_id, name, description, budget_cents, status)
                       VALUES ('bluehaven', %s, 'Synthetic RLS probe', 0, 'planned')""",
                    (f"RLS probe {time.time_ns()}",),
                ),
                (
                    "documents",
                    """INSERT INTO documents
                       (tenant_id, title, classification, body)
                       VALUES ('bluehaven', %s, 'confidential', 'Synthetic RLS probe')""",
                    (f"RLS probe {time.time_ns()}",),
                ),
            ]
            for table, statement, parameters in write_probes:
                cur.execute("SAVEPOINT cross_table_write_probe")
                blocked = False
                try:
                    cur.execute(statement, parameters)
                except psycopg.Error as exc:
                    blocked = exc.sqlstate == "42501"
                finally:
                    cur.execute("ROLLBACK TO SAVEPOINT cross_table_write_probe")
                    cur.execute("RELEASE SAVEPOINT cross_table_write_probe")
                results.check(
                    f"tenant A cannot insert tenant B {table}",
                    blocked,
                    "RLS rejected cross-tenant insert" if blocked else "insert unexpectedly allowed (rolled back)",
                )

        # Open fresh connections as tenants B and C, then reconnect as A. The
        # selected PostgreSQL role must follow each signed startup token.
        for tenant, other_tenants in (
            ("bluehaven", ("northstar", "cedarstone")),
            ("cedarstone", ("northstar", "bluehaven")),
        ):
            token = make_token(tenant)
            with connect(token, tenant) as conn, conn.cursor() as cur:
                cur.execute("SELECT current_user")
                current_role = cur.fetchone()[0]
                results.check(
                    f"{tenant} selects its expected PostgreSQL role",
                    current_role == f"labrole_{tenant}",
                    f"current_user={current_role}",
                )
                cur.execute("SELECT count(*) FROM customers WHERE tenant_id = %s", (tenant,))
                own_count = cur.fetchone()[0]
                results.check(
                    f"{tenant} reads its own records",
                    own_count >= 1,
                    f"visible rows={own_count}",
                )
                for other_tenant in other_tenants:
                    cur.execute("SELECT count(*) FROM customers WHERE tenant_id = %s", (other_tenant,))
                    cross_count = cur.fetchone()[0]
                    results.check(
                        f"{tenant} cannot read {other_tenant} records",
                        cross_count == 0,
                        f"visible rows={cross_count}",
                    )

        token_a_again = make_token("northstar")
        with connect(token_a_again, "northstar") as conn, conn.cursor() as cur:
            cur.execute("SELECT current_user")
            current_role = cur.fetchone()[0]
            results.check(
                "reconnecting as tenant A restores tenant A context",
                current_role == "labrole_northstar",
                f"current_user={current_role}",
            )

        # A valid signature with an expired timestamp must be rejected by the Engine.
        for label, token in (
            ("expired tenant context rejected", make_token("northstar", ttl=-60)),
            ("tampered tenant signature rejected", make_token("northstar", tamper=True)),
        ):
            rejected = False
            try:
                with connect(token) as conn, conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            except (psycopg.Error, RuntimeError):
                rejected = True
            results.check(label, rejected, "connection/query rejected" if rejected else "request unexpectedly succeeded")

        # Send malformed traffic to the actual Engine listener. These are tiny,
        # bounded probes: no flood and no attempt to consume significant memory.
        invalid_length_packet = struct.pack("!i", 4) + b"\x00" * 4
        malformed_closed = raw_probe_closes(invalid_length_packet)
        results.check(
            "malformed short startup frame closes connection",
            malformed_closed,
            "connection closed" if malformed_closed else "connection remained open until probe timeout",
        )

        # Send the full 8-byte initial probe because the Engine first distinguishes
        # PostgreSQL SSLRequest packets before parsing the startup length.
        oversized_header = struct.pack("!i", 16 * 1024 * 1024 + 1) + struct.pack("!i", 196608)
        oversized_closed = raw_probe_closes(oversized_header)
        results.check(
            "oversized startup frame rejected before payload allocation",
            oversized_closed,
            "connection closed" if oversized_closed else "connection remained open until probe timeout",
        )

        startup_body = struct.pack("!i", 196608) + b"user\x00proxima\x00database\x00enterprise_lab\x00\x00"
        missing_token_packet = struct.pack("!i", len(startup_body) + 4) + startup_body
        missing_token_closed = raw_probe_closes(missing_token_packet)
        results.check(
            "valid-shaped startup without tenant token rejected",
            missing_token_closed,
            "connection closed" if missing_token_closed else "connection remained open until probe timeout",
        )

    except (psycopg.Error, RuntimeError, ValueError, TypeError, OSError) as exc:
        print(f"FATAL | protocol suite could not complete | {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"\nProxima protocol suite: {results.passed} passed, {results.failed} failed")
    return 0 if results.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
