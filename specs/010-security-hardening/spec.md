# Feature Specification: Security Hardening of the Live Server

**Feature Branch**: `010-security-hardening`
**Created**: 2026-10-29
**Status**: Draft
**Input**: User description: "Security hardening of the live server (feature 010). Operator-only governance, safe fetching of user-supplied URLs, least-privilege database access, credential strength, upload limits, audit log, reliable recovery after database errors, delivered email alerts, and deploying current main plus this feature to the VM."

**Source of truth**: `docs/full-project-audit.md` rows X-02, X-04, X-05, X-06, X-09, X-10, B-58, B-115, P4-AU-1, P4-SR-2, P4-PR-1, P4-OF-4, P4-DA-4; Blueprint v1.8 §14 (`audit_log`), §19, §22 (010 row), §23 (process rules), §27 A17 and A20; PRD v1.2 FR-DISC-8 and NFR-SEC-1..7; ADR-0010, ADR-0011 (Q4, Q5), ADR-0012 (decision 4), ADR-0015; `specs/charter.md`; `.specify/memory/constitution.md` (Principles III and IV). This spec layers on features 001–002 without changing what any scholarship record means, and it is written from the code at `main` (`de39b9a`).

## Why this feature exists

The product is public on a cloud VM with open sign-up. The audit and the owner's read-only checks of the VM and database found that today any signed-up user can approve or reject sources, start monitoring runs (each one spends the scarce LLM allowance) and read operational logs; that a user-supplied web address can make the server request internal addresses such as the cloud metadata address and return what it finds; that the running application holds owner-level database rights; that passwords have no rules at all and the session-signing secret is never checked; that uploads have no size limit and a bad PDF causes a server error; that nothing records who did what; and that email alerts are never sent because mail is not configured. This feature closes those gaps before any other remaining feature is built (ADR-0015). It adds no student-visible capability; every student feature keeps working exactly as today.

## Clarifications (owner decisions, recorded 2026-10-29)

These were decided by the owner before this spec was written. They are requirements or assumptions below, not open questions.

- There is **one operator: the owner**. Operator status can be granted only by a command run on the server, never through any API.
- Email alerts go out through the owner's own mail account (Gmail with an App Password), configured on the server.
- Passwords: minimum 8 characters, reject above 72 bytes (never silently cut), no composition rules. Email addresses are compared case-insensitively.
- Uploads: PDF only (checked by content), maximum 10 MB.
- `/metrics` stays reachable only from inside the VM (ADR-0011 Q5, unchanged).
- Out of scope: refresh tokens and password reset (008), per-user LLM limits (003), full roles and review queue (006), closing sign-up, live submission (Phase 6).

## Deviations from the Blueprint, PRD and ADRs (deviation stop rule)

Blueprint §23 rule 2 requires any difference found in research to be put to the owner and recorded in the Blueprint and PRD. The owner's brief for this feature contains the following differences; they are **owner-decided** and are recorded here. **Recording them in Blueprint §14/§19/§22, PRD FR-DISC-8/NFR-SEC-4/NFR-SEC-7 and ADR-0010/ADR-0011 is a task for the plan stage and must be done before the feature is called done (FR-PROC-3).**

| # | What the owner decided | What the current documents say | Documents to amend |
|---|---|---|---|
| D-1 | The address check applies to **every redirect hop**, including candidate validation. | ADR-0010 (decision 2, negative consequences), ADR-0011 Q4 and ADR-0015 decision 1 accept the redirect risk as a known residual risk and say no redirect-aware fetch is added. Blueprint §19 repeats that the residual risk "stays accepted for candidate validation". | ADR-0010 (amendment), ADR-0011 Q4 (superseded), ADR-0015 (note), Blueprint §19, PRD NFR-SEC-4 (clarify that "reach" includes redirects) |
| D-2 | Operator-only also covers **reading source fetch logs and candidate-validation results**. | FR-DISC-8, A17 and the §22 010 row restrict only approve/reject (and, in §22, monitoring runs). | PRD FR-DISC-8, Blueprint §22 010 row and A17 |
| D-3 | **Granting operator status** is an audited action. | PRD NFR-SEC-7 lists sign-up, source approval/rejection, manually started runs and submission approvals only. | PRD NFR-SEC-7, Blueprint §14 note |
| D-4 | Gmail (App Password) is the mail provider. | Blueprint §47 "NOT YET DEFINED" item 5 leaves the SMTP provider to the 010 spec. | Blueprint §47 item 5 (mark decided) |

Nothing else in the owner's brief differs from the Blueprint, PRD or ADRs.

## User Scenarios & Testing *(mandatory)*

