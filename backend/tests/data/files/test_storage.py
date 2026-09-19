"""`LocalFileStorage` tests (T103). Every test constructs its own instance
against a pytest `tmp_path` so no test touches the real configured
`STORAGE_ROOT` or another test's files."""

import uuid

import pytest

from app.data.files.storage import InvalidFileRefError, LocalFileStorage


@pytest.fixture
def storage(tmp_path):
    return LocalFileStorage(tmp_path / "storage_root")


def test_save_get_round_trip(storage):
    user_id = uuid.uuid4()
    data = b"%PDF-1.4 fake transcript bytes"

    key = storage.save(user_id, "transcript.pdf", data)

    assert key.startswith(f"documents/{user_id}/")
    assert key.endswith(".pdf")
    assert storage.get(key) == data


def test_save_returns_relative_key_not_absolute_path(storage):
    key = storage.save(uuid.uuid4(), "cv.pdf", b"data")

    assert not key.startswith("/")
    assert ":" not in key  # no drive letter either (Windows absolute path)


def test_exists_and_delete(storage):
    key = storage.save(uuid.uuid4(), "sop.pdf", b"data")

    assert storage.exists(key) is True

    storage.delete(key)

    assert storage.exists(key) is False


def test_exists_returns_false_for_missing_key(storage):
    assert storage.exists("documents/does-not-exist/missing.pdf") is False


def test_save_takes_only_the_extension_never_the_filename_or_directory(storage):
    user_id = uuid.uuid4()

    key = storage.save(user_id, "../../evil.pdf", b"payload")

    # The generated key must stay under documents/{user_id}/ with a
    # server-generated uuid name — the caller-supplied "../../evil" must
    # never appear in it, and the write must land inside the storage root.
    assert key.startswith(f"documents/{user_id}/")
    assert "evil" not in key
    assert ".." not in key
    assert storage.get(key) == b"payload"


def test_save_with_path_traversal_filename_does_not_escape_storage_root(storage, tmp_path):
    user_id = uuid.uuid4()

    storage.save(user_id, "../../../../evil.pdf", b"payload")

    root = tmp_path / "storage_root"
    written_files = list(root.rglob("*.pdf"))
    assert len(written_files) == 1
    assert written_files[0].resolve().is_relative_to(root.resolve())


def test_get_with_path_traversal_file_ref_raises(storage):
    with pytest.raises(InvalidFileRefError):
        storage.get("../../../../etc/passwd")


def test_get_with_absolute_file_ref_raises(storage, tmp_path):
    outside_file = tmp_path / "outside.pdf"
    outside_file.write_bytes(b"secret")

    with pytest.raises(InvalidFileRefError):
        storage.get(str(outside_file))


def test_delete_with_malicious_file_ref_raises(storage):
    with pytest.raises(InvalidFileRefError):
        storage.delete("../../../../etc/passwd")


def test_exists_with_malicious_file_ref_raises(storage):
    with pytest.raises(InvalidFileRefError):
        storage.exists("../../../../etc/passwd")
