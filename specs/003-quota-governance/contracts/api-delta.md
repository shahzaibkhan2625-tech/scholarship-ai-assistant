# API Contract Delta: 003 Quota Governance

No new endpoints. Three existing response shapes change, and `/metrics` gains series. The OpenAPI contract file (`specs/001-scholarship-mvp/contracts/openapi.yaml`) and the drift tests (`tests/api/test_openapi_contract_002.py` and the 001 drift check) must be updated together with the code.

## 1. Temporary-unavailable response on LLM-backed routes (FR-BUDGET-5)

Applies to every route that can reach the LLM for a student: `POST /profile/import-cv`, `POST /scholarships/match-url`, `POST /scholarships/{id}/match`, `POST /scholarships/{id}/qa`, `POST /discovery`, `POST /applications/{id}/documents`, `POST /applications/{id}/generate/cv`, `POST /applications/{id}/generate/sop`.

When the daily allowance is spent, or the provider has reported quota exhaustion, the route returns:

```
HTTP/1.1 503 Service Unavailable
Retry-After: <seconds until the earliest retry time>
Content-Type: application/json

{
  "detail": "AI features are temporarily unavailable. Try again after the retry time.",
  "code": "llm_temporarily_unavailable",
  "retry_after_seconds": 5400,
  "retry_at": "2026-10-26T07:00:00Z",
  "reason": "daily_allowance_spent"
}
```

- `reason` is one of `daily_allowance_spent`, `provider_quota`, `provider_unavailable`.
- No provider call is made when the refusal comes from the local budget or an active pause.
- The body never contains secrets, provider payloads, page content or stack traces.
- This replaces today's behaviour on the routes that let the provider error escape as a bare 500 (documented open limitation in `docs/operations-hardening.md` section 11). Document upload keeps its existing `201` with an `extraction_unavailable` flag (the upload fix) and additionally maps the new `LlmError` base; it does not return 503.

Add `503` to the documented responses of the routes above.

## 2. Source health response gains provider-failure visibility (FR-ERR-5)

`GET /sources/health` and `GET /sources/{id}/fetch-log` (`api/health.py:22,43`):

- Each fetch-log entry gains `outcome` (string or null; values in `research.md` R-10).
- Each health entry gains `recent_provider_failures` (integer, last N checks, default 10) and `last_provider_failure_at` (timestamp or null), reported separately from `consecutive_failures`, which no longer counts provider-side failures.

## 3. Monitoring run response gains cost counters (FR-OBS-3)

`GET /monitoring/runs`, `GET /monitoring/runs/{id}`, and the `POST /monitoring/runs` body (`api/monitoring.py:35,44,64`): add `sources_skipped` and `sources_deferred` (integers, default 0).

## 4. `/metrics` series (FR-OBS-1)

Same authentication as today. New series (names provisional, finalised in tasks):

| Series | Labels | Meaning |
|---|---|---|
| `llm_budget_allowance` | `request_type` | Configured daily allowance |
| `llm_budget_used` | `request_type`, `priority_class` | `allowed` count today |
| `llm_budget_remaining` | `request_type` | Allowance minus used today |
| `llm_budget_reserve` | `request_type` | User-facing reserve |
| `llm_budget_events` | `request_type`, `priority_class`, `event` | Today's deferred, denied, provider-exhausted, discrepancy, avoided counts |
| `llm_budget_seconds_to_reset` | none | Seconds to the next budget-day boundary |
| `llm_provider_paused_until_timestamp` | `request_type` | Unix time of the provider pause, 0 if none |
| `source_check_noisy` | `domain` | 1 if the source's fingerprint changes at almost every check |

Values are read from the database at scrape time, so they survive restarts and agree across workers.

## 5. Not changed

Auth, profile, applications, alerts, coverage payload shape (only the failed-source counting rule changes), candidate-source endpoints, and every request body.
