# Integration Contract: Enterprise Lab ↔ Agata Proxima

This document describes the boundary between the fictional customer application in this repository and the actual Agata Proxima Engine in its separate repository.

## This is not a second Proxima implementation

Enterprise Lab is a synthetic B2B SaaS customer: its own API, customer/employee/project/invoice/document records, application API-key authentication, business-role checks, and adversarial acceptance tests. It does not implement the Proxima Engine, the Proxima Control Plane, product billing, product sign-in, or a competing policy-management service.

The small tenant-token signer in `app/security.py` is customer-side integration glue for the Engine's current wire contract. The Engine itself verifies the signed context and chooses the PostgreSQL role. The test lab does not replace that enforcement.

## The two different secrets

| Setting | Owner / purpose | What it is not |
|---|---|---|
| `LAB_API_KEYS` | Enterprise Lab's own caller credentials. Each key is mapped server-side to a fictional company and an application role. | Not an Agata Proxima credential and not the Engine signing key. |
| `PROXIMA_TENANT_SIGNING_KEY` | Shared protocol secret used by the trusted Enterprise Lab backend to sign a tenant-context token and by the Engine to verify it. Must be identical in both runtime environments. | Not a user sign-in password, not a Control Plane login, and not a caller API key. |
| `LAB_TENANT_DB_PASSWORDS` | Disposable database-login passwords for the test tenants. | Not the signing secret or an application API key. |
| `AGATA_LAB_EMAIL` / `AGATA_LAB_PASSWORD` | Optional dedicated account for read-only Control Plane acceptance. | Not used by the business API or Engine token signer. |

The signing key is generated locally by `scripts/setup.ps1`, written to the ignored local `.env`, and passed to the real Engine as a runtime environment variable. Do not commit it or use a local lab key in a real customer deployment. A real customer should use a managed secret store and limit the signing capability to the trusted backend that establishes tenant identity.

## Tenant IDs and PostgreSQL role names

The lab now uses fictional company identifiers, not generic `tenant_a`, `tenant_b`, and `tenant_c` IDs:

| Fictional company | Tenant ID | Default Engine role |
|---|---|---|
| Northstar Operations Ltd | `northstar` | `labrole_northstar` |
| Bluehaven Logistics Ltd | `bluehaven` | `labrole_bluehaven` |
| Cedarstone Health Systems | `cedarstone` | `labrole_cedarstone` |

The Engine currently resolves a PostgreSQL role using one configured global prefix followed by the verified tenant ID:

```text
role_name = PROXIMA_TENANT_ROLE_PREFIX + verified_tenant_id
```

The Engine's default prefix is `proxima_tenant_`. Enterprise Lab deliberately configures `PROXIMA_TENANT_ROLE_PREFIX=labrole_` and its disposable database/RLS fixtures use `labrole_northstar`, `labrole_bluehaven`, and `labrole_cedarstone`. The prefix is configurable through `PROXIMA_TENANT_ROLE_PREFIX`, and the prefix must contain only ASCII letters, digits, and underscores. It cannot currently be empty. The live CI run tests this non-default prefix against the actual Engine.

**A company is not required to use the default prefix.** For example, if a company's database roles are named `tenant_a` and `tenant_b`, it can configure prefix `tenant_` and use verified tenant IDs `a` and `b`; the Engine then resolves those IDs to the matching roles. If the tenant IDs themselves are `tenant_a` and `tenant_b`, the same prefix instead resolves to `tenant_tenant_a` and `tenant_tenant_b`.

There is an important current limitation: the Engine supports a single global prefix-plus-tenant-ID convention, not an arbitrary per-tenant mapping table. The database roles, tenant IDs, prefix, and PostgreSQL RLS policies must all agree. A customer who needs unrelated role names for each tenant would need an explicit product capability for per-tenant role mapping; the lab must not pretend that capability already exists.

## Enforcement and trust boundaries

1. The caller presents an Enterprise Lab API key.
2. The Lab backend resolves that key to a tenant ID and application role from server-side configuration. Request headers and request bodies cannot select a tenant.
3. The trusted backend signs the resolved tenant ID with a short expiry using `PROXIMA_TENANT_SIGNING_KEY`.
4. The backend connects to the **real Proxima Engine listener**, sending the signed context through the supported PostgreSQL startup option.
5. The Engine validates the signature and expiry, resolves the PostgreSQL role using its configured prefix, and establishes the PostgreSQL session.
6. PostgreSQL grants and row-level-security policies enforce the data boundary. The lab's RLS policies are test fixtures and are not shipped as product code.
7. The Control Plane is tested separately for management/configuration/evidence workflows. It is not inserted into each query's authorization path.

## Evidence boundaries

The green GitHub Actions run proves only the checks named in that run against the exact Agata Proxima source revision recorded in the uploaded artifact. The current live Engine suite covers signed context, tenant role selection, cross-tenant reads/writes, prepared statements, rollback, reconnect context, expired/tampered tokens, business API attacks, and a bounded load sample.

It does **not** automatically prove production TLS, live Control Plane acceptance, SSO, billing-provider checkout/webhooks, backup restoration, production monitoring, or a customer's full deployment. Those remain separate acceptance gates until actually exercised and recorded. A missing result is **not verified**, never an assumed pass.
