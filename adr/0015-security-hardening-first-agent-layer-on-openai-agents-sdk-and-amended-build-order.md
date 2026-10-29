# ADR-0015: Close Security Gaps First (010), Rebuild All Five Agents on the OpenAI Agents SDK (011), and Amend the Build Order

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Accepted
- **Date:** 2026-10-27
- **Feature:** Roadmap decision adding features 010 (security hardening) and 011 (agent layer) and amending the build order of ADR-0012 (source: `docs/full-project-audit.md`; Blueprint v1.8 §47; PRD v1.2 §35)
- **Context:** `docs/full-project-audit.md` (merged 2026-10-26) found that none of the five agents is an agent loop. Q&A and Matching make one LLM call each; Discovery and Application are plain functions; the Main Agent is a keyword router from task T061 that no API route reaches. The OpenAI Agents SDK named in Blueprint §21 and `specs/001-scholarship-mvp/research.md` D1 was never installed; this was noticed in the `specs/002` research and no decision was taken. MCP, LangGraph checkpoints and HITL pauses were never built. Blueprint §19 controls are missing on the public server: there are no roles; user-supplied URLs get no address check in URL match and source approval (169.254.169.254 is reachable); the app connects as the database owner; there are no password or token-secret rules and no upload limits; there is no `audit_log`; SMTP is not configured. The owner confirmed the VM and database facts with read-only checks. ADR-0012's roadmap had no place for any of this.

## Decision

Treat the following as one cluster: the security work, the agent rebuild and the build order only make sense decided together.

1. **Feature 010 (security hardening) runs first, before 003.** It contains: the operator role and the minimal operator restriction (moved from 004, ADR-0012 decision 4; fuller roles stay in 006); an address check on user-supplied URLs that reuses the ADR-0010 guard; a least-privilege database role for the app (migrations keep owner credentials); password and token-secret rules; upload limits; `audit_log`; rollback after database errors; SMTP on the server; and deploying current `main` to the VM. The SSRF redirect risk accepted in ADR-0010 and ADR-0011 stays accepted.
   - Its last sentence is superseded by ADR-0016 on 2026-10-29.
2. **Feature 011 (agent layer) runs after 005**, and therefore after 006's paid-tier decision. All five agents are built on the OpenAI Agents SDK, with Gemini reached through its OpenAI-compatible endpoint. LangGraph stays the workflow engine and LangChain stays the tools/RAG glue. 011 also delivers: a chat route for the Main Agent; Q&A live fetch and numeric grounding; MCP; checkpoints and HITL pauses; per-intent schemas with a next action; a `url_match` normalize node; and an invalid-JSON retry. Behaviour contracts are unchanged: FR-ROUTE clarification (the T061 router tests become acceptance tests), Matching guardrails a-f, button/agent parity, approval gates and Blueprint Part B.
3. **Build order is 010 -> 003 -> 004 -> 006 -> 005 -> 011 -> 007 -> 008/009.** A housekeeping change with no spec runs before 010. This supersedes ADR-0012 decision 2 only; ADR-0012's other decisions stay.
4. **Features are specified one at a time from the current code.** 003's pre-audit spec, plan and tasks are updated before 003 is built.
5. **From 010 onward every spec follows three rules:**
   - **Entry-point rule:** each user-facing capability is reachable from an API route, the scheduler or an operator CLI command, with a test that starts there.
   - **Deviation stop rule:** any difference from the Blueprint or PRD found in research stops work until the owner decides, and the decision is recorded in both.
   - **Pre-flight:** before /sp.specify, the manager reads the related specs, ADRs and prior sessions; the owner verifies the relevant audit findings with read-only commands; assumptions are listed and approved (Blueprint v1.8 §23).

## Consequences

### Positive

- The exposure that exists on the public server today (reachable internal address, owner-level database access, no roles, no audit trail) is closed before more features are added.
- The agent layer follows Blueprint §21 and `specs/001-scholarship-mvp/research.md` D1 instead of drifting from them, and its gap with the audit findings is closed in one place.
- Placing 011 after 005 means the paid-tier decision (006) is known before agents that make more LLM calls are built.
- Behaviour contracts are unchanged, so the existing T061 router tests become acceptance tests rather than being discarded.
- The entry-point rule and deviation stop rule address how the audit's findings arose: capabilities that no route reached, and a Blueprint difference noticed but never decided.

### Negative

- 003 (quota governance) is delayed behind 010, so the free-tier quota problems it addresses persist for longer.
- 011 is a large feature that comes late in the order; until it ships, the agents remain as the audit found them.
- Specifying one feature at a time means later features have no spec until their turn, and 003's existing documents need updating before it is built.
- The SSRF redirect risk accepted in ADR-0010 and ADR-0011 remains open.
- Moving the minimal operator restriction from 004 to 010 changes ADR-0012 decision 4, and the build order changes ADR-0012 decision 2; a reader must consult this ADR alongside ADR-0012 to see the current plan.

## Alternatives Considered

- **Keep the ADR-0012 order and put security in 004.** Rejected: the exposure exists now.
- **Build the agents first.** Rejected: there is no budget gate yet, and the wrong tools would be used.
- **Put the orchestrator on the SDK and the other four agents on LangChain/LangGraph.** Rejected: it contradicts Blueprint §21 and D1.
- **One big gap-closure feature.** Rejected: too large.
- **Write all specs up front.** Rejected: they go stale, as 003's did.

## References

- `docs/full-project-audit.md`
- Blueprint v1.8 §7, §10, §15, §19, §21, §22, §23, §27 A19-A21, §36, §42, §47
- PRD v1.2 §9, FR-DISC-8, NFR-SEC-4..7, AC-6, OD-13..15, §35
- Related ADRs: ADR-0003, ADR-0004, ADR-0005, ADR-0007, ADR-0010, ADR-0011, ADR-0012 (decision 2 superseded; decision 4 moved)
