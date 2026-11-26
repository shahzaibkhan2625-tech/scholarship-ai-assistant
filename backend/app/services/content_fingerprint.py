"""Content fingerprint of a monitored page (003, research R-06) and the noise-window helpers (FR-SKIP-7).

The fingerprint is the SHA-256 of the page's visible text as `clean_html_to_text` normalises it
(scripts, styles and tags stripped, whitespace collapsed), never of the raw bytes: generated element
ids and script hashes change on every request, which would report "changed" on every check. Known
limit: a link-only change (same names, new `href`) is not seen; the forced full re-check bounds how
long that can go unnoticed. Pure functions, no I/O.
"""

import hashlib

from app.tools.extract_listing import clean_html_to_text

__all__ = ["fingerprint_html", "is_noisy", "update_noise_window"]


def fingerprint_html(html: str) -> str:
    return hashlib.sha256(clean_html_to_text(html or "").encode("utf-8")).hexdigest()


def update_noise_window(checks: int, changed: int, did_change: bool, *, window: int) -> tuple[int, int]:
    """Count one more check (and one more change if `did_change`). Once the window is exceeded the
    counts slide proportionally, so the pair always describes about the last `window` checks."""
    checks += 1
    changed += 1 if did_change else 0
    if checks > window:
        changed = int(changed * window / checks)  # floor, so a quiet source always decays to zero
        checks = window
    return checks, changed


def is_noisy(checks: int, changed: int, *, window: int, threshold: float) -> bool:
    """A source is noisy when its fingerprint changed at (almost) every check of a full window."""
    return checks >= window and changed / checks >= threshold
