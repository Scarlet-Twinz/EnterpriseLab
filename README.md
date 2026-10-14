# Enterprise Lab

Enterprise Lab is a fictional, backend-only enterprise customer used to validate the real Agata Proxima product before launch. It is a separate repository and must never be treated as part of Agata Proxima itself.

The company simulator owns realistic business workflows and synthetic data. Its application database traffic is configured to pass through the **real Proxima Engine**. Security tests record observed outcomes; they do not treat mocks or direct PostgreSQL connections as proof of Proxima enforcement.

## First implementation

- FastAPI business API for customers, employees, invoices, and confidential documents.
- PostgreSQL schema with tenant-specific roles and row-level security (RLS).
- Server-side API-key identity mapping. The caller cannot choose the tenant context by sending a tenant header or body field.
- HMAC-signed Proxima tenant-context tokens passed through PostgreSQL startup options.
- An adversarial test runner for cross-tenant access, forged identity attempts, invalid credentials, malformed requests, and controlled request bursts.
- Explicit separation between administrative/bootstrap database access and application traffic through Proxima.

## Critical integration contract

Set `PROXIMA_DATABASE_URL` to the **Proxima Engine endpoint**, not the upstream PostgreSQL endpoint. Each business request opens a database connection with a signed `proxima_tenant_token` startup option. The Engine must verify the token and map the tenant to the intended PostgreSQL role.

Tenant IDs used in the lab are `tenant_a`, `tenant_b`, and `tenant_c`; the corresponding PostgreSQL roles must be configured in the Engine/database. The schema's RLS policy assumes the selected role name equals the tenant ID. Adjust the mapping deliberately if the real deployment uses a different role convention.

Administrative schema setup is separate and uses `LAB_ADMIN_DATABASE_URL` against the upstream test database. The application never falls back to that URL.

## Quick start

1. Copy `.env.example` to `.env` and replace the local-only values.
2. Create an isolated PostgreSQL test database and configure tenant roles/RLS using `db/schema.sql` with an administrative connection.
3. Configure the Proxima Engine against that database and set `PROXIMA_DATABASE_URL` to the Engine listener.
4. Install and run:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --host 127.0.0.1 --port 8088
```

Health check: `GET http://127.0.0.1:8088/healthz`

## Run checks

```bash
pytest -q
python scripts/attack_suite.py
```

The attack suite requires the API to be running and the sample API keys from your local `.env` (or equivalent environment variables) to be configured. It must only be pointed at the isolated Enterprise Lab test deployment.

## Environment variables

- `LAB_API_KEYS`: JSON object mapping API keys to trusted server-side identities, e.g. `{"replace-with-key-a":{"tenant_id":"tenant_a","role":"admin"}}`.
- `PROXIMA_SIGNING_KEY`: secret used to sign tenant context; keep it in a secret store or local environment, never commit it.
- `PROXIMA_DATABASE_URL`: application DSN pointing to Proxima Engine.
- `LAB_ADMIN_DATABASE_URL`: optional upstream admin DSN for manual schema setup only; never used by request handlers.
- `PROXIMA_TOKEN_TTL_SECONDS`: signed tenant-context lifetime (default 300 seconds).
- `LAB_BASE_URL`, `LAB_API_KEY_A`, `LAB_API_KEY_B`: attack-suite target and test identities.

The example values are for local-only development. Never use them in a deployed environment. No real customer data or production credentials belong in this repository.
