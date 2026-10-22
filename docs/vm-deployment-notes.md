# VM Deployment Notes

Operational facts discovered during a live VM redeployment session that cost
significant debugging time and were recorded nowhere else. Read this before
touching the Oracle VM.

## 1. Branch: the VM can silently be on the wrong branch

**Symptom:** The "live" server behaved like it was missing weeks of work —
endpoints from Phase 2 were 404ing — while everyone assumed Phase 2 was
deployed.

**Cause:** The Oracle VM's clone was checked out on the repo's default branch
(`main`), which only ever contained Phase 0 work. All real development lives
on `001-scholarship-mvp`. The VM ran Phase 0 code for two weeks before this
was noticed.

**Fix (original, historical):** the VM was switched to `001-scholarship-mvp`.

**Permanent fix (done):** `main` now carries the 001 work and, since 2026-10-19, the whole 002 feature
(merge commit `7b3f4cb`, which also brought in the last 001-branch commits). **`main` is now the branch to
track.** The VM's checkout was switched to `main` the same day (`git checkout main && git pull`). The
trap above can no longer silently serve stale code from a fresh clone, but still verify with
`git -C <repo-path> branch --show-current` and `git -C <repo-path> log -1 --oneline` after any
redeploy or VM rebuild. Feature branches should be merged to `main` before they are deployed, or the
VM must be deliberately switched and switched back.

## 2. Two separate Podman installs (root vs. user)

**Symptom:** `podman ps` shows nothing and `podman-compose down` reports
"no such container", while a container is demonstrably still running and
holding port 8000.

**Cause:** Podman is per-user on Linux — root and a regular user each have
their own, completely separate container store. The Phase 2 deployment used
`sudo podman-compose`, so the running container lives in **root's** Podman
store. Any podman command run without `sudo` queries the (empty) user store
instead and finds nothing.

**Diagnostic chain that found it:**
```
ss -tlnp | grep 8000          # find the PID holding the port
ps -p <pid> -o cmd            # confirm it's the backend process
# trace up to its parent conmon process
sudo podman ps -a             # root's store reveals the container
```

**Rule:** Always use `sudo` for podman on this VM. Never mix `sudo podman`
and plain `podman` — pick one (sudo) and use it for every podman/podman-compose
command, including `ps`, `logs`, `exec`, and `down`.

## 3. `podman-compose up -d --build` reuses an existing container by name

**Symptom:** The build succeeds, the container reports `Up`, but the old code
is still serving requests.

**Cause:** `podman-compose up -d --build` rebuilds the *image*, but if a
container with the same name already exists — even stopped — compose restarts
that existing container rather than creating a new one from the freshly built
image.

**Verify:** Compare container IDs before and after:
```
sudo podman ps -a --format '{{.Names}} {{.ID}}'   # before
sudo podman-compose up -d --build
sudo podman ps -a --format '{{.Names}} {{.ID}}'   # after — ID should differ
```

**Fix:** Remove the old container first, then bring it up fresh:
```
sudo podman rm <container-id-or-name>
sudo podman-compose up -d --build
```

## 4. Running tools inside the container: use `uv run`

**Symptom:**
```
$ podman exec backend alembic current
OCI runtime exec failed: exec failed: unable to start container process:
exec: "alembic": executable file not found in $PATH
```

**Cause:** The image installs and runs dependencies via `uv`, so binaries
like `alembic` aren't on the container's default `PATH` — they only resolve
inside `uv`'s managed environment.

**Fix:** Prefix the command with `uv run`:
```
sudo podman exec scholarship-ai-assistant_backend_1 uv run alembic current
```
This applies to any Python-installed console script run inside the
container (`alembic`, `pytest`, `uvicorn`, etc.) — not just `alembic`.

## 5. Container names are prefixed, and `sudo` cannot find `podman-compose`

