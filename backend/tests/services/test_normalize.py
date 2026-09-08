"""`normalize` service tests (T079, Blueprint §31, §14): a field present in
the raw record is `known`; a field absent/empty is always `unknown` — never
a guessed default."""

from app.services.normalize import normalize_record


def test_present_field_gets_known_status():
    raw = {"name": "DAAD Scholarship", "country": "Germany"}

    fields = {nf.key: nf for nf in normalize_record(raw)}

    assert fields["name"].value == "DAAD Scholarship"
    assert fields["name"].value_status == "known"
    assert fields["name"].confidence == "inferred"


def test_absent_field_gets_unknown_status_never_guessed():
    raw = {"name": "DAAD Scholarship", "deadline": None, "field": ""}

    fields = {nf.key: nf for nf in normalize_record(raw)}

    assert fields["deadline"].value is None
    assert fields["deadline"].value_status == "unknown"
    assert fields["deadline"].confidence == "unknown"
    assert fields["field"].value is None
    assert fields["field"].value_status == "unknown"


def test_explicit_status_and_confidence_sidecars_are_honored():
    raw = {
        "gpa": 3.5,
        "gpa_status": "conditional",
        "gpa_confidence": "verified",
    }

    fields = {nf.key: nf for nf in normalize_record(raw)}

    assert fields["gpa"].value == 3.5
    assert fields["gpa"].value_status == "conditional"
    assert fields["gpa"].confidence == "verified"


def test_keys_param_restricts_which_fields_are_normalized():
    raw = {"name": "X", "provider": "Y", "internal_note": "should be skipped"}

    fields = {nf.key: nf for nf in normalize_record(raw, keys=["name", "provider"])}

    assert set(fields.keys()) == {"name", "provider"}
