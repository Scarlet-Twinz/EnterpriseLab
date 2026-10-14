# Agata Proxima — Source Inspection Findings

This file records integration risks discovered while aligning Enterprise Lab to the current Agata Proxima `main` branch. These are findings from source/document inspection, not claims that a live production deployment was attacked. **No Agata Proxima files were changed.**

## Finding 1 — Proxima Verify token helper omits its TTL argument

**Severity: High for release verification evidence**  
**Status: Source-level defect confirmed and reproduced at runtime in Enterprise Lab CI**

In [Agata Proxima's Proxima Verify harness](https://github.com/Scarlet-Twinz/Agata-proxima/blob/main/tools/proxima-verify.sh), the shell function defines a local TTL, but invokes Python with only the signing key and tenant ID. The embedded Python reads `sys.argv[3]` to calculate expiry. That argument is not supplied by the invocation shown in the current file, so the harness will raise an index error when `make_token` evaluates `sys.argv[3]`. This is confirmed by source inspection and was reproduced at runtime against the isolated real-Engine CI deployment. The harness failed before performing any verification checks with `IndexError: list index out of range`. The runner records `KNOWN DEFECT REPRODUCED` and preserves the output in the uploaded `product-proxima-verify.log` evidence file. See [Enterprise Lab CI run #154](https://github.com/Scarlet-Twinz/EnterpriseLab/actions/runs/38651742920).

Enterprise Lab has an independent protocol suite in `scripts/proxima_protocol_suite.py`, and it now runs alongside a runtime probe of the product harness. The lab does not repair or replace the product script. This finding should be resolved in a separate Agata Proxima change and re-tested there.

## Finding 2 — TLS behavior documentation is inconsistent

**Severity: High for deployment configuration**  
**Status: Client TLS tested in isolated CI; upstream TLS and production deployment acceptance remain pending**

The current Agata Proxima README describes TLS termination at Proxima and optional upstream CA/hostname verification. The current `docs/verification.md` also contains wording that describes end-to-end TLS through the Engine and says tenant enforcement cannot inspect that encrypted session. These statements describe different deployment paths and need to be reconciled with the actual configured mode.

Enterprise Lab now has a separate isolated runtime test that generates a disposable server certificate, confirms the real Engine rejects plaintext when client TLS is required, and confirms a TLS PostgreSQL client retains the expected tenant role. This validates the tested client-TLS path only. The normal live integration still uses loopback with upstream TLS disabled, so it is not evidence of production TLS acceptance or upstream CA/hostname validation. Source inspection of `crates/proxima-engine/src/tls.rs` also shows that the client-side TLS acceptor uses `with_no_client_auth()`: TLS protects the client connection, but this code does not authenticate client certificates (mTLS). The `PROXIMA_TLS_CERT_FILE` and `PROXIMA_TLS_KEY_FILE` values are the Engine's server certificate and private key, not a client certificate/key. A separate runtime test must verify plaintext rejection when client TLS is required, successful TLS negotiation with the configured server certificate, and upstream CA plus hostname validation when `PROXIMA_UPSTREAM_TLS_MODE=verify-full`. Do not describe this as mTLS.

## Finding 3 — Tenant enforcement depends on Engine signing-key configuration

**Severity: Critical configuration gate**  
**Status: Guarded by Enterprise Lab launcher; production environment still needs verification**

The Engine configuration reads `PROXIMA_TENANT_SIGNING_KEY` as optional. When it is absent, the Engine can start with tenant enforcement disabled. A successful process start or TCP health check therefore does not prove tenant isolation is enabled.

The Enterprise Lab launcher supplies the signing key and the protocol suite requires valid signed tenant context. A production readiness process must independently verify the Engine's effective runtime configuration and reject promotion if tenant enforcement is disabled.

## Finding 4 — Billing-provider documentation is inconsistent

**Severity: High for commercial onboarding**  
**Status: Documentation discrepancy; provider acceptance not run**

The current Agata Proxima README and launch runbooks describe Paddle as the active billing provider, while the current developer guide describes Lemon Squeezy. This is not enough to establish which provider is active in a deployed runtime. The Control Plane's actual configuration and the provider-specific checkout/webhook tests must settle that before customers are invited to pay.

Enterprise Lab will report billing as **not verified** until the configured provider completes checkout, signed webhook, duplicate delivery, failure/recovery, cancellation and entitlement-reconciliation acceptance in the intended environment.

## Finding 5 — Control Plane OpenAPI contract is incomplete for automation

**Severity: Medium for customer integration**  
**Status: Source-inspection finding**

The current `control-plane/openapi.json` lists many management routes but omits request-body and response schemas for several resource-creation operations. The usage guide documents fields, but an integration client cannot rely on a fully described machine-readable contract for all those endpoints.

The read-only harness in `scripts/control_plane_acceptance.py` avoids guessing mutation schemas. Mutating customer-onboarding tests should wait until the relevant request/response contracts are explicit and a dedicated disposable organization is selected.

## Acceptance interpretation

- These findings are not proof that the underlying Engine isolation mechanism is broken.
- A missing test result is **not verified**, not a pass.
- Enterprise Lab remains a separate repository. Fixes to Agata Proxima, if needed, must be proposed and reviewed there independently.

## Finding 6 — Dream PDF pillars are not all evidenced by the current Engine

**Severity: High for claims made to enterprise customers**  
**Status: Source inspection; Monitor is partial, Breaker and Attest are not verified**

The supplied dream PDF defines four distinct capabilities: Engine, Monitor, Breaker, and Attest. I compared those requirements with the current Agata Proxima Engine source without changing the product repository.

- **Engine — partially verified in the isolated lab.** The real Engine is built from the separate product repository and live tests verify signed tenant context, role selection, and PostgreSQL RLS behavior. This does not constitute a production deployment acceptance.
- **Monitor — partial runtime visibility, not the full PDF specification.** The Engine has structured tracing logs and a local dashboard with `/health` and `/api/status`. The inspected Engine dependency manifest does not include a Prometheus exporter, and the dashboard handler does not expose the PDF's described Prometheus `/metrics` counters for active sessions, latency, and blocked attempts. Do not claim those specific metrics have been verified.
- **Breaker — not verified / not evidenced by inspected source.** The Engine has a maximum-connection semaphore and logs connection failures, but the inspected configuration and connection loop do not implement the dream PDF's `PROXIMA_SECURITY_BREAKER=fail-closed` policy, per-endpoint attack tracking, circuit state, or an intrusion event forwarded to the Control Plane. A connection limit alone is not equivalent to that breaker.
- **Attest — not verified / not evidenced by inspected source.** The inspected Engine workspace has no identified hourly signed state-ledger implementation or PDF attestation generation path. The SHA-256 checksum attached to an Enterprise Lab test artifact is a lab evidence checksum, not a Proxima Attest compliance certificate and not proof of SOC 2, HIPAA, or FedRAMP compliance.

These are source-inspection conclusions about what was inspected, not runtime attack findings. The next step is to add explicit acceptance criteria and test only the capabilities that exist; missing product capabilities remain clearly marked not verified. Any product implementation work belongs in a separately reviewed Agata Proxima change, not in Enterprise Lab.
