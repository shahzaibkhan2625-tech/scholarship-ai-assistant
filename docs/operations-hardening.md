# Operations & Hardening Runbook

For whoever operates the Oracle VM (T237, feature 002, NFR-HARDEN-1..8, SC-007..010).
Design reasoning lives in [ADR-0011](../adr/0011-us8-production-hardening.md); VM-specific
gotchas (wrong branch, root vs user podman, container reuse) live in
[vm-deployment-notes.md](vm-deployment-notes.md). Read both before touching the VM.

> **Not demonstrated by the build.** Everything marked **[OWNER]** is an action only the VM
> owner can perform or verify (firewall, Neon console, public-internet checks). The build
> ships the config and the instructions, not the proof.

## 1. Topology

```
Internet ──80/443──▶ Caddy (host network) ──127.0.0.1:8000──▶ uvicorn/FastAPI (host network)
                       tls internal                              in-process APScheduler
                       /metrics → 404                            Neon pooled Postgres (outbound)
                                                                 STORAGE_ROOT=/data/storage (named volume)
```

- One backend container, one uvicorn worker, one in-process scheduler (monitoring every
  `MONITORING_INTERVAL_MINUTES`, retention daily at 03:00 UTC).
- Both containers use `network_mode: host` (podman does not reliably forward ports on this
  VM). Therefore the **backend must bind loopback** (`--host 127.0.0.1`), otherwise plain HTTP
  on :8000 stays public next to Caddy. The compose `command:` enforces it and
  `backend/tests/infra/test_deploy_config.py` asserts it.
- Config files: `Caddyfile`, `docker-compose.podman.yml`, `.env` (never committed).
- Compose must be run with **rootful** podman (`sudo`), both because Caddy binds 80/443 and
  because the existing store is root's (vm-deployment-notes §2).

## 2. Deploy and redeploy

Follow the "Verified deployment procedure" in [vm-deployment-notes.md](vm-deployment-notes.md),
with these B4 differences:

1. **Branch:** the VM tracks `main` (feature 002 was merged on 2026-10-19, `7b3f4cb`). Confirm with
   `git branch --show-current` and `git log -1 --oneline` before building. The example branch name
   in the older notes (`001-scholarship-mvp`) is historical.
2. **First cutover to Caddy only:** after this change, `http://<ip>:8000` stops working from
   outside (by design). Anything that called the API on :8000 directly must move to
   `https://<ip>/`.
3. **Validate the Caddyfile before starting** (needs no running stack):
   ```
   sudo podman run --rm -v ./Caddyfile:/etc/caddy/Caddyfile:ro,z \
     -e SITE_ADDRESS=130.61.159.24 docker.io/library/caddy:2-alpine \
     caddy validate --config /etc/caddy/Caddyfile
   ```
4. **Start:** `sudo /usr/local/bin/podman-compose -f docker-compose.podman.yml up -d --build`,
   removing the old container first as the notes describe.
5. **Migrations** (`sudo podman exec scholarship-ai-assistant_backend_1 uv run alembic current`, then `upgrade head` if behind).
   The expected head is `f2b8d4a6c1e3` (composite index `ix_source_fetch_log_source_started`, added in
   US8 B2). It was generated without being applied by the build; as of the B4 verification it
   was found already applied to the development Neon database, so on the VM just confirm
   `alembic current` reports it. If you ever need to apply it, use Neon's **direct (non-pooled)**
   endpoint, passing a direct `DATABASE_URL` for that one command
   (`podman exec -e DATABASE_URL=… scholarship-ai-assistant_backend_1 uv run alembic upgrade head`). The app is correct
   without the index, only slower on large fetch logs.
6. **Prove the new code is serving**, from the VM:
   ```
   curl -s http://127.0.0.1:8000/openapi.json | grep -o '"/[a-z/{}_-]*"' | sort -u
   ss -tlnp | grep -E ':(80|443|8000)\b'   # 8000 must show 127.0.0.1, never 0.0.0.0 or *
   ```

