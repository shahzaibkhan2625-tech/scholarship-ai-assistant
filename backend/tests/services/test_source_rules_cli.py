"""003 T312 (FR-RULES-5, plan decision 7): the operator commands `set`, `parity`, `diff` and `record-snapshot`.

`set` changes only `check_interval_minutes` and `deterministic` in `extraction_rules` (status, listing URL and every
other field stay byte-identical); it refuses to enable a rule set unless the parity check passes; `parity` reports
`matched`, `LLM-only` (blocks enabling) and `rules-only` (flagged, not blocking); `diff` is read-only;
`record-snapshot` is the only command that calls the provider, under the `backfill` class and only with
`--confirm`. Tests start at the CLI `main`. Real (rolled-back) DB; the provider is a fake that fails if called
by anything except `record-snapshot`."""

import json
import uuid
from pathlib import Path

import pytest

from app.core import llm_gate
from app.core.llm_gate import Priority, current_priority
from app.data.repositories import scholarship_repo, source_repo
from app.models.scholarship import Scholarship
from app.models.source import ScholarshipSource, SourceStatus
from app.models.user import User
from app.schemas.source import SourceRegistryCreate
from app.services import source_rules
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hec"
FIXTURE = FIXTURES / "listing.html"
RULES = {
    "version": 1,
    "enabled": True,
    "item_selector": "ul.card-info-container li.card",
    "name": {"attribute": "data-position"},
    "detail_url": {"selector": "a.read-more-arrow", "attribute": "href"},
    "provider_from_registry": True,
    "min_items": 20,
}


class _ExplodingGate:
    """Installed for every test: any provider request outside `record-snapshot` fails the test."""

    def acquire(self, request_type, priority):
        raise AssertionError("the provider must not be called")

    def report_provider_error(self, request_type, error):
        raise AssertionError("the provider must not be called")


@pytest.fixture(autouse=True)
def no_provider():
    llm_gate.install_gate(_ExplodingGate())
    yield
    llm_gate.install_gate(None)


@pytest.fixture
def db(db_session_factory, monkeypatch):
    import app.data.repositories.db as db_mod

    session = db_session_factory()
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)  # `main` closes its session; the test keeps using it
    yield session


@pytest.fixture
def operator(db):
    user = User(email=f"op-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", is_operator=True)
    db.add(user)
    db.commit()
    return user


def _source(db, *, status="active", **rules):
    source = source_repo.upsert_source_by_domain(
        db,
        SourceRegistryCreate(
            name="Rules CLI", organization="Higher Education Commission", source_type="gov",
            official_status="official", domain=f"cli-{uuid.uuid4().hex[:10]}.example.com", access_method="web",
            discovery_role=True, reliability_level="high", status=status, update_frequency="weekly",
            listing_page_url=None, extraction_rules={"list_page_url": "https://old.example.com/x", "robots_checked": True, **rules},
        ),
    )
    db.commit()
    return source


