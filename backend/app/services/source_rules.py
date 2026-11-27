"""Operator commands for extraction rules and the check interval (003 US5, FR-RULES-1/5/6, plan decision 7).

    python -m app.services.source_rules set  --domain D --operator-email E [--interval-minutes N]
                                              [--rules-file F [--fixture HTML --snapshot JSON]]
    python -m app.services.source_rules parity --rules-file F --fixture HTML --snapshot JSON
    python -m app.services.source_rules diff   --domain D --fixture HTML
    python -m app.services.source_rules record-snapshot --domain D --fixture HTML --confirm [--output JSON]

`set` is the ONLY write path for rules on a live source (re-running the seed would reset a failing source and
replace `extraction_rules` wholesale). It changes only `check_interval_minutes` and `deterministic` inside
`extraction_rules`; every other key and column stays byte-identical. It requires an existing operator's email and
writes a 010 audit row (`source.rules.set`, entity `source:<uuid>`) in the same transaction as the change. A rule
set can be enabled only when the parity check passes against the committed fixture and LLM snapshot. `parity`,
`diff` and `set` never call the provider; `record-snapshot` is the one command that does (exactly one request,
under the `backfill` class, refused without `--confirm`).

Exit codes: 0 ok, 1 refused or failed (nothing changed), 2 bad arguments.
"""

import argparse
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.llm_gate import run_as_backfill
from app.data.repositories import audit_repo, scholarship_repo, source_repo
from app.models.user import User
from app.schemas.extraction_rules import DeterministicRules
from app.schemas.source import validate_rule_keys
from app.tools import rule_extract
from app.tools.extract_listing import extract_listing_from_page

logger = logging.getLogger(__name__)

__all__ = ["ParityReport", "main", "parity_report"]


@dataclass(frozen=True)
class ParityReport:
    matched: list[str]
    llm_only: list[str]  # found by the LLM, missed by the rules: blocks enabling
    rules_only: list[str]  # found only by the rules: flagged to the owner, not blocking

    @property
    def passed(self) -> bool:
        return not self.llm_only


def parity_report(rules: DeterministicRules, html: str, snapshot_names: list[str], *, source_url: str) -> ParityReport:
    """Compare, by normalised name, what the rules find on the saved page with the recorded LLM snapshot."""
    found = rule_extract.rule_extract(html, rules, source_url=source_url).output.candidates
    rule_by_key = {rule_extract.normalise_name(c.name): c.name for c in found}
    llm_by_key = {rule_extract.normalise_name(n): n for n in snapshot_names if n and n.strip()}
    matched = [llm_by_key[k] for k in llm_by_key if k in rule_by_key]
    llm_only = [llm_by_key[k] for k in llm_by_key if k not in rule_by_key]
    rules_only = [rule_by_key[k] for k in rule_by_key if k not in llm_by_key]
    return ParityReport(matched=matched, llm_only=llm_only, rules_only=rules_only)


def _print_parity(report: ParityReport) -> None:
    print(f"matched: {len(report.matched)}")
    print(f"LLM-only: {len(report.llm_only)}" + (" (blocks enabling)" if report.llm_only else ""))
    for name in report.llm_only:
        print(f"  - {name}")
    print(f"rules-only: {len(report.rules_only)}" + (" (flagged for the owner, not blocking)" if report.rules_only else ""))
    for name in report.rules_only:
        print(f"  - {name}")
    print("PASS" if report.passed else "FAIL")


def _load_snapshot(path: Path) -> list[str] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    return [i.get("name") for i in items if isinstance(i, dict) and isinstance(i.get("name"), str)]