**Container names.** On the Oracle VM podman-compose names the containers
`scholarship-ai-assistant_backend_1` and `scholarship-ai-assistant_caddy_1` (project directory + service + index), not `backend` / `caddy`.
All commands in this runbook use those names. Confirm with
`sudo podman ps -a --format '{{.Names}} {{.ID}}'` if the repo directory is ever renamed.
Run compose by full path: `sudo /usr/local/bin/podman-compose …` (plain `sudo podman-compose` fails: sudo's PATH
does not include `/usr/local/bin`).

### Environment

`.env` on the VM carries the real values (see `.env.example` for names). Relevant to this
runbook: `SITE_ADDRESS` (defaults to `130.61.159.24` in compose), `RATE_LIMIT_PER_MINUTE`,
`FETCH_LOG_RETENTION_DAYS`, `HEALTH_WINDOW_N`, `MONITORING_INTERVAL_MINUTES`, SMTP_*.
`STORAGE_ROOT` is **forced** to `/data/storage` by compose and overrides `.env`.

## 3. VM firewall **[OWNER]**

Two layers must both allow 80/443 and neither may allow 8000.

**Oracle Cloud security list / NSG** (console → Networking → VCN → Security Lists): ingress
rules for TCP **80** and **443** from `0.0.0.0/0`; **no** rule for 8000. Port 22 only from
your own address range if you can.

> **This step cannot be done from the VM and is easy to get wrong.** With firewalld open and Caddy
> listening, 80/443 still timed out from the internet on the first deploy: a packet capture on the VM
> (`sudo tcpdump -ni enp0s6 'tcp and (port 80 or port 443)'` while connecting from outside) showed
> **zero packets arriving**, which means the drop is in Oracle's network layer, not the VM. Check in the
> console: (1) the rule is in the security list attached to the **VM's subnet** (VM private address
> `10.0.0.206`, subnet `10.0.0.0/24`), (2) **Stateful**, protocol **TCP**, source `0.0.0.0/0`, (3) the
> ports are in **Destination Port Range** (`80` and `443`, one rule each or `80,443`), not Source Port
> Range, (4) no network security group on the VM's VNIC overrides it. After the rules were corrected
> everything worked immediately; no VM change was needed.

**OS firewall (Oracle Linux, firewalld):**
```
sudo firewall-cmd --permanent --add-service=http --add-service=https
sudo firewall-cmd --reload
sudo firewall-cmd --list-all            # services: ssh http https ; ports: (no 8000)
```
If 8000 was ever opened by hand: `sudo firewall-cmd --permanent --remove-port=8000/tcp && sudo firewall-cmd --reload`.

**SELinux** stays enforcing. The Caddyfile mount carries `:z`; named volumes need no label.
If something is denied: `getenforce`, then `sudo ausearch -m avc -ts recent`. Do not
`setenforce 0` as a fix.

**Verify from a machine outside the VM** (not from the VM itself):
```
curl -kI https://130.61.159.24/health        # 200
curl -I  http://130.61.159.24/health         # 308 → https://…
curl -m 5 http://130.61.159.24:8000/health   # must time out / refuse
```

## 4. HTTPS and the `tls internal` trade-off

There is no domain yet, so Caddy issues the certificate from its own internal CA
(`tls internal`, [ADR-0011 Q2](../adr/0011-us8-production-hardening.md)).

- **Consequence:** browsers and `curl` show a certificate warning. Fine for a personal or
  demo deployment; **not production-grade** for real users.
- **Workaround for you:** use `curl -k`, or trust Caddy's root CA on your own machine:
  ```
  sudo podman cp scholarship-ai-assistant_caddy_1:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt
  ```
  and import that file into the OS/browser trust store. The root lives in the `caddy_data`
  volume, so it survives redeploys; deleting the volume mints a new CA and breaks that trust.
- **Moving to a real domain (the proper fix):** point an A record at the VM, set
  `SITE_ADDRESS=your.domain` in `.env`, and **remove the `tls internal` line** from the
  `Caddyfile` (and update `test_deploy_config.py`, which currently requires it). Caddy then
  obtains and renews a public certificate automatically; ports 80/443 must be reachable.
  `default_sni` in the global block then becomes unnecessary but harmless.
