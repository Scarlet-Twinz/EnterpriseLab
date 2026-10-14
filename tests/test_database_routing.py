import json

import pytest
from psycopg.conninfo import conninfo_to_dict

import app.db as db
from app.security import Principal


def test_connection_uses_proxima_endpoint_and_tenant_specific_password(monkeypatch):
    monkeypatch.setenv("PROXIMA_DATABASE_URL", "postgresql://proxima@127.0.0.1:6432/enterprise_lab")
    monkeypatch.setenv("PROXIMA_TENANT_SIGNING_KEY", "unit-test-signing-key-that-is-at-least-32-bytes")
    monkeypatch.setenv(
        "LAB_TENANT_DB_PASSWORDS",
        json.dumps({"tenant_a": "tenant-a-secret", "tenant_b": "tenant-b-secret"}),
    )
    captured = {}

    def fake_connect(conninfo, **kwargs):
        captured["conninfo"] = conninfo
        captured["kwargs"] = kwargs
        return "fake-connection"

    monkeypatch.setattr(db.psycopg, "connect", fake_connect)
    result = db.connect_as(Principal(tenant_id="tenant_b", role="admin"))

    assert result == "fake-connection"
    parsed = conninfo_to_dict(captured["conninfo"])
    assert parsed["host"] == "127.0.0.1"
    assert parsed["port"] == "6432"
    assert parsed["dbname"] == "enterprise_lab"
    assert parsed["password"] == "tenant-b-secret"
    assert "proxima_tenant_token=v1.tenant_b." in parsed["options"]
    assert captured["kwargs"]["row_factory"] is not None


def test_missing_tenant_database_password_fails_closed(monkeypatch):
    monkeypatch.setenv("PROXIMA_DATABASE_URL", "postgresql://proxima@127.0.0.1:6432/enterprise_lab")
    monkeypatch.setenv("LAB_TENANT_DB_PASSWORDS", json.dumps({"tenant_a": "tenant-a-secret"}))
    with pytest.raises(RuntimeError, match="No database password configured for tenant tenant_b"):
        db.connect_as(Principal(tenant_id="tenant_b", role="admin"))
