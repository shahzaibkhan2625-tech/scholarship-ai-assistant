"""Local-filesystem object-storage abstraction (T103, Blueprint plan.md;
data-model.md §6 `file_ref`). An S3-compatible backend can implement the same
`StorageBackend` Protocol later without callers changing (they only ever
depend on the Protocol via `get_storage()`, never on `LocalFileStorage`
directly).

A `file_ref` read back from `application_documents`/`generated_documents` is
untrusted input (it round-trips through the DB) — every `get`/`delete`/
`exists` call here resolves it against `STORAGE_ROOT` and raises
`InvalidFileRefError` rather than silently touching a path outside the
storage root.
"""

import uuid
from pathlib import Path
from typing import Protocol

from app.core.config import settings

__all__ = ["StorageBackend", "LocalFileStorage", "StorageError", "InvalidFileRefError", "get_storage"]


class StorageError(Exception):
    """Base class for storage-layer failures."""


class InvalidFileRefError(StorageError):
    """Raised when a file_ref is absolute, contains `..`, or otherwise
    resolves outside STORAGE_ROOT."""


class StorageBackend(Protocol):
    def save(self, user_id: uuid.UUID, filename: str, data: bytes) -> str: ...
    def get(self, file_ref: str) -> bytes: ...
    def delete(self, file_ref: str) -> None: ...
    def exists(self, file_ref: str) -> bool: ...


class LocalFileStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, user_id: uuid.UUID, filename: str, data: bytes) -> str:
        # Only the extension is ever taken from the caller-supplied filename —
        # `Path(...).suffix` operates on the final path component, so it can
        # never carry a directory component through even for a crafted name
        # like "../../evil.pdf".
        ext = Path(filename).suffix
        key = f"documents/{user_id}/{uuid.uuid4()}{ext}"
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, file_ref: str) -> bytes:
        return self._resolve(file_ref).read_bytes()

    def delete(self, file_ref: str) -> None:
        self._resolve(file_ref).unlink(missing_ok=True)

    def exists(self, file_ref: str) -> bool:
        return self._resolve(file_ref).is_file()

    def _resolve(self, file_ref: str) -> Path:
        ref_path = Path(file_ref)
        if ref_path.is_absolute() or ".." in ref_path.parts:
            raise InvalidFileRefError(f"invalid file_ref: {file_ref!r}")

        # Path.resolve() also follows symlinks, so a symlink planted inside
        # the root that points back out is still caught by the relative_to
        # check below, not just the upfront ".." rejection.
        candidate = (self._root / ref_path).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise InvalidFileRefError(f"file_ref escapes storage root: {file_ref!r}") from exc
        return candidate


def get_storage() -> StorageBackend:
    return LocalFileStorage(Path(settings.storage_root))