- **Certificate renewal needs no action.** Caddy's internal CA issues short-lived leaf certificates
  (about 12 hours). Caddy renews them automatically well before expiry and swaps them in without a
  restart (observed in the Caddy log on the first deploy: "certificate expires soon; queuing for renewal" →
  "certificate renewed successfully" → "replaced certificate in cache"). A certificate that expires "today"
  in `openssl s_client` output is normal. Alarm only if the Caddy log shows repeated renewal errors, or if the
  `caddy_data` volume was deleted (new CA, clients must re-trust the root).
- HTTPS reachability from the public internet was verified on 2026-10-19 (see §13).

## 5. Rate limiting

- Per-client-IP token bucket, in memory, capacity `RATE_LIMIT_PER_MINUTE` (default 120);
  excess requests get `429` with `Retry-After`. Only `/health` is exempt. One global limit:
  `/auth/login` has no stricter bucket (ADR-0011 Q6).
- **It depends on `--proxy-headers --forwarded-allow-ips=127.0.0.1`** in the compose
  `command:`. Without them every caller appears as 127.0.0.1 and everyone shares one bucket
  (symptom: 429s for all users at once). Caddy overwrites client-supplied `X-Forwarded-For`,
  so the header cannot be spoofed past it.
- **Per-process state:** limits reset on restart and multiply by the worker count. We run one
  worker; do not add `--workers N` without re-reading this. Users behind one NAT share a
  bucket.
- **Tuning:** change `RATE_LIMIT_PER_MINUTE` in `.env`, then recreate the container (env is
  read at start). Raise it if legitimate clients see 429s; lower it under abuse.

## 6. Metrics, logs and request IDs

- `/metrics` (Prometheus text) is protected twice: Caddy answers **404** publicly, and the app
  requires a bearer token. **Access is therefore VM-internal only** (ADR-0011 Q5 amended —
  a net security improvement over "any account holder via the public URL"):
  ```
  TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login -d '…' | jq -r .access_token)
  curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/metrics
  ```
  Tokens last 60 minutes. No Prometheus is deployed; this is manual. Registration is open, so
  any account could read it *from the VM*, which only you can reach.
- Logs are single-line JSON on stderr: `sudo podman logs scholarship-ai-assistant_backend_1`. Fields: `timestamp`,
  `level`, `logger`, `message`, plus `request_id`, `run_id`, `source_id` when bound. Every
  response carries `X-Request-ID`; quote it when investigating. Access lines never contain
  headers, bodies or query strings. Caddy's own log is separate (`sudo podman logs scholarship-ai-assistant_caddy_1`).

## 7. Fetch-log retention

**Policy.** The daily 03:00 UTC job deletes `source_fetch_log` rows older than
`FETCH_LOG_RETENTION_DAYS` (default 90), **except** three floors that keep the history behind
a source's current state:

| Floor | Keeps | Why |
|---|---|---|
| (a) | every row of each source's last `HEALTH_WINDOW_N` monitoring **runs** | one check writes 2–3 rows, so a row-count floor could erase the evidence behind a `failing` flag (ADR-0008) |
| (b) | each source's single latest row of any kind | a source flagged `failing` by discovery may have only one old row; deleting it would read as "never checked" |
| (c) | each source's latest `OK` row with `items_found` | it is the extraction-cache entry and the shrink-guard baseline; pruning it silently loses both |

`monitoring_runs`, `alerts`, `alert_preferences` and `candidate_source_validations` are never
pruned. Retention is coupled to monitoring: `MONITORING_INTERVAL_MINUTES <= 0` means no
scheduler and no retention.

**First production run (large one-time delete).** Always count first:
```
sudo podman exec scholarship-ai-assistant_backend_1 uv run python -m app.services.retention --dry-run
sudo podman exec scholarship-ai-assistant_backend_1 uv run python -m app.services.retention            # then for real
# scope to one source while testing:  --source-id <uuid>
```
Deletion is batched (1000 rows, one short transaction per batch, no locks), so it is safe on
the transaction-mode pooler and can be interrupted and re-run.

