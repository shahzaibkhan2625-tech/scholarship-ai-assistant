"""003 T277: the content fingerprint (FR-SKIP-1/7, research R-06) and the noise-window helpers.

The HEC fixtures follow the card markup measured on 2026-10-25 (research R-11): `a` and `b_noise`
differ only in generated element ids, script content and whitespace; `c_new_card` adds one card."""

import hashlib
from pathlib import Path

import pytest

from app.services import content_fingerprint as fp
from app.tools.extract_listing import clean_html_to_text

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hec"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_markup_noise_does_not_change_the_fingerprint():
    a, b = _read("listing_a.html"), _read("listing_b_noise.html")
    assert a != b  # the raw bytes do differ, as they do on the real page
    assert fp.fingerprint_html(a) == fp.fingerprint_html(b)


def test_adding_one_card_changes_the_fingerprint():
    assert fp.fingerprint_html(_read("listing_a.html")) != fp.fingerprint_html(_read("listing_c_new_card.html"))


def test_it_is_deterministic_and_a_sha256_hex_of_the_normalised_text():
    html = _read("listing_a.html")
    value = fp.fingerprint_html(html)
    assert value == fp.fingerprint_html(html)
    assert value == hashlib.sha256(clean_html_to_text(html).encode("utf-8")).hexdigest()
    assert len(value) == 64


def test_whitespace_only_differences_are_ignored_but_text_differences_are_not():
    assert fp.fingerprint_html("<p>Hello   world</p>") == fp.fingerprint_html("<p>Hello\n\nworld</p>")
    assert fp.fingerprint_html("<p>Hello world</p>") != fp.fingerprint_html("<p>Hello there</p>")


def test_empty_content_has_a_stable_fingerprint():
    assert fp.fingerprint_html("") == fp.fingerprint_html("   ")


@pytest.mark.parametrize("changed, checks, noisy", [(8, 10, True), (9, 10, True), (7, 10, False), (3, 10, False)])
def test_noise_is_flagged_at_eight_changes_in_ten_checks(changed, checks, noisy):
    assert fp.is_noisy(checks, changed, window=10, threshold=0.8) is noisy


def test_a_source_is_not_noisy_before_a_full_window():
    assert fp.is_noisy(4, 4, window=10, threshold=0.8) is False


def test_update_noise_window_counts_checks_and_changes_and_slides_at_the_window():
    checks, changed = 0, 0
    for _ in range(10):
        checks, changed = fp.update_noise_window(checks, changed, True, window=10)
    assert (checks, changed) == (10, 10)
    checks, changed = fp.update_noise_window(checks, changed, False, window=10)
    assert checks == 10 and 8 <= changed <= 10  # stays bounded by the window
    for _ in range(30):
        checks, changed = fp.update_noise_window(checks, changed, False, window=10)
    assert checks == 10 and changed == 0
    assert fp.is_noisy(checks, changed, window=10, threshold=0.8) is False