The "users" are the **operator** (the owner), the **student** (every other signed-in user) and an **attacker** who can sign up freely and supply URLs and files. Stories are ordered by how much exposure each one removes.

### User Story 1 - Only the operator governs sources (Priority: P1)

Approving or rejecting a candidate source, starting a manual monitoring run, reading a source's fetch log and reading candidate-validation results are operator-only. Any other signed-in user gets a clear "forbidden" response (distinct from "not signed in"). Operator status is granted to the owner by a command run on the server; no API can grant or revoke it. All student features stay available to every signed-in user exactly as today. `/metrics` stays reachable only from inside the VM.

**Why this priority**: Today any account holder can change which sources the product treats as authoritative (Constitution Principle IV) and burn the daily LLM allowance. Sign-up is open, so this is exploitable by anyone.

**Independent Test**: Create two users, make one the operator by the server command, and call each operator-only capability as each user and as an unauthenticated caller. Then run the existing student journeys as the non-operator and confirm no change.

**Acceptance Scenarios**:

1. **Given** a signed-in non-operator, **When** they approve a candidate source, **Then** they receive a forbidden response and no source is created and no candidate changes status.
2. **Given** a signed-in non-operator, **When** they reject a candidate source, start a manual monitoring run, read a source fetch log, or read a candidate's validation results, **Then** each returns forbidden and nothing is started, changed or disclosed.
3. **Given** the operator, **When** they do each of those five things, **Then** they succeed exactly as they do today.
4. **Given** an unauthenticated caller, **When** they call any of those five capabilities, **Then** the response is the existing "not signed in" response, not "forbidden".
5. **Given** any signed-in user, **When** they use profile, discovery, matching, Q&A, documents, applications, alerts or coverage, **Then** behaviour is unchanged from `main`.
6. **Given** any signed-in user, **When** they try to make themselves or anyone else an operator through any API request (including by adding an extra field to sign-up or profile updates), **Then** operator status does not change.
7. **Given** a user who is not an operator, **When** the owner runs the server command to grant operator status to that user's email, **Then** the user's next request is accepted as operator.
8. **Given** the public internet, **When** anyone requests `/metrics`, **Then** the public listener still refuses it (ADR-0011 Q5, unchanged).

---

### User Story 2 - User-supplied URLs cannot reach internal addresses (Priority: P1)

When a student gives a URL (URL match) or the operator approves a source, and when candidate sources are validated, the server must not reach private, loopback, link-local, cloud-metadata or other reserved addresses. The check applies to the first address and to every redirect hop. The domain given at source approval must be a public hostname. A refused request returns a clear error and is logged.

**Why this priority**: A signed-up user can currently make the VM request its own metadata address and get the extracted result back. This is a credential-theft path on a cloud VM.

**Independent Test**: Against a fake network that records every connection attempt, submit a battery of blocked URLs (loopback, private ranges, link-local, metadata address, the IPv6 equivalents, IPv4-mapped IPv6, `localhost`-style names, numeric encodings, a public name that resolves to a private address, a public URL that redirects to a private one at hop 1, hop 2 and the last allowed hop) through each entry point and confirm that no connection to a non-public address is ever attempted.

**Acceptance Scenarios**:

1. **Given** a URL whose host is, or resolves to, a non-public address, **When** a student submits it to URL match, **Then** no request is sent to that address, the student gets a clear "address not allowed" error, and the refusal is logged.
2. **Given** a public URL that answers with a redirect to a non-public address at any hop, **When** URL match, candidate validation or any other fetch of a user-supplied or candidate URL follows it, **Then** the redirect to the non-public address is refused before any request to it is sent, and no content from it is used.
3. **Given** a host name that resolves to several addresses and at least one is non-public, **When** it is fetched, **Then** it is refused.
4. **Given** a redirect chain longer than the allowed number of hops, **When** it is followed, **Then** the fetch is refused with a clear error rather than followed indefinitely.
5. **Given** the operator approving a candidate with a domain that is an IP address, a single-label or internal-only name (for example `internal.corp`, `localhost`), or a name that resolves to a non-public address, **When** they submit the approval, **Then** it is rejected with a clear validation error and no source is created.
6. **Given** the operator approving a candidate whose domain already exists in the registry, **When** they submit, **Then** they get a clear conflict error, not a server error.
7. **Given** a public URL that resolves only to public addresses, **When** it is fetched through any entry point, **Then** it works as today.
8. **Given** a refusal from any entry point, **When** the operator reads the server log, **Then** it shows who asked (user), which entry point, and why it was refused, without logging secrets.

---

