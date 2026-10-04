"""T162 (US6): the seed loader maps each seed's `extraction_rules.list_page_url`
into the `listing_page_url` column (explicit YAML key wins; legacy key kept),
and the discovery_role decisions for the five seeds. Pure: reads the YAML via
`load_seed_rows`, no DB.

Deviation from tasks.md T162/T168 text (approved override): DAAD, Stipendium
Hungaricum and KAUST STAY `discovery_role: false` until a live check confirms
extractable content and the robots review is done.
"""

import textwrap

from app.services.source_registry_seed import load_seed_rows

_EXPECTED_LISTING_URLS = {
    "www2.daad.de": "https://www2.daad.de/deutschland/stipendium/datenbank/en/",
    "www.eacea.ec.europa.eu": "https://www.eacea.ec.europa.eu/scholarships/emjmd-catalogue_en",
    "stipendiumhungaricum.hu": "https://stipendiumhungaricum.hu/apply/",
    "www.hec.gov.pk": "https://www.hec.gov.pk/english/scholarshipsgrants/Pages/default.aspx",
    "www.kaust.edu.sa": "https://www.kaust.edu.sa/en/study/scholarships",
}
_DISCOVERY_TRUE = {"www.eacea.ec.europa.eu", "www.hec.gov.pk"}
_DISCOVERY_FALSE = {"www2.daad.de", "stipendiumhungaricum.hu", "www.kaust.edu.sa"}


def _by_domain():
    return {row.domain: row for row in load_seed_rows()}


def test_all_five_seeds_map_listing_page_url():
    rows = _by_domain()
    assert set(rows) == set(_EXPECTED_LISTING_URLS)
    for domain, url in _EXPECTED_LISTING_URLS.items():
        assert rows[domain].listing_page_url == url
        assert rows[domain].extraction_rules["list_page_url"] == url  # legacy key kept


def test_discovery_role_decisions():
    rows = _by_domain()
    for domain in _DISCOVERY_TRUE:
        assert rows[domain].discovery_role is True
    for domain in _DISCOVERY_FALSE:
        assert rows[domain].discovery_role is False


def test_kaust_domain_is_unchanged():
    assert "www.kaust.edu.sa" in _by_domain()


def test_t140_notes_rewritten_for_the_three_disabled_sources():
    rows = _by_domain()
    expected = (
        "T140 fixed; listing URL configured; discovery_role stays false until a live check "
        "confirms extractable content AND the robots review is done."
    )
    for domain in _DISCOVERY_FALSE:
        assert rows[domain].notes == expected
        assert "dead metadata" not in rows[domain].notes


def test_loader_fills_listing_page_url_from_legacy_key_when_yaml_has_none(tmp_path):
    seed = tmp_path / "seed.yaml"
    seed.write_text(
        textwrap.dedent(
            """
            - name: Legacy Only
              source_type: gov
              official_status: official
              domain: legacy.example.org
              access_method: web
              reliability_level: high
              extraction_rules:
                list_page_url: "https://legacy.example.org/list/"
            """
        ),
        encoding="utf-8",
    )
    [row] = load_seed_rows(seed)
    assert row.listing_page_url == "https://legacy.example.org/list/"
    assert row.extraction_rules["list_page_url"] == "https://legacy.example.org/list/"


def test_explicit_listing_page_url_key_wins_over_legacy_key(tmp_path):
    seed = tmp_path / "seed.yaml"
    seed.write_text(
        textwrap.dedent(
            """
            - name: Explicit Wins
              source_type: gov
              official_status: official
              domain: explicit.example.org
              access_method: web
              reliability_level: high
              listing_page_url: "https://explicit.example.org/column/"
              extraction_rules:
                list_page_url: "https://explicit.example.org/legacy/"
            """
        ),
        encoding="utf-8",
    )
    [row] = load_seed_rows(seed)
    assert row.listing_page_url == "https://explicit.example.org/column/"
    assert row.extraction_rules["list_page_url"] == "https://explicit.example.org/legacy/"


def test_explicit_null_listing_page_url_is_respected_not_overridden(tmp_path):
    """An explicit key (even null) wins, so an operator can clear the column."""
    seed = tmp_path / "seed.yaml"
    seed.write_text(
        textwrap.dedent(
            """
            - name: Explicit Null
              source_type: gov
              official_status: official
              domain: nullcase.example.org
              access_method: web
              reliability_level: high
              listing_page_url: null
              extraction_rules:
                list_page_url: "https://nullcase.example.org/legacy/"
            """
        ),
        encoding="utf-8",
    )
    [row] = load_seed_rows(seed)
    assert row.listing_page_url is None