def _load_rules(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _parity_for(rules: DeterministicRules, fixture: str | None, snapshot: str | None) -> ParityReport | None:
    """The parity report, or `None` (after saying why) when the fixture or the snapshot is missing."""
    if not fixture or not snapshot:
        print("refused: enabling a rule set needs --fixture and --snapshot (the parity check)")
        return None
    names = _load_snapshot(Path(snapshot))
    if names is None:
        print(f"refused: the LLM snapshot {snapshot} is missing or unreadable; record it first (record-snapshot)")
        return None
    try:
        html = Path(fixture).read_text(encoding="utf-8")
    except OSError:
        print(f"refused: the fixture {fixture} is missing or unreadable")
        return None
    return parity_report(rules, html, names, source_url="https://fixture.invalid/")


def _find_operator(db: Session, email: str) -> User | None:
    user = db.scalars(select(User).where(func.lower(User.email) == email.strip().lower())).one_or_none()
    return user if user is not None and user.is_operator else None


def _cmd_set(args: argparse.Namespace) -> int:
    from app.data.repositories.db import SessionLocal

    if args.interval_minutes is None and args.rules_file is None:
        print("nothing to set: give --interval-minutes and/or --rules-file")
        return 1
    with SessionLocal() as db:
        operator = _find_operator(db, args.operator_email)
        if operator is None:
            print("refused: --operator-email must belong to an existing operator; nothing was changed")
            return 1
        source = source_repo.get_source_by_domain(db, args.domain)
        if source is None:
            print(f"no source with domain {args.domain}")
            return 1
        rules = dict(source.extraction_rules or {})
        try:
            if args.interval_minutes is not None:
                rules["check_interval_minutes"] = args.interval_minutes
            parsed = None
            if args.rules_file is not None:
                rules["deterministic"] = _load_rules(args.rules_file)
                parsed = DeterministicRules.model_validate(rules["deterministic"])
            validate_rule_keys(rules)
        except (ValidationError, ValueError, OSError) as exc:
            print(f"refused: invalid input ({type(exc).__name__}); nothing was changed")
            return 1
        if parsed is not None and parsed.enabled:
            report = _parity_for(parsed, args.fixture, args.snapshot)
            if report is None:
                return 1
            _print_parity(report)
            if not report.passed:
                print("refused: the parity check failed, so the rule set is not enabled")
                return 1
        try:
            source.extraction_rules = rules  # the only column written
            audit_repo.record(db, operator.id, "source.rules.set", f"source:{source.id}")
            db.commit()  # the change and its audit row land together
        except Exception as exc:  # noqa: BLE001 - nothing is half-written
            db.rollback()
            logger.warning("source rules set failed (%s)", type(exc).__name__)
            print("failed: nothing was changed")
            return 1
        logger.info("source rules set", extra={"source_id": str(source.id), "operator_id": str(operator.id)})
        print(f"updated {args.domain}")
        return 0


def _cmd_parity(args: argparse.Namespace) -> int:
    try:
        rules = DeterministicRules.model_validate(_load_rules(args.rules_file))
    except (ValidationError, ValueError, OSError) as exc:
        print(f"refused: invalid rules file ({type(exc).__name__})")
        return 1
    report = _parity_for(rules, args.fixture, args.snapshot)
    if report is None:
        return 1
    _print_parity(report)
    return 0 if report.passed else 1


def _cmd_diff(args: argparse.Namespace) -> int:
    """Read-only: rule output on the saved page against the scholarships already stored for the source."""
    from app.data.repositories.db import SessionLocal

    with SessionLocal() as db:
        source = source_repo.get_source_by_domain(db, args.domain)
        if source is None:
            print(f"no source with domain {args.domain}")
            return 1
        raw = (source.extraction_rules or {}).get("deterministic")
        if not raw:
            print("the source has no rule set")
            return 1
        rules = DeterministicRules.model_validate(raw)
        html = Path(args.fixture).read_text(encoding="utf-8")
        found = rule_extract.rule_extract(html, rules, source_url=f"https://{source.domain}/").output.candidates
        stored = scholarship_repo.list_for_source(db, source.id)
        rule_by_key = {rule_extract.normalise_name(c.name): c.name for c in found}
        stored_by_key = {rule_extract.normalise_name(s.name): s.name for s in stored}
        both = [rule_by_key[k] for k in rule_by_key if k in stored_by_key]
        rules_only = [rule_by_key[k] for k in rule_by_key if k not in stored_by_key]
        stored_only = [stored_by_key[k] for k in stored_by_key if k not in rule_by_key]
        db.rollback()  # nothing was written
    print(f"in both: {len(both)}")
    print(f"rules-only: {len(rules_only)}")
    for name in rules_only:
        print(f"  - {name}")
    print(f"stored-only: {len(stored_only)}")
    for name in stored_only:
        print(f"  - {name}")
    return 0


def _install_gate() -> None:
    from app.core.config import settings
    from app.core.llm_gate import install_gate
    from app.services.llm_budget import BudgetConfig, DbLlmGate

    install_gate(DbLlmGate(BudgetConfig.from_settings(settings)))


def _cmd_record_snapshot(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("refused: record-snapshot spends one provider request; pass --confirm to run it")
        return 2
    fixture = Path(args.fixture)
    html = fixture.read_text(encoding="utf-8")
    output = Path(args.output) if args.output else fixture.parent / "llm_snapshot.json"
    _install_gate()
    result = run_as_backfill(extract_listing_from_page, html, source_url=f"https://{args.domain}/")
    snapshot = {
        "fetched": datetime.now(timezone.utc).date().isoformat(),
        "source_domain": args.domain,
        "fixture": fixture.name,
        "items": [{"name": c.name} for c in result.candidates],
    }
    output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"recorded {len(snapshot['items'])} item(s) to {output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set and check deterministic extraction rules and check intervals.")
    commands = parser.add_subparsers(dest="command", required=True)

    set_cmd = commands.add_parser("set", help="set the check interval and/or the rule set of one source (audited)")
    set_cmd.add_argument("--domain", required=True)
    set_cmd.add_argument("--operator-email", required=True, help="an existing operator; recorded in the audit log")
    set_cmd.add_argument("--interval-minutes", type=int)
    set_cmd.add_argument("--rules-file", help="JSON file holding the `deterministic` rule set")
    set_cmd.add_argument("--fixture", help="saved listing page (needed to enable a rule set)")
    set_cmd.add_argument("--snapshot", help="recorded LLM snapshot (needed to enable a rule set)")
    set_cmd.set_defaults(run=_cmd_set)

    parity_cmd = commands.add_parser("parity", help="compare the rules with the recorded LLM snapshot (read-only)")
    parity_cmd.add_argument("--rules-file", required=True)
    parity_cmd.add_argument("--fixture", required=True)
    parity_cmd.add_argument("--snapshot", required=True)
    parity_cmd.set_defaults(run=_cmd_parity)

    diff_cmd = commands.add_parser("diff", help="compare rule output with the stored scholarships (read-only)")
    diff_cmd.add_argument("--domain", required=True)
    diff_cmd.add_argument("--fixture", required=True)
    diff_cmd.set_defaults(run=_cmd_diff)

    snap_cmd = commands.add_parser("record-snapshot", help="ONE provider request: record the LLM extraction of a saved page")
    snap_cmd.add_argument("--domain", required=True)
    snap_cmd.add_argument("--fixture", required=True)
    snap_cmd.add_argument("--output")
    snap_cmd.add_argument("--confirm", action="store_true")
    snap_cmd.set_defaults(run=_cmd_record_snapshot)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