### User Story 3 - The running app has least-privilege database access (Priority: P1)

The running application connects with credentials that can only read and write data. It cannot create, alter or drop tables, and cannot create roles or databases, and it does not bypass row-level protections. Schema changes (migrations) use separate owner credentials that the owner applies. Every existing feature keeps working with the restricted credentials.

**Why this priority**: The app currently connects as the database owner, so any bug that reaches the database (for example injection) can destroy or rewrite the schema.

**Independent Test**: Run the full automated test suite and the deep smoke test with the restricted credentials; separately, with the restricted credentials, attempt to create a table, alter an existing one, drop one and create a role, and confirm all are refused. Run migrations with the owner credentials and confirm they succeed.

**Acceptance Scenarios**:

1. **Given** the application's credentials, **When** a schema-changing or role-creating statement is attempted, **Then** the database refuses it.
2. **Given** the application's credentials, **When** any existing feature runs (sign-up, profile, discovery, matching, Q&A, documents, applications, alerts, coverage, monitoring, scheduled jobs, the retention job), **Then** it works as before.
3. **Given** a new migration, **When** it is applied with the owner credentials, **Then** the restricted role automatically has the data access it needs on the new tables, without a manual grant per table.
4. **Given** the app is started with credentials that have owner-level rights, **When** it starts, **Then** it logs a visible warning (it still starts; the owner decides).
5. **Given** the runbook, **When** the owner follows it, **Then** the owner can create the restricted role, apply migrations with owner credentials, and start the app with the restricted credentials, without the app ever holding the owner credentials at run time.

---

### User Story 4 - Credentials must be strong enough (Priority: P2)

Passwords must be at least 8 characters and are rejected above 72 bytes (never silently cut). There are no composition rules. Email addresses are compared case-insensitively. The application refuses to start if the session-signing secret is shorter than 32 characters or equals the placeholder in the sample environment file.

**Why this priority**: Weak or silently truncated credentials undermine every other control, but exploitation needs more effort than the P1 items.

**Independent Test**: Sign up with passwords of 0, 7, 8, 72 and 73 bytes (including multi-byte characters) and with `Ada@x.com` then `ada@x.com`; start the app with a 31-character secret, a 32-character secret and the placeholder.

**Acceptance Scenarios**:

1. **Given** a password under 8 characters (including empty), **When** a user signs up, **Then** it is rejected with a clear message and no account is created.
2. **Given** a password of 8 to 72 bytes, **When** a user signs up, **Then** it is accepted regardless of character mix.
3. **Given** a password above 72 bytes (counting bytes, so multi-byte characters count more than one), **When** a user signs up, **Then** it is rejected with a clear message; it is never accepted and cut.
4. **Given** an account registered as `Ada@x.com`, **When** someone signs up as `ada@x.com`, **Then** it is rejected as already registered.
5. **Given** an existing account registered with mixed-case email, **When** its owner signs in with any casing of that address, **Then** sign-in succeeds.
6. **Given** a session-signing secret shorter than 32 characters or equal to the sample placeholder, **When** the app starts, **Then** it refuses to start with a message that names the problem and does not print the secret.
7. **Given** an existing account whose password is shorter than 8 characters or longer than 72 bytes, **When** its owner signs in, **Then** sign-in still works (rules apply to new passwords only; password change/reset belongs to 008).

---

### User Story 5 - Uploads are limited and safe (Priority: P2)

Document uploads and CV import accept only PDF files (checked by content, not by file name) up to 10 MB. A larger or non-PDF file gets a clear error, never a server error. A CV import must not make other users' requests wait.

**Why this priority**: An unbounded upload can exhaust memory or disk and a bad PDF currently causes a 500; a slow CV import stalls everyone because the server handles requests with a single worker.

**Independent Test**: Upload files of exactly 10 MB, 10 MB plus one byte, a renamed non-PDF, a PDF renamed to another extension, an empty file, a corrupt PDF and an encrypted PDF to both entry points; while a CV import with a deliberately slow model call is running, time a simple authenticated request from another user.

**Acceptance Scenarios**:

1. **Given** a valid PDF of exactly 10 MB, **When** it is uploaded to a document upload or CV import, **Then** it is accepted.
2. **Given** a file of 10 MB plus one byte, **When** it is uploaded, **Then** it is rejected with a clear "file too large" error, nothing is stored and no row is left behind.
3. **Given** a file that is not a PDF by content (even if named `.pdf`), **When** it is uploaded, **Then** it is rejected with a clear "only PDF files are accepted" error and nothing is stored.
4. **Given** a real PDF whose file name has another extension, **When** it is uploaded, **Then** it is accepted (content decides).
5. **Given** an empty, corrupt or encrypted PDF, **When** it is uploaded or imported, **Then** the user gets a clear error and never a server error.
6. **Given** a CV import in progress whose model call takes several seconds, **When** another user makes an ordinary authenticated request, **Then** that request is answered without waiting for the import.

