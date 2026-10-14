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
| Proxima connection path | Application connects through Engine endpoint | Real Engine accepts valid signed tenant context and selects the expected prefixed PostgreSQL role |
| Tenant A/B isolation | Tenant B creates a record in each business table; tenant A requests or lists data | A receives no record (normally 404/403 or a list without the synthetic marker) |
| Tenant-local access | Owning tenant reads its own record | Successful response |
| RLS enforcement | Repeat isolation tests against actual PostgreSQL RLS | Protected row remains inaccessible |
| Writes | Cross-tenant INSERT, UPDATE, DELETE through Engine | INSERT is rejected by RLS; UPDATE and DELETE affect zero protected rows |
| Transaction lifecycle | Insert synthetic own-tenant probe then roll back | Probe row is absent after rollback |
| Prepared statements / transactions | Add database-level integration scenarios | Isolation persists across statement and transaction lifecycles |
| Invalid/expired tenant context | Supply invalid and expired signed context in dedicated integration harness | Engine rejects before query execution |
| Engine restart / connection reuse | Re-run tenant checks around restarts and reused sessions | No tenant-context leakage |
| Control Plane outage | Stop management plane while Engine remains running | Local enforcement continues as designed |
| PostgreSQL outage | Stop isolated upstream DB | Requests fail closed and recover without cross-tenant access |
| Client TLS enforcement | Start Engine with server cert/key and `PROXIMA_TLS_REQUIRE_CLIENT=true`; attempt plaintext and TLS PostgreSQL negotiation | Plaintext is rejected; TLS succeeds with the Engine server certificate. This setting currently requires TLS encryption, not client-certificate authentication (mTLS) |
| Upstream TLS verification | Configure PostgreSQL TLS with a test CA; run Engine in `verify-full` mode with valid and invalid CA/hostname combinations | Valid CA + matching hostname succeeds; invalid CA or hostname fails closed |
| Monitoring | Compare generated traffic with available metrics/logs | Observations match actual requests and outcomes |
| Raw protocol rejection | Send a malformed short startup frame, an oversized startup header, and a valid-shaped startup packet without a tenant token | Each connection is closed by the real Engine; bounded probes do not crash the Engine |
| Control Plane integration | Read-only authenticated acceptance suite | Authenticated organization, tenant, policy, node, deployment, verification, audit, support, developer, and billing endpoints respond as expected |
| Performance | Bounded API-through-Engine workload | Success/error counts and p50/p95/p99 latency recorded; no public SLA inferred |
| Attestation | Verify signed evidence and tamper detection if implemented | Verification result matches the actual attestation contract |
| Load | Bounded concurrent read workload, then staged load ramps | Latency/error results captured; no isolation regression |

## Safety and interpretation

- The live attack suite must only run against the isolated Enterprise Lab deployment.
- The initial concurrency scenario is bounded (8 workers, 32 read requests); it is not a denial-of-service test.
- A passing API-level test alone does not prove database isolation. At least one live test must traverse the real Engine and validate the actual protected data.
- Any feature not available in the deployed build is reported as **not verified**, not passed.
- Source-inspection risks and documentation discrepancies are recorded in [PRODUCT_FINDINGS.md](PRODUCT_FINDINGS.md); they are not silently treated as runtime failures or passes.

## Dream PDF pillar acceptance

The supplied dream PDF names four pillars. Their acceptance status must be reported independently; one passing pillar does not imply the others exist or pass.

| Pillar | Customer-style acceptance test | Current evidence/status |
|---|---|---|
| Proxima Engine | Run the synthetic company app through the real Engine; verify signed context, correct PostgreSQL role, tenant A/B/C read/write isolation across customers, invoices, employees, projects, and documents; prepared statements, rollback, expiry/tampering, malformed/oversized startup frames, and missing context. | Verified in CI run #154: 38 protocol checks passed, 0 failed; cross-table RLS blocked reads/writes; malformed/oversized startup and missing-token probes were closed. This is isolated CI evidence, not production deployment acceptance. |
| Proxima Monitor | Generate known traffic and rejected requests; verify active-session, request/latency, and blocked-attempt telemetry, metric labels, scrape behavior, and correlation with logs. | Partial only: local dashboard status/health and structured logs exist in inspected source. The dream PDF's Prometheus /metrics counters are not verified in the Engine. |
| Proxima Breaker | On the isolated lab only, send bounded malformed/oversized PostgreSQL frames and invalid/expired tenant contexts; verify configured fail-closed behavior, endpoint/circuit state, bounded resource use, security event logging, and recovery. | Basic protocol rejection is now runtime-tested (malformed/oversized startup and missing tenant token close the connection). The PDF's full PROXIMA_SECURITY_BREAKER state machine, per-endpoint circuit state, and Control Plane intrusion-event path remain not verified / not evidenced by inspected source. |
| Proxima Attest | Generate an hourly evidence snapshot; independently verify signature/hash-chain continuity, detect altered/deleted/reordered records, and validate any generated PDF against the signed evidence and declared scope. | Not verified: no identified Engine implementation of the dream PDF's hourly signed ledger/PDF certificate path. Enterprise Lab artifact checksums are not Proxima Attest. |

The current live run also reproduced the product `tools/proxima-verify.sh` defect: its embedded Python reads a TTL argument omitted by the shell invocation, so the harness fails before running checks. This is preserved as a known finding and is not silently replaced by the lab's independent suite. The Breaker probes must not be described as a successful product feature until the product actually implements the described contract and a runtime test demonstrates it. The Attest pillar must not be represented as audit certification merely because a JSON report or SHA-256 checksum exists.
