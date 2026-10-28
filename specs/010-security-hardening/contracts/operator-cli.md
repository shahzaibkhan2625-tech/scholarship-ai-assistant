# CLI Contract: Operator Management (010)

Entry point for FR-AUTHZ-3 / FR-AUTHZ-4 / FR-AUDIT-1 (operator grant). Follows `python -m app.services.retention` (`main(argv) -> int`, argparse, `SessionLocal` inside `main`, `raise SystemExit(main())`).

```text
uv run python -m app.services.operator grant  --email EMAIL
uv run python -m app.services.operator revoke --email EMAIL
```

On the VM, run inside the backend container (it holds only the restricted database credentials, which are sufficient):

```text
sudo podman exec scholarship-ai-assistant_backend_1 uv run python -m app.services.operator grant --email <owner email>
```

| Case | stdout | Exit | Audit |
|---|---|---|---|
| grant, user exists, not operator | `granted operator status to <email>` | 0 | one `operator.grant` row (user = grantee, entity `user:<id>`), written in the same transaction as the flag |
| grant, already operator | `<email> is already an operator` | 0 | none |
| revoke, user is operator | `revoked operator status from <email>` | 0 | none (not in the approved audit list); logged |
| revoke, not operator | `<email> is not an operator` | 0 | none |
| email not found | `no such user: <email>` | 1 | none; nothing changed |
| bad arguments | argparse usage | 2 | none |

Rules:
- The email is trimmed and lower-cased, then matched case-insensitively (same rule as sign-up).
- The change takes effect on the user's next HTTP request (no token reissue; the role is read from the row each request).
- No API route can do this. There is no `--all`, no list command, and no bulk form.
- Output never prints password hashes or tokens.

Tests start at `main([...])`: grant then call an operator route as that user (200) and as another user (403); revoke then call again (403); unknown email → exit 1; audit row counts per case above.