---

### User Story 6 - A record of who did what (Priority: P2)

The product records who did what and when for: sign-up, source approval, source rejection, a manually started monitoring run, a submission approval, and granting operator status. Each record has an id, the user, the action, the entity affected and the time (Blueprint §14 `audit_log`). Only the operator can read it, if any read path exists.

**Why this priority**: Without it, the owner cannot reconstruct an incident or prove who changed source authority; it supports the other controls but does not block them.

**Independent Test**: Perform each of the six actions through its entry point and confirm exactly one correct record each; confirm a student cannot read the records through any route.

**Acceptance Scenarios**:

1. **Given** a successful sign-up, **When** it completes, **Then** one record exists with the new user, the action "sign-up" and the time.
2. **Given** the operator approves or rejects a candidate, **When** it succeeds, **Then** one record exists naming the operator, the action and the candidate or source affected.
3. **Given** a manually started monitoring run, **When** it is accepted, **Then** one record exists naming the user who started it and the run.
4. **Given** a student records a submission approval for their application, **When** it succeeds, **Then** one record exists naming that student and the approval.
5. **Given** the owner runs the server command granting operator status, **When** it succeeds, **Then** one record exists naming the user who received the status and the action.
6. **Given** an action that fails or is refused (forbidden, validation error, conflict), **When** it ends, **Then** no audit record claims it succeeded.
7. **Given** any read path for audit records, **When** a non-operator calls it, **Then** they get forbidden; when no read path exists, the records are readable by the owner directly on the server.
8. **Given** the audit record cannot be written, **When** an audited action runs, **Then** the action does not complete without its record (they succeed or fail together).

---

### User Story 7 - The app recovers after a database error (Priority: P2)

After a database error inside a step that carries on (candidate detection during extraction, discovery steps, and other broad catches), the session is rolled back so the next write still works and the run can finish and report its result.

**Why this priority**: Today one database error inside a "never fails" step leaves the session unusable, so the next log write and the closing coverage summary fail, turning a recoverable error into a failed run.

**Independent Test**: Inject a database error into candidate detection, into each discovery step, and into any other broad catch found in research; confirm the surrounding run or request still completes, the next write succeeds and the error is logged.

**Acceptance Scenarios**:

1. **Given** a database error inside candidate detection during a source check, **When** the step swallows the error, **Then** the session is rolled back, the source's fetch outcome is still recorded, and the run continues with the next source.
2. **Given** a database error inside one discovery step, **When** the step is skipped, **Then** the remaining steps and the closing coverage summary still run and the response is returned.
3. **Given** any other step that catches a broad error and continues, **When** the error is a database error, **Then** the same rollback happens before continuing.
4. **Given** a non-database error in such a step, **When** it is swallowed, **Then** behaviour is as today.

---

### User Story 8 - Email alerts are delivered (Priority: P3)

Alert emails are sent through the owner's configured mail account (Gmail with an App Password) on the server. When mail is not configured, alerts still appear in the app and the reason is logged. Mail credentials live only in the server's environment, never in the repository.

**Why this priority**: Alerts are a Production-phase promise (B-115) but they are not a security exposure; they ship last among the code changes.

**Independent Test**: With a fake mail server, trigger an alert for a user with email alerts on and confirm one message is delivered; repeat with mail unconfigured and with the mail server refusing the connection.

**Acceptance Scenarios**:

1. **Given** mail is configured and a user has email alerts enabled, **When** an alert is created by a monitoring run, **Then** one email is sent to the user's address and the alert is also visible in the app.
2. **Given** mail is not configured, **When** an alert is created, **Then** the alert appears in the app and the log states that email was skipped because mail is not configured.
3. **Given** mail is configured but sending fails, **When** an alert is created, **Then** the alert still appears in the app, the failure and its reason are logged without the password, and the monitoring run does not fail.
4. **Given** the repository and its history, **When** it is searched for the mail password, **Then** nothing is found; the sample environment file shows only placeholders.

---

### User Story 9 - The server runs the current code (Priority: P3)

Current `main` plus feature 010 is deployed to the VM, with post-deploy checks that show the controls above are live.

**Why this priority**: Nothing above protects users until the live server runs it; but it must come last because it depends on every other story.

**Independent Test**: After deployment, run the post-deploy check list against the public address and the VM.