On this VM podman-compose names containers `<project>_<service>_<n>`:
`scholarship-ai-assistant_backend_1` and `scholarship-ai-assistant_caddy_1`.
`podman exec backend …` fails; use the full name (or the ID from `sudo podman ps -a`).
Run compose by full path: `sudo /usr/local/bin/podman-compose -f docker-compose.podman.yml …`
(`sudo podman-compose` is "command not found": sudo's PATH excludes `/usr/local/bin`).

## 6. Public ports need BOTH the Oracle security list and firewalld

Opening firewalld alone leaves 80/443 timing out from the internet; the Oracle Cloud ingress rules
(TCP, `0.0.0.0/0`, **destination** ports 80 and 443, on the security list of the VM's subnet) are a
console step that cannot be done from the VM. Diagnose with a packet capture on `enp0s6`: zero
packets arriving means the block is upstream of the VM. Full detail: `docs/operations-hardening.md` §3.
Since the 002 deployment the backend listens on `127.0.0.1:8000` only and Caddy owns 80/443; plain
`:8000` is no longer reachable from outside (and `8000/tcp` was removed from firewalld).

## 7. Deployment log

- **2026-10-18/19 — 002-source-monitoring-hardening @ `e7b3c24`**: first Caddy/HTTPS deployment. Outcome and
  rollback info in `docs/operations-hardening.md` §13. Lesson: build the new image *before* removing the old
  container (`podman-compose … build backend`, then `stop`/`rm`, then `up -d`) to keep the outage to seconds.

## 8. Removing the backend container fails while Caddy exists; SSH can flap

- `sudo podman rm scholarship-ai-assistant_backend_1` fails with "container ... has dependent containers which must be removed before it"
  because `scholarship-ai-assistant_caddy_1` was created with a dependency on it (compose `depends_on`). Remove in this order:
  Caddy first, then the backend, then `up -d`. Removing both in one `podman rm a b` command in the
  wrong order removes Caddy but leaves the backend, which then only *restarts* with the same container ID,
  so check that the ID actually changed when you mean to recreate. Named volumes are unaffected.
- SSH (port 22) timed out for a few minutes at a time, three times in one session on 2026-10-22, while
  80/443 kept serving. It recovered by itself each time. Batch several commands per SSH session and retry
  after a few minutes before suspecting the VM; check `https://<ip>/health` to see whether the app is up.

## Verified deployment procedure

The exact sequence that worked, in order, with what to check after each step.

1. **Confirm branch**
   ```
   git -C <repo-path> branch --show-current
   ```
   Expect `main` (the branch to track; see section 1). If not, `git checkout main && git pull`
   (stash/commit any local changes first). If you are deliberately deploying a feature branch
   for testing, say so in the deployment log (section 7) and switch back to `main` afterwards. The
   public listener is now Caddy; see `docs/operations-hardening.md`.

2. **Pull latest**
   ```
   git -C <repo-path> pull
   ```
   Check the output names the commits you expect to see deployed.

3. **Identify and remove any existing container by name (see #3 above)**
   ```
   sudo podman ps -a --format '{{.Names}} {{.ID}} {{.Image}}'
   sudo podman rm <backend-container-id>
   ```
   Do this even if `podman-compose down` was already run — `down` can miss
   containers started outside the current compose invocation.

4. **Rebuild and start (always with sudo — see #2 above)**
   ```
   sudo podman-compose up -d --build
   ```
   Check: new container ID differs from the one removed in step 3.

5. **Run migrations (see #4 above for the `uv run` requirement)**
   ```
   sudo podman exec scholarship-ai-assistant_backend_1 uv run alembic current
   sudo podman exec scholarship-ai-assistant_backend_1 uv run alembic upgrade head
   ```
   Check: `alembic current` reports the expected head revision.

6. **Confirm the port is actually served by the new container**
   ```
   ss -tlnp | grep 8000
   ```
   Check: PID belongs to the container just started (cross-reference with
   `sudo podman inspect <container-id> | grep -i pid`).

7. **Prove new code is serving — endpoint-list verification**
   ```
   curl -s http://localhost:8000/openapi.json | grep -o '/applications[a-z/{}_-]*' | sort -u
   ```
   This is the real proof: a container reporting `Up` and a port that's open
   are both consistent with the *old* container serving (see #3). Only the
   actual route list confirms the new code is live. Compare the output
   against the routes you expect the deployed commit to expose.
- **2026-10-22 - post-deployment checks** (persistence across a real recreate, first retention run, partial
  smoke run): results in `docs/operations-hardening.md` section 13 and `docs/t-002-verification-log.md`.