## 8. Neon backups and restore drill **[OWNER]**

Backup recovery is demonstrated by you, not by the build (SC-009).

1. **Check PITR is on:** Neon console → your project → *Settings* → history/restore window.
   Note the retention window (it depends on the plan) and record it in the verification log.
2. **Restore drill (never against production):**
   1. Console → *Branches* → *Create branch* → choose **a past point in time** inside the
      window (or "Restore" preview, if offered).
   2. Copy that branch's connection string into a scratch shell only
      (`DATABASE_URL=… uv run python -c "…SELECT count(*) FROM scholarships…"`), never into
      the VM's `.env`.
   3. Compare row counts (`scholarships`, `source_fetch_log`, `alerts`) with production at
      that time; confirm they plausibly match.
   4. Delete the scratch branch. Record date, timestamp restored to, and result.
3. Repeat after any schema migration and at least quarterly.

## 9. Secrets

- Secrets live only in `.env` on the VM (gitignored; `.env.example` holds placeholders).
  Compose reads them through `env_file`; the compose file and Caddyfile contain none.
- Guards: `backend/tests/test_no_secrets_in_repo.py` scans the repo for key/DSN/`password=`
  shapes and runs in CI; `pip-audit --strict` runs in CI (`.github/workflows/ci.yml`) with
  two documented ignores (python-jose CVE-2026-85394, ecdsa PYSEC-2026-1325). Both have no
  upstream fix; re-check them whenever python-jose or ecdsa release, and drop the ignore then.
- **Rotating `JWT_SECRET_KEY` invalidates every session** (users must log in again). Rotate
  on suspected leak. Rotate SMTP/Qdrant/Gemini/Neon credentials at the provider, edit `.env`,
  recreate the backend container.
- `.env` file mode on the VM should be `600`, owned by the deploying user.

## 10. Persistent-volume redeploy check

Proves uploaded documents survive a redeploy (NFR-HARDEN-6, quickstart US8 step 5). Procedure as
actually run on 2026-10-22 (the API has **no document download or delete endpoint**, so retrieval is
verified at the storage layer plus the database checksum):

1. Sign up / log in a throwaway user, `POST /applications`, then `POST /applications/{id}/documents`
   (multipart: `type=unclassified`, `file=@x.pdf`). Record the SHA-256 of the local file.
2. From the DB (`application_documents`: `file_ref`, `checksum`) and inside the container
   (`sudo podman exec scholarship-ai-assistant_backend_1 sha256sum /data/storage/documents/*/*.pdf`) confirm all three hashes agree.
3. **Recreate both containers, in this order** (Caddy was created with a dependency on the backend, so
   `podman rm` of the backend fails with "has dependent containers" while Caddy exists):
   ```
   sudo podman stop scholarship-ai-assistant_caddy_1 && sudo podman rm scholarship-ai-assistant_caddy_1
   sudo podman stop scholarship-ai-assistant_backend_1 && sudo podman rm scholarship-ai-assistant_backend_1
   sudo /usr/local/bin/podman-compose -f docker-compose.podman.yml up -d      # no --build
   ```
   Confirm the backend container ID changed. **Not** `podman-compose down -v` (deletes the volumes).
4. Re-check: same SHA-256 and size inside the new container, same `application_documents` row, and the
   app's own storage code reads it (`get_storage().exists(ref)` / `.get(ref)` via `podman exec ... uv run python -c`).
5. `sudo podman volume ls` shows `...backend_storage`, `...caddy_data`, `...caddy_config`.
6. Clean up: delete the test rows (`application_documents`, `applications`, `profiles`, `users`) and the
   file (`get_storage().delete(ref)`), scoped to the test user id only.

Never run `podman volume rm` / `down -v` on these without a backup: `backend_storage` holds
the only copy of uploaded documents and `caddy_data` holds the CA the clients trust. Document
storage has no backup in this setup; copy the volume (`podman volume export`) before risky
changes.

## 11. Known limitations and accepted risks

