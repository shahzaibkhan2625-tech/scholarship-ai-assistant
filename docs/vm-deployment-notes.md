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

**Fix:** The VM is now checked out on `001-scholarship-mvp`. Verify with
`git -C <repo-path> branch --show-current` after any redeploy or VM rebuild —
don't assume.

**Permanent fix:** Merging `001-scholarship-mvp` into `main` removes this trap
entirely, since a fresh clone/redeploy defaults to `main`.

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
sudo podman exec backend uv run alembic current
```
This applies to any Python-installed console script run inside the
container (`alembic`, `pytest`, `uvicorn`, etc.) — not just `alembic`.

## Verified deployment procedure

The exact sequence that worked, in order, with what to check after each step.

1. **Confirm branch**
   ```
   git -C <repo-path> branch --show-current
   ```
   Expect `001-scholarship-mvp`. If not, `git checkout 001-scholarship-mvp`
   (stash/commit any local changes first).

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
   sudo podman exec backend uv run alembic current
   sudo podman exec backend uv run alembic upgrade head
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
