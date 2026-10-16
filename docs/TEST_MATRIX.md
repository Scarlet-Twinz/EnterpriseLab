# Enterprise Lab — Acceptance Matrix

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
| PostgreSQL outage | Start a second real Engine pointed at an intentionally closed loopback upstream port in the isolated CI environment | Client query is rejected and the Engine log confirms the upstream connection is unavailable; no query result is returned |
| Client TLS enforcement | Start a second real Engine with a disposable server certificate and `PROXIMA_TLS_REQUIRE_CLIENT=true`; attempt plaintext and TLS PostgreSQL negotiation | Verified in isolated CI run #162: plaintext was rejected, TLS succeeded, and `current_user` remained `labrole_northstar`. This setting requires TLS encryption, not client-certificate authentication (mTLS) |
| Upstream TLS verification | Configure a disposable PostgreSQL 16 TLS server with a test CA; run the real Engine in `verify-full` mode with valid and invalid CA/hostname combinations | Verified in isolated CI run #180: valid CA + matching hostname succeeds; untrusted CA fails closed; hostname mismatch fails closed. Client-to-Engine TLS is enabled for this test, with channel binding disabled because the Engine terminates and separately re-establishes TLS; this does not test transparent end-to-end TLS |
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
- Product implementation status and production gates are maintained in the [Agata Proxima documentation](https://github.com/Scarlet-Twinz/Agata-proxima/tree/main/docs); a missing test result is recorded as not verified.

## Four-pillar acceptance

The four Proxima capabilities have separate acceptance criteria. Implementation documentation is not a substitute for running the corresponding test against the intended Engine and Control Plane builds.

| Pillar | Acceptance test | Evidence status |
|---|---|---|
| Proxima Engine | Run the synthetic company app through the real Engine. Verify signed tenant context, expected PostgreSQL role, tenant A/B/C read/write isolation, prepared statements, rollback, expiry/tampering, malformed/oversized frames, missing context, and failure behavior. | Historical isolated CI run #180 reported 38 protocol checks passing, cross-table RLS isolation, malformed/oversized frame rejection, missing-token rejection, upstream-outage fail-closed behavior, and upstream TLS CA/hostname checks. This is isolated CI evidence, not production deployment acceptance. Re-run against the current build before release. |
| Proxima Monitor | Generate known traffic and rejected requests. Verify the separate Prometheus listener, expected active-session/request/latency/blocked-attempt metrics, bounded labels, scrape behavior, and correlation with logs. | The Engine exposes `/metrics` on its configured management listener. This matrix does not claim a fresh live acceptance run; capture and review the current metric output before release. |
| Proxima Breaker | On the isolated lab only, send bounded malformed/oversized frames and invalid/expired tenant contexts. Verify per-source-IP threshold/window/cooldown behavior, connection rejection, recovery, and authenticated Control Plane security-event ingestion when configured. | Implementation and configuration are documented in the [four-pillar runbook](https://github.com/Scarlet-Twinz/Agata-proxima/blob/main/docs/production/proxima-four-pillars.md). Full Enterprise Lab acceptance of threshold, cooldown, and event-ingestion behavior remains not verified by this matrix until a current run records evidence. |
| Proxima Attest | Configure the signing key and measured p99 overhead; verify a signed hourly ledger record, SHA-256 chain continuity, HMAC signature, tamper detection, Control Plane ingestion, organization scoping, and the PDF report. | The Engine and Control Plane contain the attestation workflow. The lab must not claim this pillar passed until a current record is ingested and its signature/chain and PDF are independently checked. An artifact checksum alone is not Proxima Attest evidence or a compliance certification. |

The prior CI run #180 remains historical evidence for the scenarios listed above. The Proxima Verify TTL-argument defect recorded during that run has since been corrected in the current product script; rerun the harness and confirm its output before treating the verification suite as passed. A feature is not accepted solely because its implementation or runbook exists.