| Item | Status | Source |
|---|---|---|
| **SSRF guard is not airtight.** Candidate validation follows redirects inside `fetch_url`, so a redirect to a non-public address is *requested* before the final URL is checked; its content is discarded, not mined. DNS rebinding between guard and fetch is also possible. Fix needs a redirect-aware fetch in the shared `web_fetch` tool. | Accepted, out of scope | ADR-0010, ADR-0011 Q4 |
| `tls internal` certificate is not publicly trusted | Accepted until a domain exists | ADR-0011 Q2, §4 |
| Rate-limit state is per process and lost on restart | Accepted (one worker) | ADR-0011, §5 |
| `/metrics` has no role gate (any account can read it, from the VM only) | Accepted | ADR-0011 Q5 |
| API-access sources are never re-checked by monitoring, so a `failing` API source does not auto-recover | Deferred | ADR-0008 |
| `api_connector.py` calls `mark_source_failing` unconditionally; monitoring cannot reach it, discovery-side caller not traced | Low priority, deferred | ADR-0008 |
| `expired` is not time-driven by monitoring | Deferred | ADR-0009 |
| Alert idempotency is an application check, not a DB unique constraint; not safe if alert generation ever runs concurrently | Accepted | ADR-0007 D6 |
| Alert email is not retried and carries no link | Out of scope | ADR-0007 |
| The monitoring scheduler runs in every uvicorn worker (guarded by advisory locks and `has_running_run`); do not add workers casually | Accepted | scheduler.py |
| Candidate validation selection order falls back to id-order (no `created_at` on the table) | Deferred | tasks.md US7 note |
| `app/core/metrics.py` imports `get_current_user` from `app.api` (layering inversion) | Cleanup candidate | tasks.md US7 note |
| `created_at.desc()` same-transaction ties | **Closed** in US8 B2 (id tiebreakers at 7 sites) | commit 5603428 |
| Document storage has no off-VM backup | Open | §10 |
| **Document upload returns a bare 500 when the LLM provider errors** (e.g. Gemini 429). The file and the `application_documents` row are already saved, but the row has no flag explaining the failure and the HTTP 500 is the only trace. `_extract_fields_node` catches only `DocumentFieldExtractionError` (bad JSON), not provider/transport errors. Found 2026-10-22. | **Open, fix proposed** | `app/workflows/doc_pipeline/graph.py`, `app/tools/extract_document_fields.py` |
| **Gemini free-tier quota is tiny** (20 requests/day for `gemini-2.5-flash` observed). Once used up, every LLM-backed endpoint (document parsing, Q&A, URL matching, generation) fails until the quota resets (about a 24h window); the LLM smoke tests also cannot pass. | Operational constraint; consider a paid key | observed 2026-10-22 |
| **Qdrant Cloud endpoint resets TLS connections** from both this laptop and the VM (2026-10-22): `test_qdrant_reachable` and `test_qdrant_collection_query_works` fail with a connection reset. Likely a suspended or removed free-tier cluster (the T130 failure mode) but not confirmed; needs the Qdrant console. | **Open, owner action** | `tests/smoke/test_live_services.py` |
| **Smoke-test coverage unverified.** Could not confirm the live listing smoke test covers all three re-enabled sources (DAAD, Stipendium Hungaricum, KAUST); it appears to target a single source. Verify and expand in a future slice. | Open | `backend/tests/smoke/test_discovery_listing_extraction_live.py`, verification log |
| Caddy `2-alpine` is a major-version pin and moves with minor releases | Review periodically (at least quarterly) and after each Caddy security notice | compose |

## 12. Troubleshooting

