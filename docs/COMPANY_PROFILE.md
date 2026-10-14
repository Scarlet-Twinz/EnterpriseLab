# Enterprise Lab — Simulated Customer Profile

## The fictional customer

**Enterprise Lab Systems, Inc.** is a fictional US-based B2B SaaS company headquartered in Austin, Texas. It provides a cloud operations platform to business customers. Its application stores customer contacts, employee directories, project budgets, invoices, and internal/confidential/restricted documents.

This company and every record in the lab are synthetic. The profile is a test scenario, not a claim about a real company.

## Why this customer is evaluating Agata Proxima

The application is multi-tenant: each customer company must see its own records and must not see or change another customer's data. The company wants a database-layer enforcement boundary rather than relying exclusively on application developers remembering to add the correct tenant filter to every query.

Agata Proxima is evaluated as infrastructure underneath the existing application:

```text
Enterprise Lab API
       |
       | PostgreSQL protocol + signed tenant context
       v
Real Proxima Engine
       |
       v
Isolated PostgreSQL database with tenant roles and RLS
```

The Control Plane is used separately for configuration, tenant/node management, verification evidence, audit history, and operational visibility. It is not in the per-query authorization path.

## Synthetic tenant organizations

| Tenant ID | Fictional customer | Business scenario | Protected records |
|---|---|---|---|
| `northstar` | Northstar Operations Ltd | Finance and business operations | Customer contacts, invoices, project budgets, finance documents |
| `bluehaven` | Bluehaven Logistics Ltd | Warehousing and logistics | Customers, operations staff, warehouse projects, restricted procedures |
| `cedarstone` | Cedarstone Health Systems | Compliance and records governance | Staff, governance projects, restricted policy documents |

All amounts are stored as integer cents and sample invoices use USD. The tenant IDs are synthetic identifiers, not identities that callers may choose freely: the Enterprise Lab API maps API keys to tenants server-side.

## Customer acceptance goals

The lab is accepted only when evidence demonstrates all of the following:

1. The normal business API connects to the real Proxima Engine, not directly to PostgreSQL.
2. A tenant can read and change its own allowed records.
3. Cross-tenant reads, inserts, updates, and deletes are blocked by PostgreSQL RLS through the Engine.
4. A caller cannot select another tenant by forging an HTTP header or adding `tenant_id` to a request body.
5. Missing, expired, or tampered signed tenant context is rejected by the Engine.
6. Prepared statements, transactions, and new connections do not leak tenant context.
7. Database and Engine failures fail closed; the application does not fall back to a direct database path.
8. Monitoring and test reports record what actually happened without logging credentials or protected document bodies.
9. The customer can review the test procedure, build/version information, observed results, known limitations, and any unresolved failures.

A green unit-test or PostgreSQL-only CI job is useful evidence, but it is not by itself proof that the real Engine integration has passed. That requires a live run against the actual Engine binary and the isolated lab database.
