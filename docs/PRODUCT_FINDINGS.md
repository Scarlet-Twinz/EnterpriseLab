# Agata Proxima — Source Inspection Findings

This file records integration risks discovered while aligning Enterprise Lab to the current Agata Proxima `main` branch. These are findings from source/document inspection, not claims that a live production deployment was attacked. **No Agata Proxima files were changed.**

## Finding 1 — Proxima Verify token helper appears to omit its TTL argument

**Severity: High for release verification evidence**  
**Status: Source-inspection finding; runtime confirmation pending**

In [Agata Proxima's Proxima Verify harness](https://github.com/Scarlet-Twinz/Agata-proxima/blob/main/tools/proxima-verify.sh), the shell function defines a local TTL, but invokes Python with only the signing key and tenant ID. The embedded Python reads `sys.argv[3]` to calculate expiry. That argument is not supplied by the invocation shown in the current file, so the harness appears to raise an index error before it can produce a signed token.

Enterprise Lab has an independent protocol suite in `scripts/proxima_protocol_suite.py`. That suite is not a substitute for fixing and successfully running the product's own verification harness; this finding should be resolved in a separate Agata Proxima change and re-tested there.

## Finding 2 — TLS behavior documentation is inconsistent

**Severity: High for deployment configuration**  
**Status: Requires explicit deployment-mode acceptance**

The current Agata Proxima README describes TLS termination at Proxima and optional upstream CA/hostname verification. The current `docs/verification.md` also contains wording that describes end-to-end TLS through the Engine and says tenant enforcement cannot inspect that encrypted session. These statements describe different deployment paths and need to be reconciled with the actual configured mode.

Enterprise Lab's local launcher intentionally uses loopback and disables upstream TLS for its isolated development run. That is not evidence of production TLS acceptance. A separate test must configure a client certificate/key, require client TLS, and—when enabled—verify upstream CA and hostname validation.

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
