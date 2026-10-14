# Enterprise Lab — Initial Acceptance Matrix

This matrix distinguishes tests that can run without a live database from tests that require a real Agata Proxima deployment.

## Local automated checks

| Area | Test | Expected evidence |
|---|---|---|
| API authentication | Missing and unknown API keys | HTTP 401 |
| Identity binding | Caller sends a forged tenant header | Server-side API-key tenant remains authoritative |
| Role authorization | Viewer attempts to create a customer | HTTP 403 |
| Token integrity | Verify HMAC format and signature | Exact payload and matching HMAC |
| Token input validation | Malformed tenant identifier | Rejected before a database connection |
| Request validation | Client supplies tenant_id in business body | HTTP 422 |
| Logging | Business request finishes | Request ID, method, path, status and duration; no secrets |
| Monitoring | Scrape `/metrics` | Prometheus process, request-count, and duration metrics are exposed without tenant labels |

## Live tests (must target an isolated deployment)

| Area | Test | Pass condition |
|---|---|---|
| Proxima connection path | Application connects through Engine endpoint | Real Engine accepts valid signed tenant context |
| Tenant A/B isolation | Tenant B creates a record; tenant A requests its ID | A receives no record (normally 404/403) |
| Tenant-local access | Owning tenant reads its own record | Successful response |
| RLS enforcement | Repeat isolation tests against actual PostgreSQL RLS | Protected row remains inaccessible |
| Writes | Insert/update/delete through Engine | Only authorized tenant rows change |
| Prepared statements / transactions | Add database-level integration scenarios | Isolation persists across statement and transaction lifecycles |
| Invalid/expired tenant context | Supply invalid and expired signed context in dedicated integration harness | Engine rejects before query execution |
| Engine restart / connection reuse | Re-run tenant checks around restarts and reused sessions | No tenant-context leakage |
| Control Plane outage | Stop management plane while Engine remains running | Local enforcement continues as designed |
| PostgreSQL outage | Stop isolated upstream DB | Requests fail closed and recover without cross-tenant access |
| Monitoring | Compare generated traffic with available metrics/logs | Observations match actual requests and outcomes |
| Attestation | Verify signed evidence and tamper detection if implemented | Verification result matches the actual attestation contract |
| Load | Bounded concurrent read workload, then staged load ramps | Latency/error results captured; no isolation regression |

## Safety and interpretation

- The live attack suite must only run against the isolated Enterprise Lab deployment.
- The initial concurrency scenario is bounded (8 workers, 32 read requests); it is not a denial-of-service test.
- A passing API-level test alone does not prove database isolation. At least one live test must traverse the real Engine and validate the actual protected data.
- Any feature not available in the deployed build is reported as **not verified**, not passed.
