# Enterprise Lab

Enterprise Lab is a fictional, backend-only enterprise customer used to validate the real Agata Proxima product before launch. It is a separate repository and must never be treated as part of Agata Proxima itself.

The company simulator owns realistic business workflows and synthetic data. Its application database traffic is configured to pass through the **real Proxima Engine**. Security tests record observed outcomes; they do not treat mocks or direct PostgreSQL connections as proof of Proxima enforcement.

## First implementation

Read the [simulated US customer profile](docs/COMPANY_PROFILE.md) for the business scenario and acceptance goals.

- FastAPI business API for customers, employees, projects, invoices, and confidential documents.
- PostgreSQL schema with tenant-specific roles and row-level security (RLS).
- Server-side API-key identity mapping. The caller cannot choose the tenant context by sending a tenant header or body field.
- HMAC-signed Proxima tenant-context tokens passed through PostgreSQL startup options.
- A Prometheus `/metrics` endpoint and request logs with request IDs and latency.
- An adversarial test runner for cross-tenant access, forged identity attempts, invalid credentials, malformed requests, and controlled request bursts.
- Explicit separation between administrative/bootstrap database access and application traffic through Proxima.

## Critical integration contract

Set `PROXIMA_DATABASE_URL` to the **Proxima Engine endpoint**, not the upstream PostgreSQL endpoint. Each business request opens a database connection with a signed `proxima_tenant_token` startup option. The Engine must verify the token and map the tenant to the intended PostgreSQL role.

Tenant IDs used in the lab are `tenant_a`, `tenant_b`, and `tenant_c`; the corresponding PostgreSQL roles must be configured in the Engine/database. The Engine's default role prefix is `proxima_tenant_`, so `tenant_a` maps to PostgreSQL role `proxima_tenant_tenant_a`. The RLS policies explicitly bind each `tenant_id` to that role name. If the role prefix changes, update and re-test the database policies deliberately.

Administrative schema setup is separate and uses `LAB_ADMIN_DATABASE_URL` against the upstream test database. The application never falls back to that URL.

## Quick start

1. On Windows PowerShell, run `./scripts/setup.ps1` to generate local-only secrets and start the isolated database. Alternatively, copy `.env.example` to `.env` and replace its values.
2. Start the isolated upstream database: `docker compose up -d enterprise-db` (the setup script does this automatically). The schema, synthetic records, tenant roles, and RLS policies are initialized on the database's first startup.
3. In a separate PowerShell terminal, run `./scripts/start-proxima.ps1 -AgataRepoPath <path-to-your-local-Agata-Proxima-repository>`. This launches the real Engine with runtime-only environment variables; it does not edit any file in Agata Proxima. It uses listener `127.0.0.1:6432`, upstream `127.0.0.1:55432`, the shared `PROXIMA_TENANT_SIGNING_KEY`, and the default `PROXIMA_TENANT_ROLE_PREFIX=proxima_tenant_`. The Engine forwards the selected role's authentication exchange, so the lab backend uses each tenant role's separate password from `LAB_TENANT_DB_PASSWORDS`. The matching database login roles are `proxima_tenant_tenant_a`, `proxima_tenant_tenant_b`, and `proxima_tenant_tenant_c`.
4. Keep `PROXIMA_DATABASE_URL` pointed at the Engine listener (`127.0.0.1:6432`), never port `55432`.
5. Install and run:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --host 127.0.0.1 --port 8088
```

Health check: `GET http://127.0.0.1:8088/healthz`

## Business API

All data endpoints require `Authorization: Bearer <Enterprise-Lab-API-key>`. Tenant identity and role are derived from the server-side `LAB_API_KEYS` mapping, never from a caller-supplied tenant header.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/healthz` | Process liveness only |
| GET | `/metrics` | Prometheus metrics |
| GET | `/api/v1/me` | Show the authenticated synthetic identity |
| GET / POST | `/api/v1/customers` | Customer records |
| GET | `/api/v1/customers/{id}` | Tenant-scoped customer lookup |
| GET / POST | `/api/v1/employees` | Employee directory |
| GET / POST | `/api/v1/projects` | Project portfolio and budgets |
| GET / POST | `/api/v1/invoices` | Invoice records |
| GET / POST | `/api/v1/documents` | Confidential and restricted documents |

Writes require the `admin` or `operator` role. The schema includes synthetic data for three separate tenant companies.

## Run checks

```bash
pytest -q
python scripts/attack_suite.py
python scripts/proxima_protocol_suite.py
python scripts/control_plane_acceptance.py
python scripts/performance_suite.py
```

The protocol suite connects directly to the configured Proxima Engine listener and tests role mapping, three-tenant reads, prepared statements, cross-tenant insert/update/delete, rollback, reconnect context, expired tokens, and tampered signatures. It requires an isolated Engine/database environment and the correct signing key. Do not point it at production.

The Control Plane suite is read-only. Fill `AGATA_CONTROL_PLANE_URL`, `AGATA_LAB_EMAIL`, and `AGATA_LAB_PASSWORD` in the ignored local `.env` using a dedicated test account, then run it. It checks authentication and management endpoints without creating resources. Exit code 3 means the API checks completed but production readiness remains blocked.

The performance suite defaults to 100 requests with 10 workers and permits local targets only. It records p50/p95/p99 latency and a SHA-256 checksum. Remote testing requires an explicit exact-target confirmation and must use a dedicated isolated environment. This bounded sample is not an SLA or a production capacity claim.

The attack suite requires the API to be running and the sample API keys from your local `.env` (or equivalent environment variables) to be configured. It must only be pointed at the isolated Enterprise Lab test deployment.

## Environment variables

- `LAB_API_KEYS`: JSON object mapping API keys to trusted server-side identities, e.g. `{"replace-with-key-a":{"tenant_id":"tenant_a","role":"admin"}}`.
- `PROXIMA_TENANT_SIGNING_KEY`: shared signing secret for the real Engine and lab backend; keep it in a secret store or local environment, never commit it.
- `LAB_TENANT_DB_PASSWORDS`: JSON object mapping tenant IDs to the matching PostgreSQL tenant-role passwords.
- `PROXIMA_DATABASE_URL`: application DSN pointing to Proxima Engine.
- `LAB_ADMIN_DATABASE_URL`: upstream admin DSN for manual schema setup only; never used by request handlers.
- `LAB_POSTGRES_ADMIN_PASSWORD`: password for the disposable local PostgreSQL bootstrap administrator.
- `LAB_TENANT_A_DB_PASSWORD`, `LAB_TENANT_B_DB_PASSWORD`, `LAB_TENANT_C_DB_PASSWORD`: passwords for the three isolated tenant login roles.
- `PROXIMA_TOKEN_TTL_SECONDS`: signed tenant-context lifetime (default 300 seconds).
- `LAB_BASE_URL`, `LAB_API_KEY_A`, `LAB_API_KEY_B`: attack-suite target and test identities.

The example values are for local-only development. Never use them in a deployed environment. No real customer data or production credentials belong in this repository.