**Acceptance Scenarios**:

1. **Given** the deploy runbook, **When** the owner follows it in order (owner-credential migrations first, then restart with restricted credentials), **Then** the server reports the deployed commit and it equals `main` plus feature 010.
2. **Given** the deployed server, **When** the post-deploy checks run, **Then** all of these pass: the site answers over HTTPS; sign-up and sign-in work with the new password rules; a non-operator is forbidden on the five operator-only capabilities and the operator is not; URL match to the metadata address is refused; the app's database credentials cannot create a table; a test alert email reaches the owner; public `/metrics` is refused; and the owner's account is the only operator.
3. **Given** any post-deploy check fails, **When** the owner follows the runbook's rollback step, **Then** the previous version is restored and the failing check and cause are recorded.
4. **Given** existing accounts that differ only by email case, **When** the owner runs the read-only pre-check before migrating, **Then** collisions are listed and the deploy halts until the owner decides.

---

### Edge Cases

- **Existing mixed-case emails**: Existing accounts keep working with any casing at sign-in. If two existing accounts differ only by case, the case-insensitive rule cannot be applied silently: the deploy halts and the owner decides (FR-CRED-5).
- **Operator demoted**: The owner revokes operator status with the same server command; the user loses operator access on their very next request (no waiting for the session to expire). A monitoring run the operator started before demotion finishes; its audit record stays.
- **Redirect chain longer than allowed**: Refused with a clear error; the number of allowed hops is fixed and documented (assumption: 5).
- **DNS returns several addresses including a private one**: Refused if any address is non-public. The address actually connected to must be one that passed the check (no change between check and connect).
- **Upload exactly at the limit**: Exactly 10 MB is accepted; one byte more is refused.
- **Upload with a misleading name**: A non-PDF named `.pdf` is refused; a PDF without a `.pdf` name is accepted.
- **Unicode passwords**: The limit counts bytes, so 25 four-byte characters (100 bytes) are refused while 72 ASCII characters are accepted.
- **Operator grant for a missing email**: The server command reports "no such user" and changes nothing; re-granting an operator is a harmless no-op that is not audited twice.
- **Two simultaneous sign-ups differing only by email case**: Exactly one succeeds; the other gets the "already registered" response.
- **Audit write fails during an audited action**: The action does not complete (US6 scenario 8); the user gets a clear error.
- **IPv6 and encoded addresses**: IPv4-mapped IPv6 addresses, numeric encodings of IPv4 addresses and `localhost`-style names are treated as non-public.
- **Mail unreachable at scheduled time**: The scheduled run completes; email for that alert is not retried within this feature (the in-app alert is the record).
- **Restricted database credentials and a new table**: A migration that adds a table must not leave the app unable to use it (US3 scenario 3).

## Requirements *(mandatory)*

### Functional Requirements

**Operator-only governance (US1)**

- **FR-AUTHZ-1**: The system MUST allow only an operator to approve a candidate source, reject a candidate source, start a manual monitoring run, read a source's fetch log, and read a candidate's validation results.
- **FR-AUTHZ-2**: Every signed-in non-operator calling any of those five capabilities MUST receive a clear "forbidden" response that is distinct from the "not signed in" response, with no side effects and no data disclosed.
- **FR-AUTHZ-3**: Operator status MUST be grantable and revocable only by a command run on the server by the owner. No API route, sign-up field or profile field MAY set or change it.
- **FR-AUTHZ-4**: An operator change (grant or revoke) MUST take effect on the user's next request without requiring a new sign-in.
- **FR-AUTHZ-5**: The system MUST have exactly one operator after deployment: the owner's account.
- **FR-AUTHZ-6**: All student features (profile, discovery, matching, Q&A, documents, applications, alerts, coverage) MUST remain available to every signed-in user with unchanged behaviour.
- **FR-AUTHZ-7**: `/metrics` MUST remain reachable only from inside the VM (ADR-0011 Q5); the public listener MUST keep refusing it.

**Safe fetching of user-supplied URLs (US2)**

