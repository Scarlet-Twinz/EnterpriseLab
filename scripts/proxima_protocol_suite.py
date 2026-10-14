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
import sys
import time
from dataclasses import dataclass

import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import make_conninfo

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


def connect(token: str, tenant: str = "tenant_a"):
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


def main() -> int:
    results = Results()
    try:
        token_a = make_token("tenant_a")
        with connect(token_a) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM customers WHERE tenant_id = %s", ("tenant_a",))
            own_count = cur.fetchone()[0]
            results.check("tenant A reads its own records", own_count >= 1, f"visible rows={own_count}")
            cur.execute("SELECT current_user")
            current_role = cur.fetchone()[0]
            results.check(
                "signed tenant A selects the expected PostgreSQL role",
                current_role == "proxima_tenant_tenant_a",
                f"current_user={current_role}",
            )

            cur.execute("SELECT count(*) FROM customers WHERE tenant_id = %s", ("tenant_b",))
            cross_count = cur.fetchone()[0]
            results.check("tenant A cannot read tenant B records", cross_count == 0, f"visible rows={cross_count}")

            cur.execute("PREPARE elab_cross_read(text) AS SELECT count(*) FROM customers WHERE tenant_id = $1")
            cur.execute("EXECUTE elab_cross_read('tenant_b')")
            prepared_count = cur.fetchone()[0]
            results.check(
                "prepared statement cannot cross tenant boundary",
                prepared_count == 0,
                f"visible rows={prepared_count}",
            )
            cur.execute("DEALLOCATE elab_cross_read")

            cur.execute("UPDATE customers SET status = 'suspended' WHERE tenant_id = %s", ("tenant_b",))
            results.check(
                "tenant A cannot update tenant B records",
                cur.rowcount == 0,
                f"affected rows={cur.rowcount}",
            )
            cur.execute("DELETE FROM customers WHERE tenant_id = %s", ("tenant_b",))
            results.check(
                "tenant A cannot delete tenant B records",
                cur.rowcount == 0,
                f"affected rows={cur.rowcount}",
            )

            cur.execute("SAVEPOINT rollback_probe")
            cur.execute(
                """INSERT INTO customers (tenant_id, name, email, status)
                   VALUES ('tenant_a', 'ROLLBACK-ONLY PROBE', %s, 'active')""",
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
                       VALUES ('tenant_b', 'ROLLBACK-ONLY SECURITY PROBE',
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

        # A valid signature with an expired timestamp must be rejected by the Engine.
        for label, token in (
            ("expired tenant context rejected", make_token("tenant_a", ttl=-60)),
            ("tampered tenant signature rejected", make_token("tenant_a", tamper=True)),
        ):
            rejected = False
            try:
                with connect(token) as conn, conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            except (psycopg.Error, RuntimeError):
                rejected = True
            results.check(label, rejected, "connection/query rejected" if rejected else "request unexpectedly succeeded")

    except (psycopg.Error, RuntimeError, ValueError, TypeError, OSError) as exc:
        print(f"FATAL | protocol suite could not complete | {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"\nProxima protocol suite: {results.passed} passed, {results.failed} failed")
    return 0 if results.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
