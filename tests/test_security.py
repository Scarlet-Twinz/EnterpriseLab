import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import make_proxima_token


def test_signed_token_uses_expected_wire_format(monkeypatch):
    monkeypatch.setenv("PROXIMA_TENANT_SIGNING_KEY", "test-secret-that-is-long-enough-123456")
    token = make_proxima_token("tenant_a", now=1_700_000_000, ttl_seconds=60)
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(
        b"test-secret-that-is-long-enough-123456", payload.encode(), hashlib.sha256
    ).hexdigest()
    assert payload == "v1.tenant_a.1700000060"
    assert hmac.compare_digest(signature, expected)


def test_invalid_tenant_id_is_rejected(monkeypatch):
    monkeypatch.setenv("PROXIMA_TENANT_SIGNING_KEY", "test-secret-that-is-long-enough-123456")
    with pytest.raises(ValueError):
        make_proxima_token("tenant-a;drop-table", now=100, ttl_seconds=60)


def test_short_signing_key_is_rejected(monkeypatch):
    monkeypatch.setenv("PROXIMA_TENANT_SIGNING_KEY", "too-short")
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        make_proxima_token("tenant_a", now=100, ttl_seconds=60)


def test_api_key_maps_to_server_side_tenant(monkeypatch):
    monkeypatch.setenv(
        "LAB_API_KEYS",
        json.dumps({"test-key-a": {"tenant_id": "tenant_a", "role": "admin"}}),
    )
    client = TestClient(app)
    response = client.get("/api/v1/me", headers={"Authorization": "Bearer test-key-a"})
    assert response.status_code == 200
    assert response.json() == {"tenant_id": "tenant_a", "role": "admin"}


def test_caller_cannot_select_tenant_with_header(monkeypatch):
    monkeypatch.setenv(
        "LAB_API_KEYS",
        json.dumps({"test-key-a": {"tenant_id": "tenant_a", "role": "admin"}}),
    )
    client = TestClient(app)
    response = client.get(
        "/api/v1/me",
        headers={"Authorization": "Bearer test-key-a", "X-Lab-Tenant": "tenant_b"},
    )
    assert response.status_code == 200
    assert response.json()["tenant_id"] == "tenant_a"


def test_unknown_api_key_is_rejected(monkeypatch):
    monkeypatch.setenv("LAB_API_KEYS", json.dumps({"known": {"tenant_id": "tenant_a", "role": "admin"}}))
    client = TestClient(app)
    response = client.get("/api/v1/me", headers={"Authorization": "Bearer unknown"})
    assert response.status_code == 401


def test_viewer_cannot_create_business_records(monkeypatch):
    monkeypatch.setenv(
        "LAB_API_KEYS",
        json.dumps({"viewer-key": {"tenant_id": "tenant_c", "role": "viewer"}}),
    )
    client = TestClient(app)
    response = client.post(
        "/api/v1/customers",
        headers={"Authorization": "Bearer viewer-key"},
        json={"name": "Attempted Admin", "email": "nope@example.test"},
    )
    assert response.status_code == 403



def test_metrics_endpoint_exposes_prometheus_format():
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "enterprise_lab_process_up 1" in response.text
    assert "# TYPE enterprise_lab_http_requests_total counter" in response.text