- **FR-SSRF-1**: The system MUST NOT send a request to a private, loopback, link-local, cloud-metadata or otherwise reserved address (IPv4 or IPv6, including IPv4-mapped IPv6 and numeric encodings, and `localhost`-style names) when fetching a URL supplied by a user (URL match), a URL entered by the operator at source approval, or a candidate URL during validation.
- **FR-SSRF-2**: The check MUST be applied to the initial URL and to every redirect hop before a request is sent to that hop; only `http` and `https` are allowed.
- **FR-SSRF-3**: A host name that resolves to several addresses MUST be refused if any address is non-public, and the address actually connected to MUST be one that passed the check.
- **FR-SSRF-4**: A redirect chain longer than the allowed number of hops MUST be refused with a clear error.
- **FR-SSRF-5**: A refused request MUST return a clear error to the caller (not a server error) and MUST be logged with the user, the entry point and the reason, without secrets.
- **FR-SSRF-6**: The domain submitted at source approval MUST be a public host name: not an IP address, not a single-label or internal-only name, and not resolving to a non-public address; otherwise approval MUST be rejected with a clear validation error and create nothing.
- **FR-SSRF-7**: Approving a candidate with a domain already in the registry MUST return a clear conflict error, not a server error.
- **FR-SSRF-8**: Public URLs MUST behave as today at every entry point.

**Least-privilege database access (US3)**

- **FR-DBROLE-1**: The running application MUST connect with credentials that can read and write data but cannot create, alter or drop tables, cannot create roles or databases, and does not bypass row-level protections.
- **FR-DBROLE-2**: Migrations MUST use separate owner credentials that only the owner applies; the running application MUST NOT hold them.
- **FR-DBROLE-3**: A table or sequence added by a migration MUST be usable by the application without a separate manual grant.
- **FR-DBROLE-4**: Every existing feature, the scheduler, the retention job and the deep smoke test MUST work with the restricted credentials.
- **FR-DBROLE-5**: The application MUST log a visible warning at start when its credentials have owner-level rights.
- **FR-DBROLE-6**: A runbook MUST describe creating the restricted role, applying migrations with owner credentials, switching the application to the restricted credentials and rolling back.

**Credential strength (US4)**

- **FR-CRED-1**: A new password MUST be at least 8 characters and at most 72 bytes; one outside that range MUST be rejected with a clear message and MUST NOT be cut or altered. There MUST be no composition rules.
- **FR-CRED-2**: Email addresses MUST be compared case-insensitively for sign-up uniqueness and sign-in; sign-up MUST refuse an address that differs from an existing one only by case.
- **FR-CRED-3**: The application MUST refuse to start if the session-signing secret is shorter than 32 characters or equals the sample-environment placeholder, with a message that names the problem and never prints the secret.
- **FR-CRED-4**: Sign-in for existing accounts MUST keep working regardless of the password length rules and the casing of the email they registered with.
- **FR-CRED-5**: Before the case-insensitive rule goes live, a read-only check MUST list existing accounts whose emails differ only by case; if any exist, deployment MUST halt for an owner decision.

**Upload limits (US5)**

- **FR-UPLOAD-1**: Document upload and CV import MUST accept only PDF files, decided by file content and not by name, and no larger than 10 MB (10 MB exactly is accepted).
- **FR-UPLOAD-2**: A file that is too large, not a PDF, empty, corrupt or encrypted MUST get a clear error, never a server error, and MUST leave nothing stored and no orphan row.
- **FR-UPLOAD-3**: A CV import MUST NOT delay other users' requests while it runs.

**Audit log (US6)**

- **FR-AUDIT-1**: The system MUST record who did what and when for sign-up, source approval, source rejection, a manually started monitoring run, a submission approval, and granting operator status. Each record has an id, the user, the action, the entity affected and the time (Blueprint §14).
- **FR-AUDIT-2**: A record MUST be written only for an action that succeeded, and an audited action MUST NOT complete if its record cannot be written.
- **FR-AUDIT-3**: Audit records MUST be append-only from the application's point of view: no route or feature edits or deletes them.
- **FR-AUDIT-4**: Audit records MUST be readable only by the operator; no student route returns them.

**Recovery after database errors (US7)**

- **FR-RECOV-1**: After a database error caught inside a step that continues (candidate detection and the discovery steps, and every other broad catch research finds), the system MUST roll back the session so the next write succeeds.
- **FR-RECOV-2**: A run or request containing such a step MUST still record its outcome and finish, including the closing coverage summary.

**Delivered alerts (US8)**

- **FR-MAIL-1**: When mail is configured and a user has email alerts enabled, each new alert MUST be emailed to that user through the owner's configured mail account.
- **FR-MAIL-2**: An alert MUST always appear in the app. When mail is not configured or sending fails, the reason MUST be logged (without secrets) and the run MUST NOT fail.
- **FR-MAIL-3**: Mail credentials MUST be supplied only through the server environment; they MUST NOT be in the repository, and the sample environment file MUST contain placeholders only.

**Deployment (US9)**