def _snapshot(tmp_path, names, name="llm_snapshot.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"fetched": "2026-11-27", "items": [{"name": n} for n in names]}), encoding="utf-8")
    return path


def _rule_names():
    from app.schemas.extraction_rules import DeterministicRules
    from app.tools import rule_extract

    html = FIXTURE.read_text(encoding="utf-8")
    result = rule_extract.rule_extract(html, DeterministicRules.model_validate(RULES), source_url="https://x.example.com/")
    return [c.name for c in result.output.candidates]


def _rules_file(tmp_path, rules=RULES) -> Path:
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    return path


def _set(source, operator, tmp_path, *extra):
    return source_rules.main(
        ["set", "--domain", source.domain, "--operator-email", operator.email, *extra]
    )


def _reload(db, source):
    db.expire_all()
    return source_repo.get_source_any_status(db, source.id)


# -- parity -----------------------------------------------------------------------------------------------------------


def test_parity_passes_when_the_rules_find_everything_the_llm_found(db, tmp_path, capsys):
    names = _rule_names()
    snapshot = _snapshot(tmp_path, names[:30])  # the LLM found fewer (the real page: 32 vs 38)
    code = source_rules.main(
        ["parity", "--rules-file", str(_rules_file(tmp_path)), "--fixture", str(FIXTURE), "--snapshot", str(snapshot)]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "matched: 30" in out and "LLM-only: 0" in out and f"rules-only: {len(names) - 30}" in out
    assert "PASS" in out


def test_parity_fails_on_any_llm_only_item_and_lists_it(db, tmp_path, capsys):
    snapshot = _snapshot(tmp_path, _rule_names()[:5] + ["Scholarship The Rules Cannot See"])
    code = source_rules.main(
        ["parity", "--rules-file", str(_rules_file(tmp_path)), "--fixture", str(FIXTURE), "--snapshot", str(snapshot)]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "LLM-only: 1" in out and "Scholarship The Rules Cannot See" in out and "FAIL" in out


def test_parity_compares_by_normalised_name(db, tmp_path, capsys):
    names = _rule_names()
    shouting = [names[0].upper(), "  " + names[1].replace(" ", "   ") + " "]
    snapshot = _snapshot(tmp_path, shouting)
    code = source_rules.main(
        ["parity", "--rules-file", str(_rules_file(tmp_path)), "--fixture", str(FIXTURE), "--snapshot", str(snapshot)]
    )
    assert code == 0 and "matched: 2" in capsys.readouterr().out


def test_parity_with_a_missing_snapshot_is_refused_not_passed(db, tmp_path, capsys):
    code = source_rules.main(
        ["parity", "--rules-file", str(_rules_file(tmp_path)), "--fixture", str(FIXTURE),
         "--snapshot", str(tmp_path / "nope.json")]
    )
    assert code == 1 and "snapshot" in capsys.readouterr().out.lower()


# -- set ---------------------------------------------------------------------------------------------------------------


def test_set_interval_changes_only_that_key(db, operator, tmp_path):
    source = _source(db, listing_complete=True)
    before = _reload(db, source)
    code = _set(source, operator, tmp_path, "--interval-minutes", "180")
    after = _reload(db, source)
    assert code == 0
    assert after.extraction_rules == {**before.extraction_rules, "check_interval_minutes": 180}
    assert (after.status, after.listing_page_url, after.update_frequency, after.freshness_window_days) == (
        before.status, before.listing_page_url, before.update_frequency, before.freshness_window_days,
    )


def test_set_rules_requires_a_passing_parity_check_to_enable(db, operator, tmp_path, capsys):
    source = _source(db)
    rules = _rules_file(tmp_path)
    bad = _snapshot(tmp_path, ["Scholarship The Rules Cannot See"], "bad.json")
    refused = _set(source, operator, tmp_path, "--rules-file", str(rules), "--fixture", str(FIXTURE), "--snapshot", str(bad))
    assert refused == 1
    assert "deterministic" not in _reload(db, source).extraction_rules  # nothing was written
    # no snapshot at all is also a refusal to enable
    assert _set(source, operator, tmp_path, "--rules-file", str(rules)) == 1
    good = _snapshot(tmp_path, _rule_names()[:10], "good.json")
    ok = _set(source, operator, tmp_path, "--rules-file", str(rules), "--fixture", str(FIXTURE), "--snapshot", str(good))
    assert ok == 0
    assert _reload(db, source).extraction_rules["deterministic"] == RULES


def test_a_rule_set_saved_disabled_needs_no_parity(db, operator, tmp_path):
    source = _source(db)
    rules = _rules_file(tmp_path, {**RULES, "enabled": False})
    assert _set(source, operator, tmp_path, "--rules-file", str(rules)) == 0
    assert _reload(db, source).extraction_rules["deterministic"]["enabled"] is False


def test_set_validates_the_rule_schema(db, operator, tmp_path):
    source = _source(db)
    rules = _rules_file(tmp_path, {**RULES, "enabled": False, "exclude_categories": ["Support Programs"]})
    assert _set(source, operator, tmp_path, "--rules-file", str(rules)) == 1
    assert _set(source, operator, tmp_path, "--interval-minutes", "5") == 1  # below the 15-minute minimum
    assert "check_interval_minutes" not in _reload(db, source).extraction_rules


def test_set_leaves_a_failing_source_failing_and_every_other_field_alone(db, operator, tmp_path):
    source = _source(db, status="failing")
    before = _reload(db, source)
    assert _set(source, operator, tmp_path, "--interval-minutes", "60") == 0
    after = _reload(db, source)
    assert after.status == SourceStatus.FAILING  # EACEA stays failing
    assert after.extraction_rules["list_page_url"] == "https://old.example.com/x"
    assert after.extraction_rules["robots_checked"] is True
    for column in ("name", "organization", "country", "domain", "reliability_level", "notes", "constraints"):
        assert getattr(after, column) == getattr(before, column)


def test_set_for_an_unknown_domain_changes_nothing(db, operator, tmp_path):
    assert source_rules.main(
        ["set", "--domain", "nope.example.com", "--operator-email", operator.email, "--interval-minutes", "60"]
    ) == 1


# -- diff ----------------------------------------------------------------------------------------------------------------


def test_diff_compares_rule_output_with_the_stored_scholarships_and_writes_nothing(db, tmp_path, capsys):
    source = _source(db, deterministic={**RULES})
    names = _rule_names()
    for name in names[:3]:
        record = scholarship_repo.create(db, Scholarship(name=name, official_scholarship_url="https://x.example.com"))
        db.add(ScholarshipSource(scholarship_id=record.id, source_id=source.id, url="https://x.example.com"))
    db.add(Scholarship(name="Stored Elsewhere", official_scholarship_url="https://x.example.com"))  # not linked to this source: never listed
    db.commit()
    stored_before = len(scholarship_repo.list_for_source(db, source.id))
    code = source_rules.main(["diff", "--domain", source.domain, "--fixture", str(FIXTURE)])
    out = capsys.readouterr().out
    assert code == 0
    assert "rules-only" in out and "stored-only" in out and "in both" in out
    assert len(scholarship_repo.list_for_source(db, source.id)) == stored_before


# -- record-snapshot ---------------------------------------------------------------------------------------------------


@pytest.fixture
def one_extraction(monkeypatch):
    seen = {"calls": 0, "priority": None, "gate_installed": False}

    def fake_extract(html, *, source_url):
        seen["calls"] += 1
        seen["priority"] = current_priority()
        return ExtractListingOutput(
            candidates=[ExtractedListingCandidate(name="Alpha Scholarship"), ExtractedListingCandidate(name="Beta Grant")],
            accepted_count=2, rejected_count=0,
        )

    monkeypatch.setattr(source_rules, "extract_listing_from_page", fake_extract)
    monkeypatch.setattr(source_rules, "_install_gate", lambda: seen.__setitem__("gate_installed", True))
    return seen


def test_record_snapshot_is_refused_without_confirm(db, tmp_path, one_extraction):
    out_path = tmp_path / "llm_snapshot.json"
    code = source_rules.main(
        ["record-snapshot", "--domain", "www.hec.gov.pk", "--fixture", str(FIXTURE), "--output", str(out_path)]
    )
    assert code == 2 or code == 1
    assert one_extraction["calls"] == 0 and not out_path.exists()


def test_record_snapshot_makes_exactly_one_request_under_the_backfill_class(db, tmp_path, one_extraction):
    out_path = tmp_path / "llm_snapshot.json"
    code = source_rules.main(
        ["record-snapshot", "--domain", "www.hec.gov.pk", "--fixture", str(FIXTURE), "--output", str(out_path),
         "--confirm"]
    )
    assert code == 0
    assert one_extraction["calls"] == 1
    assert one_extraction["priority"] is Priority.BACKFILL
    assert one_extraction["gate_installed"] is True  # the command installs the gate itself
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert [i["name"] for i in data["items"]] == ["Alpha Scholarship", "Beta Grant"]
    assert "fetched" in data


# -- the committed snapshot (owner step T319) --------------------------------------------------------------------------

SNAPSHOT = FIXTURES / "llm_snapshot.json"


@pytest.mark.skipif(
    not SNAPSHOT.exists(),
    reason="llm_snapshot.json is recorded by the owner (T319, one provider request); parity is pending",
)
def test_the_committed_hec_rules_pass_parity_against_the_committed_snapshot(db, capsys):
    from app.services.source_registry_seed import load_seed_rows

    hec = next(row for row in load_seed_rows() if row.domain == "www.hec.gov.pk")
    rules = {**hec.extraction_rules["deterministic"], "enabled": True}
    path = Path(__file__).parent / "_hec_rules_tmp.json"
    try:
        path.write_text(json.dumps(rules), encoding="utf-8")
        code = source_rules.main(["parity", "--rules-file", str(path), "--fixture", str(FIXTURE), "--snapshot", str(SNAPSHOT)])
    finally:
        path.unlink(missing_ok=True)
    assert code == 0, capsys.readouterr().out
