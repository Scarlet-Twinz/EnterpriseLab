import json

import pytest

from app import db
from app.security import Principal


def test_database_connection_uses_engine_endpoint_and_tenant_password(monkeypatch):
    monkeypatch.setenv(
        "PROXIMA_DATABASE_URL",
        "postgresql://proxima@127.0.0.1:6432/enterprise_lab?sslmode=disable",
    )
    monkeypatch.setenv(
        "LAB_TENANT_DB_PASSWORDS",
        json.dumps({"northstar": "northstar-db-password", "bluehaven": "bluehaven-db-password"}),
    )
    monkeypatch.setenv("PROXIMA_TENANT_SIGNING_KEY", "test-signing-key-that-is-at-least-32-bytes")
    captured = {}

    def fake_make_conninfo(dsn, **kwargs):
        captured["dsn"] = dsn
        captured.update(kwargs)
        return "normalized-engine-dsn"

    def fake_connect(conninfo, *, row_factory):
        captured["conninfo"] = conninfo
        captured["row_factory"] = row_factory
        return "connection"

    monkeypatch.setattr(db, "make_conninfo", fake_make_conninfo)
    monkeypatch.setattr(db.psycopg, "connect", fake_connect)

    result = db.connect_as(Principal(tenant_id="northstar", role="admin"))

    assert result == "connection"
    assert captured["dsn"].startswith("postgresql://proxima@127.0.0.1:6432/")
    assert captured["user"] == "proxima"
    assert captured["password"] == "northstar-db-password"
    assert captured["options"].startswith("-c proxima_tenant_token=v1.northstar.")
    assert captured["connect_timeout"] == 5


def test_database_connection_never_falls_back_to_admin_dsn(monkeypatch):
    monkeypatch.delenv("PROXIMA_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "LAB_ADMIN_DATABASE_URL",
        "postgresql://admin:admin-password@127.0.0.1:55432/enterprise_lab",
    )
    monkeypatch.setenv("LAB_TENANT_DB_PASSWORDS", json.dumps({"northstar": "tenant-password"}))
    monkeypatch.setenv("PROXIMA_TENANT_SIGNING_KEY", "test-signing-key-that-is-at-least-32-bytes")

    with pytest.raises(RuntimeError, match="must point to the Proxima Engine"):
        db.connect_as(Principal(tenant_id="northstar", role="admin"))


def test_database_connection_fails_closed_when_tenant_password_missing(monkeypatch):
    monkeypatch.setenv(
        "PROXIMA_DATABASE_URL",
        "postgresql://proxima@127.0.0.1:6432/enterprise_lab?sslmode=disable",
    )
    monkeypatch.setenv("LAB_TENANT_DB_PASSWORDS", json.dumps({"bluehaven": "bluehaven-db-password"}))
    monkeypatch.setenv("PROXIMA_TENANT_SIGNING_KEY", "test-signing-key-that-is-at-least-32-bytes")

    with pytest.raises(RuntimeError, match="No database password configured for tenant northstar"):
        db.connect_as(Principal(tenant_id="northstar", role="admin"))