- **FR-DEPLOY-1**: `main` plus feature 010 MUST be deployed to the VM, with owner-credential migrations applied before the application restarts with restricted credentials.
- **FR-DEPLOY-2**: A post-deploy check list MUST be defined and run (see US9 scenario 2), and its result recorded.
- **FR-DEPLOY-3**: A rollback step MUST be documented and be usable if a post-deploy check fails.

**Process rules (Blueprint §23)**

- **FR-PROC-1 (entry-point rule)**: Every capability in this feature MUST be reachable from an API route, the scheduler or an operator CLI command, and MUST have a test that starts from that entry point (see the table below). No capability may exist only as an internal helper.
- **FR-PROC-2 (deviation stop rule)**: If research or planning finds anything that differs from the Blueprint or PRD beyond D-1..D-4, work MUST stop and the owner MUST decide, with the decision recorded in both.
- **FR-PROC-3**: D-1..D-4 MUST be recorded in the Blueprint, the PRD and the affected ADRs before the feature is called done.

### Entry-point map (FR-PROC-1)

| Capability | Entry point | Test starts at |
|---|---|---|
| Approve / reject candidate; read validations (operator only) | `/sources/candidates/...` routes | those routes, as operator, non-operator, anonymous |
| Manual monitoring run (operator only) | `POST /monitoring/runs` | that route |
| Read source fetch log (operator only) | `GET /sources/{id}/fetch-log` | that route |
| Grant / revoke operator | operator CLI command on the server | the CLI command |
| Safe fetch of user URL | `POST /scholarships/match-url` | that route |
| Safe fetch at source approval; public-domain rule | approve route | that route |
| Safe fetch in candidate validation | scheduler-driven and manual monitoring runs | the manual run route and the scheduler tick |
| Password / email rules | sign-up and sign-in routes | those routes |
| Signing-secret check | application start | starting the app |
| Upload limits | document upload route; CV import route | those routes |
| Audit records | sign-up, approve, reject, manual run, submission-approval routes; operator CLI command | those entry points |
| Rollback after database error | scheduler/manual monitoring run; discovery route; URL match route | those entry points with an injected database error |
| Email alert delivery | scheduler / manual monitoring run | the run, with a fake mail server |
| Least-privilege access | whole app | automated suite and smoke test under restricted credentials |
| Deployment and post-deploy checks | runbook executed on the VM | the post-deploy check list |

### Key Entities

- **Operator status**: a yes/no property of a user, changed only by the server command; at launch only the owner has it.
- **Audit record**: who (user), what (action), which thing (entity), when; append-only; operator-readable.
- **Candidate source / source registry entry**: unchanged meaning; approval now requires an operator and a public domain.
- **Fetch request**: a request the server makes to a URL a user or a third-party page supplied; every hop is checked.
- **Alert**: an existing in-app notification; may also be emailed.

## Assumptions

- The owner already has a Gmail account with an App Password and will place it only in the server environment.
- "Forbidden" is a distinct, documented response class for a signed-in user lacking permission.
- The fixed redirect limit is 5 hops; the plan may change it only with the owner's agreement.
- Source fetch logs are read only through the per-source fetch-log route. The monitoring-run list and detail routes and the aggregate source-health route stay open to signed-in users because they expose no raw fetch-log rows. If the plan finds a route that does expose fetch-log rows, it follows FR-PROC-2.
- For the server command (no signed-in actor), the audit record's user is the person receiving operator status, and the entity says so.
- Only successful actions are audited; refusals go to the application log.
- Existing accounts and sessions survive deployment; signed-in sessions need not be invalidated, except that operator status is read fresh on each request (FR-AUTHZ-4).
- The same server command that grants operator status can revoke it.
- Approving a source already trusts the operator to enter the other registry fields; cross-checking those fields against the candidate (P4-SR-2, second half) is left to 006 because only the operator can now reach it.
- Retention, backups, and scheduler behaviour are unchanged except as described.

## Out of Scope

