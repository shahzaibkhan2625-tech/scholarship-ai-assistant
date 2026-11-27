"""003 T476 (FR-RULES-6, D-1): `source_rules set` is audited.

`--operator-email` is required; an email that belongs to no user or to a non-operator is refused with a clear message
and nothing changes (no registry change, no audit row); a valid operator gives exactly one audit row, action
`source.rules.set`, `user_id` = that operator, entity `source:<uuid>`, written in the same transaction as the change.
Tests start at the CLI `main`. Real (rolled-back) DB; no provider."""

import json
import uuid

import pytest
from sqlalchemy import select

from app.data.repositories import audit_repo, source_repo
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.source import SourceRegistryCreate
from app.services import source_rules


@pytest.fixture
def db(db_session_factory, monkeypatch):
    import app.data.repositories.db as db_mod

    session = db_session_factory()
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)
    yield session


def _user(db, *, operator: bool) -> User:
    user = User(email=f"u-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", is_operator=operator)
    db.add(user)
    db.commit()
    return user


def _source(db):
    source = source_repo.upsert_source_by_domain(
        db,
        SourceRegistryCreate(
            name="Audit Rules", source_type="gov", official_status="official",
            domain=f"aud-{uuid.uuid4().hex[:10]}.example.com", access_method="web", discovery_role=True,
            reliability_level="high", status="active", extraction_rules={"list_page_url": "https://x.example.com/l"},
        ),
    )
    db.commit()
    return source


def _rows(db, source):
    return list(db.execute(select(AuditLog).where(AuditLog.entity == f"source:{source.id}")).scalars())


def _rules(db, source):
    db.expire_all()
    return dict(source_repo.get_source_any_status(db, source.id).extraction_rules)


def test_the_operator_email_is_required(db, capsys):
    source = _source(db)
    with pytest.raises(SystemExit) as exit_info:
        source_rules.main(["set", "--domain", source.domain, "--interval-minutes", "60"])
    assert exit_info.value.code == 2
    assert "check_interval_minutes" not in _rules(db, source) and _rows(db, source) == []


@pytest.mark.parametrize("who", ["nobody@example.com", "member"])
def test_an_unknown_or_non_operator_email_is_refused_and_nothing_changes(db, capsys, who):
    source = _source(db)
    email = _user(db, operator=False).email if who == "member" else who
    before = _rules(db, source)
    code = source_rules.main(
        ["set", "--domain", source.domain, "--operator-email", email, "--interval-minutes", "60"]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "operator" in out.lower()
    assert _rules(db, source) == before and _rows(db, source) == []


def test_a_valid_operator_gives_exactly_one_audit_row(db):
    source = _source(db)
    operator = _user(db, operator=True)
    code = source_rules.main(
        ["set", "--domain", source.domain, "--operator-email", operator.email.upper(), "--interval-minutes", "60"]
    )
    assert code == 0
    [row] = _rows(db, source)
    assert (row.action, row.user_id, row.entity) == ("source.rules.set", operator.id, f"source:{source.id}")
    assert _rules(db, source)["check_interval_minutes"] == 60


def test_the_change_and_its_audit_row_commit_together(db, monkeypatch):
    source = _source(db)
    operator = _user(db, operator=True)
    before = _rules(db, source)

    def boom(*a, **k):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(audit_repo, "record", boom)
    code = source_rules.main(
        ["set", "--domain", source.domain, "--operator-email", operator.email, "--interval-minutes", "60"]
    )
    assert code == 1
    assert _rules(db, source) == before  # no audit row, no change


def test_a_refused_set_writes_no_audit_row(db, tmp_path):
    source = _source(db)
    operator = _user(db, operator=True)
    rules = tmp_path / "r.json"
    rules.write_text(json.dumps({"version": 9}), encoding="utf-8")
    code = source_rules.main(
        ["set", "--domain", source.domain, "--operator-email", operator.email, "--rules-file", str(rules)]
    )
    assert code == 1 and _rows(db, source) == []


def test_the_audit_action_set_gains_exactly_the_one_action_and_stays_closed(db):
    assert "source.rules.set" in audit_repo.AUDIT_ACTIONS
    operator = _user(db, operator=True)
    audit_repo.record(db, operator.id, "source.rules.set", f"source:{uuid.uuid4()}")  # accepted
    with pytest.raises(ValueError):
        audit_repo.record(db, operator.id, "source.rules.delete", "source:x")
