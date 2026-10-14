"""Database access through the real Proxima Engine only."""
from __future__ import annotations

import json
import os

import psycopg
from psycopg import Connection
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.security import Principal, make_proxima_token


def _tenant_passwords() -> dict[str, str]:
    raw = os.getenv("LAB_TENANT_DB_PASSWORDS", "{}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LAB_TENANT_DB_PASSWORDS must be a JSON object") from exc
    if not isinstance(value, dict) or any(
        not isinstance(tenant, str) or not isinstance(password, str) or not password
        for tenant, password in value.items()
    ):
        raise RuntimeError("LAB_TENANT_DB_PASSWORDS must map tenant IDs to non-empty passwords")
    return value


def connect_as(principal: Principal) -> Connection:
    """Open a tenant-bound connection through Proxima, using that DB role's password.

    The Engine rewrites the startup username to its tenant-role prefix plus tenant ID.
    Its startup authentication broker forwards the password, so the trusted backend must
    select the matching tenant role password. There is deliberately no direct-DB fallback.
    """
    dsn = os.getenv("PROXIMA_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("PROXIMA_DATABASE_URL must point to the Proxima Engine")
    password = _tenant_passwords().get(principal.tenant_id)
    if not password:
        raise RuntimeError(f"No database password configured for tenant {principal.tenant_id}")
    token = make_proxima_token(principal.tenant_id)
    conninfo = make_conninfo(
        dsn,
        user="proxima",
        password=password,
        options=f"-c proxima_tenant_token={token}",
        connect_timeout=5,
    )
    return psycopg.connect(conninfo, row_factory=dict_row)
