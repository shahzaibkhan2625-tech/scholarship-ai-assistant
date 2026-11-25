# API Contract Delta: 003 Quota Governance

(updated 2026-11-26, post-audit/post-010): one new read-only endpoint (section 5, operator-only; `GET /monitoring/budget`, approved 2026-11-26) and the 503 mapping widened (section 1). Three existing response shapes change, and `/metrics` gains series. The OpenAPI contract file and the drift tests must be updated together with the code (updated 2026-11-26, post-audit/post-010): the live drift contract today is `specs/002-source-monitoring-hardening/contracts/openapi.yaml` (`CONTRACT_PATH` in `tests/api/test_openapi_contract_002.py`); 003 copies it to its own folder (T276) and does not edit the 001 or 002 files.

## 1. Temporary-unavailable response on LLM-backed routes (FR-BUDGET-5)

Applies to every route that can reach the LLM for a student: `POST /profile/import-cv`, `POST /scholarships/match-url`, `POST /scholarships/{id}/match`, `POST /scholarships/{id}/qa`, `POST /discovery`, `POST /applications/{id}/documents`, `POST /applications/{id}/generate/cv`, `POST /applications/{id}/generate/sop`.

When the daily allowance is spent, the provider has reported quota exhaustion, **or any other provider failure occurs (outage, unparseable response, provider not configured; FR-ERR-6)** (updated 2026-11-26, post-audit/post-010), the route returns:

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

- `reason` is one of `daily_allowance_spent`, `provider_quota`, `provider_unavailable`, `provider_response_invalid`, `not_configured` (updated 2026-11-26, post-audit/post-010). For the last three there is no provider time, so `retry_at` is now plus `llm_unavailable_retry_seconds` (default 60) and `Retry-After` matches.
- Only the `LlmError` family is mapped; any other exception is not reported as an AI outage.
- No provider call is made when the refusal comes from the local budget or an active pause.
- The body never contains secrets, provider payloads, page content or stack traces.
- This replaces today's behaviour on the routes that let the provider error escape as a bare 500 (audit B-57; documented open limitation in `docs/operations-hardening.md` section 11). Document upload keeps its existing `201` with an `extraction_unavailable` flag (the upload fix) and additionally maps the new `LlmError` base; it does not return 503.

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

## 5. Budget and cadence view (FR-OBS-5, approved new endpoint) (updated 2026-11-26, post-audit/post-010)

`GET /monitoring/budget` (approved by the manager 2026-11-26 as a new operator-only endpoint; the earlier statement "no new endpoints" no longer holds). Dependency `require_operator` (010): 401 without a valid token, 403 for a signed-in non-operator (`{"detail": "Operator access required"}`), 200 for an operator. Read-only; no request body; no write path.

```
{
  "budget_day": "2026-11-26",
  "reset_at": "2026-11-27T08:00:00Z",
  "classify_pending": 0,
  "request_types": {
    "generate": {"allowance": 20, "reserve": 8, "used": {"user_facing": 0, "backfill": 0, "monitoring": 0, "validation": 0},
                 "remaining": 20, "deferred": 0, "denied": 0, "provider_exhausted": 0, "discrepancy": 0,
                 "avoided_unchanged": 0, "avoided_rules": 0, "paused_until": null, "observed_limit": null},
    "embed": {"allowance": null, "used": {}, "remaining": null}
  },
  "sources": [
    {"domain": "www.hec.gov.pk", "interval_minutes": 1440, "interval_source": "derived|override|default",
     "last_checked_at": null, "next_due_at": null, "noisy": false, "rules_review_needed": false}
  ]
}
```

Numbers come from the same read helpers as `/metrics`. The body never contains secrets, provider payloads or page content.

## 6. Not changed

Auth, profile, applications, alerts, coverage payload shape (only the failed-source counting rule changes), candidate-source endpoints, and every request body. (updated 2026-11-26, post-audit/post-010): access levels set by 010 are unchanged: `GET /sources/health` and `GET /monitoring/runs` stay open to any signed-in user (they gain the new fields), `GET /sources/{id}/fetch-log` and `GET /monitoring/runs/{id}` stay operator-only, and `/metrics` keeps its bearer token and is not exposed through Caddy.
