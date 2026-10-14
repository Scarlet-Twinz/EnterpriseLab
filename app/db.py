"""Database access through the Proxima Engine only."""
from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
from psycopg import Connection
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.security import Principal, make_proxima_token


def connect_as(principal: Principal) -> Connection:
    """Open one tenant-bound connection through the configured Proxima Engine.

    There is deliberately no fallback to LAB_ADMIN_DATABASE_URL or direct PostgreSQL.
    """
    dsn = os.getenv("PROXIMA_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("PROXIMA_DATABASE_URL must point to the Proxima Engine")
    token = make_proxima_token(principal.tenant_id)
    conninfo = make_conninfo(dsn, options=f"-c proxima_tenant_token={token}", connect_timeout=5)
    return psycopg.connect(conninfo, row_factory=dict_row)


def connection_for(principal: Principal) -> Iterator[Connection]:
    connection = connect_as(principal)
    try:
        yield connection
    finally:
        connection.close()