- Refresh tokens and password reset (008), including forcing existing weak passwords to change.
- Per-user LLM limits (003).
- Full roles, reviewer queue and cross-checking approval fields against the candidate (006).
- Closing sign-up or adding invitations.
- Live submission to third-party systems (Phase 6; Constitution Principle III stays in force).
- New student-facing features, a UI, rate-limit changes, or malware scanning of uploads.
- Changing the `/metrics` exposure decision (ADR-0011 Q5).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For each of the 5 operator-only capabilities, 100% of tests as a non-operator return forbidden with no side effect, and 100% as the operator succeed; the full existing student regression suite passes with no edits to its expectations.
- **SC-002**: Across a battery of at least 20 blocked-address cases (loopback, private ranges, link-local, metadata, IPv6 and mapped forms, numeric encodings, `localhost`-style names, multi-address DNS with one private, redirects to a private address at every hop up to the limit), 0 connection attempts reach a non-public address, and 100% return a clear error and a log line.
- **SC-003**: Under the application's credentials, 0 of 4 attempts (create table, alter table, drop table, create role) succeed, 100% of the existing automated tests and the deep smoke test pass, and migrations succeed with owner credentials.
- **SC-004**: 100% of passwords under 8 characters or over 72 bytes are rejected; 100% of passwords from 8 to 72 bytes are accepted; 100% of case-variant duplicate emails are rejected; the app refuses to start in 100% of the three bad-secret cases (31 characters, empty, the placeholder) and starts with a 32-character secret.
- **SC-005**: A 10 MB PDF is accepted and a 10 MB plus one byte file is refused, at both upload entry points; a corpus of at least 8 bad files (non-PDF, renamed, empty, corrupt, encrypted, truncated, oversized, wrong extension) produces 0 server errors; during a CV import with a model call of at least 5 seconds, another user's simple authenticated request is answered in under 1 second.
- **SC-006**: Each of the 6 audited actions produces exactly one correct audit record (right user, action, entity, time within 5 seconds of the action); 0 student routes return audit records; 0 refused actions leave a record.
- **SC-007**: With a database error injected into each broad-catch step found by research, 100% of runs and requests still complete, record their outcome and complete the next write.
- **SC-008**: With mail configured, a test alert email reaches the owner's inbox within 5 minutes of the alert being created; with mail unconfigured or failing, 100% of alerts still appear in the app and 100% log a reason; the repository contains 0 mail credentials.
- **SC-009**: After deployment, the server reports a commit equal to `main` plus feature 010 and all 8 post-deploy checks of US9 scenario 2 pass; the owner is the only operator.
- **SC-010**: 100% of the requirements above map to at least one test that starts at an entry point from the Entry-point map, and all 13 audit rows in the traceability table are closed or have a stated, owner-accepted remainder.

## Traceability

| Requirement | Story | Audit row(s) | PRD / Blueprint ID |
|---|---|---|---|
| FR-AUTHZ-1..6 | US1 | X-04, B-58 | PRD FR-DISC-8, NFR-SEC-1; Blueprint §19, §22 (010), A17, A20; ADR-0012 d.4, ADR-0015 d.1; D-2 |
| FR-AUTHZ-7 | US1 | X-04 | ADR-0011 Q5; Blueprint §19 |
| FR-SSRF-1..5, 8 | US2 | X-05 | PRD NFR-SEC-4, NFR-SEC-2; Blueprint §19; ADR-0010, ADR-0011 Q4, ADR-0015; D-1 |
| FR-SSRF-6, 7 | US2 | P4-SR-2 (domain rule and duplicate-domain 500; field cross-check left to 006) | PRD FR-DISC-8, NFR-SEC-4; Blueprint §19, A17 |
| FR-DBROLE-1..6 | US3 | X-10 | PRD NFR-SEC-5; Blueprint §19, §22 (010), A20 |
| FR-CRED-1..5 | US4 | X-02, P4-AU-1 | PRD NFR-SEC-6, NFR-SEC-2; Blueprint §19, §20 |
| FR-UPLOAD-1, 2 | US5 | X-06 | PRD NFR-SEC-6, NFR-SEC-2; Blueprint §19 |
| FR-UPLOAD-3 | US5 | X-06, P4-PR-1 | PRD NFR-SEC-6; Blueprint §19 |
| FR-AUDIT-1..4 | US6 | X-09 | PRD NFR-SEC-7; Blueprint §14 `audit_log`, §19; D-3 |
| FR-RECOV-1, 2 | US7 | P4-OF-4, P4-DA-4 | PRD NFR-REL-2; Blueprint §22 (010), §33 |
| FR-MAIL-1..3 | US8 | B-115 | PRD NFR-REL-3; Blueprint §22 (010), §35, §47 item 5; D-4 |
| FR-DEPLOY-1..3 | US9 | X-10, X-04, X-05, B-115 (live closure) | Blueprint §22 (010 row: deploy current `main`); ADR-0015 d.1 |
| FR-PROC-1..3 | all | (process) | Blueprint §23 rules 1-2, A21; ADR-0015 d.5 |

Audit-row coverage: X-02 (US4), X-04 (US1), X-05 (US2), X-06 (US5), X-09 (US6), X-10 (US3), B-58 (US1), B-115 (US8), P4-AU-1 (US4), P4-SR-2 (US2, partly), P4-PR-1 (US5), P4-OF-4 (US7), P4-DA-4 (US7).