| Symptom | Likely cause | Check / fix |
|---|---|---|
| 429 for everyone at once | `--proxy-headers` / `--forwarded-allow-ips` missing, all callers look like 127.0.0.1 | `sudo podman inspect backend` → command; recreate container |
| `http://<ip>:8000` works from outside | backend bound `0.0.0.0` or firewall open | `ss -tlnp \| grep 8000` must show 127.0.0.1; §3 |
| `podman rm scholarship-ai-assistant_backend_1` says "has dependent containers" | Caddy was created depending on the backend | Stop and remove `scholarship-ai-assistant_caddy_1` first, then the backend (section 10 step 3) |
| SSH to the VM times out intermittently (port 22 only; 80/443 stay up) while a series of connections is being made | Observed three times for 2-5 minutes on 2026-10-22, each time self-recovering; cause unknown (network path or an upstream rate limit; not the VM's firewalld) | Wait a few minutes; batch commands into one SSH session; if it persists check the Oracle security list rule for 22 and the instance console |
| Caddy exits immediately | cannot bind 80/443 (rootless podman, or port taken) or Caddyfile invalid | `sudo podman logs scholarship-ai-assistant_caddy_1`; §2 step 3 |
| Browser certificate warning | expected with `tls internal` | §4 |
| Browser warning *reappeared* after a redeploy | `caddy_data` volume was deleted, new CA | re-trust the root CA |
| `/metrics` is 404 from outside | expected | read it from the VM, §6 |
| Uploaded files vanish after redeploy | volume not mounted / `down -v` | `podman volume ls`; §10 |
| Old behaviour after redeploy | container reuse by name | vm-deployment-notes §3 |
| Permission denied reading the Caddyfile | SELinux label | keep `:z` on the mount; `ausearch -m avc` |
| Logs show 5xx with a `request_id` | app error | grep that id in `sudo podman logs scholarship-ai-assistant_backend_1` |
| A source reads `failing` with fewer failures than `HEALTH_WINDOW_N` | discovery flips on first failure; monitoring recovers it next run | ADR-0008 |

## 13. Deployment record

| | |
|---|---|
| Deployed | 2026-10-18 (containers started 18:48 UTC); public-internet checks passed 2026-10-19 |
| Branch / commit | `002-source-monitoring-hardening` @ `e7b3c24` (VM was on `001-scholarship-mvp` @ `68f8569`) |
| Host | Oracle Linux 9.8, aarch64, SELinux enforcing, firewalld; public IP `130.61.159.24` |
| Containers | `scholarship-ai-assistant_backend_1`, `scholarship-ai-assistant_caddy_1` (image `caddy:2-alpine`); volumes `…_backend_storage`, `…_caddy_data`, `…_caddy_config` |
| Database | Same Neon pooled database as development (host/db/user hashes matched); already at Alembic head `f2b8d4a6c1e3`, no migration was needed |
| Method | Caddyfile validated with `caddy validate` (podman) → new image built while the old container still served → old container removed → `up -d` → verified on loopback → verified from outside |
| Rollback kept | previous image `e66ea19b3a8d` (now untagged); restore with `sudo podman tag e66ea19b3a8d localhost/scholarship-ai-assistant_backend:latest`, check out `68f8569`, compose up, and re-add `8000/tcp` in firewalld |
| Also changed | VM `.env` set to mode 600; firewalld `http`+`https` added, `8000/tcp` removed |

Outcome (all verified): `https://130.61.159.24/health` 200 (self-signed certificate); `http://…/` 308 → HTTPS;
`/metrics` 404 through Caddy (404 for `/Metrics` and `//metrics` too) and 401 direct on loopback without a token;
`openapi.json` 34 paths (was 23); backend bound to `127.0.0.1:8000` only; plain `:8000` unreachable from outside.

Not done in this deployment: Neon point-in-time-recovery check and restore drill (§8), the live smoke tests,
the document-persistence redeploy check (§10), and the first production retention run (§7).

### Post-deployment checks, 2026-10-22

- **Document persistence across a real recreate: PASS** (section 10 procedure). Same SHA-256 and size, same DB row, readable
  through the app's storage layer in the new container. Test data removed afterwards.
- **First production retention run: PASS.** Dry run on the VM: 0 rows; real run: `deleted 0 row(s)`. The fetch log held 650
  rows, the oldest 44 days old (inside the 90-day window), so 0 is correct. 92 monitoring runs show the scheduler working.
  The first run that deletes anything is expected around 2026-12-07.
- **Smoke tests:** Qdrant pair run and **failed** (connection reset, see section 11); Gemini and discovery smoke tests not run (daily quota
  exhausted). Neon PITR still open (section 8).
- **Defect found:** upload returns 500 when Gemini errors (section 11).
