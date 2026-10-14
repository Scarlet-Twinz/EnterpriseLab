import json

from fastapi.testclient import TestClient

from app import main as main_module


def _configure(monkeypatch, key, tenant, role):
    monkeypatch.setenv("LAB_API_KEYS", json.dumps({key: {"tenant_id": tenant, "role": role}}))


def test_viewer_can_only_list_internal_documents(monkeypatch):
    _configure(monkeypatch, "viewer-key", "cedarstone", "viewer")
    observed = {}

    def fake_run(principal, sql, params=(), *, one=False):
        observed["sql"] = sql
        observed["params"] = params
        return []

    monkeypatch.setattr(main_module, "_run", fake_run)
    response = TestClient(main_module.app).get(
        "/api/v1/documents",
        headers={"Authorization": "Bearer viewer-key"},
    )
    assert response.status_code == 200
    assert "classification = 'internal'" in observed["sql"]
    assert observed["params"] == ("cedarstone",)


def test_operator_cannot_create_restricted_document(monkeypatch):
    _configure(monkeypatch, "operator-key", "bluehaven", "operator")
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(main_module, "_run", fake_run)
    response = TestClient(main_module.app).post(
        "/api/v1/documents",
        headers={"Authorization": "Bearer operator-key"},
        json={"title": "Restricted", "classification": "restricted", "body": "Synthetic test"},
    )
    assert response.status_code == 403
    assert called is False


def test_admin_can_create_restricted_document(monkeypatch):
    _configure(monkeypatch, "admin-key", "northstar", "admin")
    monkeypatch.setattr(
        main_module,
        "_run",
        lambda *args, **kwargs: {
            "id": 7,
            "title": "Restricted",
            "classification": "restricted",
            "body": "Synthetic test",
        },
    )
    response = TestClient(main_module.app).post(
        "/api/v1/documents",
        headers={"Authorization": "Bearer admin-key"},
        json={"title": "Restricted", "classification": "restricted", "body": "Synthetic test"},
    )
    assert response.status_code == 201
    assert response.json()["classification"] == "restricted"



def test_customer_body_cannot_override_tenant_identity(monkeypatch):
    _configure(monkeypatch, "tenant-a-key", "northstar", "admin")
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(main_module, "_run", fake_run)
    response = TestClient(main_module.app).post(
        "/api/v1/customers",
        headers={"Authorization": "Bearer tenant-a-key"},
        json={
            "tenant_id": "bluehaven",
            "name": "Forged tenant customer",
            "email": "forged@example.test",
            "status": "active",
        },
    )

    assert response.status_code == 422
    assert called is False
