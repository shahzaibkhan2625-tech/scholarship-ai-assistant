"""`extract_listing` tool tests (T135). Fixture HTML snippets below are
reconstructed from the real markup shapes observed while live-fetching the
5 seeded sources during T135's Phase A design pass (a static server-rendered
card grid for Erasmus Mundus/EACEA, a SharePoint card-list for HEC Pakistan,
and a near-empty JS/consent-gated shell for KAUST) — not byte-for-byte saved
captures, per this project's existing "no external test-data dependency"
convention (see `test_pdf_parse.py`'s module docstring).

The LLM boundary mocked here is `_llm_extract_listing` (mirrors
`test_classify_tool.py` patching `_llm_fallback_classify` directly rather
than the deeper `generate_text` call)."""

from unittest.mock import patch

from app.tools.extract_listing import clean_html_to_text, extract_listing_from_page

_EACEA_LIKE_HTML = """
<html><body>
<ul class="ecl-listing">
  <li class="ecl-card">
    <div class="ecl-card__body">
      <div class="ecl-content-block ecl-card__content-block">
        Erasmus Mundus Joint Master in Multilingualism and Cultural Diversity
        <a href="https://erasmus-plus.ec.europa.eu/projects/search/details/101241136">Project overview</a>
      </div>
    </div>
  </li>
  <li class="ecl-card">
    <div class="ecl-card__body">
      <div class="ecl-content-block ecl-card__content-block">
        Law and Gender, Intersectionality and Diversity
        <a href="https://erasmus-plus.ec.europa.eu/projects/search/details/101240350">Project overview</a>
      </div>
    </div>
  </li>
</ul>
</body></html>
"""

_HEC_LIKE_HTML = """
<html><body>
<ul id="myUL" class="card-info-container list-view-display">
  <li class="card card-info-listing" data-position="Faculty Development Programme for Pakistani Universities">
    <div class="desc">Faculty Development Programme for Pakistani Universities</div>
    <div class="card-listing-footer">
      <ul class="card-actions"><li>National</li></ul>
      <a class="read-more-arrow" href="/english/scholarshipsgrants/Pages/FDP.aspx"></a>
    </div>
  </li>
  <li class="card card-info-listing" data-position="Allama Muhammad Iqbal Scholarships for Afghan Students">
    <div class="desc">Allama Muhammad Iqbal Scholarships for Afghan Students</div>
    <div class="card-listing-footer">
      <ul class="card-actions"><li>Scholarship for Foreigners</li></ul>
      <a class="read-more-arrow" href="/english/scholarshipsgrants/Pages/Iqbal.aspx"></a>
    </div>
  </li>
</ul>
</body></html>
"""

# Long enough to pass the min-viable-text-length gate, but — like the real
# KAUST fetch — contains cookie-consent/nav chrome and no actual scholarship
# names, so it must reach the LLM rather than being pre-filtered out.
_THIN_SHELL_HTML = "<html><body><nav>" + (
    "Study Programs Divisions Admissions Cookie Notice Accept all cookies Privacy Notice "
) * 20 + "</nav></body></html>"


def test_clean_html_to_text_strips_scripts_styles_and_tags():
    html = "<html><head><style>.x{color:red}</style><script>alert(1)</script></head>" "<body><p>Hello   world</p></body></html>"
    assert clean_html_to_text(html) == "Hello world"


def test_blank_content_skips_llm_call_entirely():
    with patch("app.tools.extract_listing._llm_extract_listing") as mock_llm:
        result = extract_listing_from_page("", source_url="https://example.invalid/list")

    mock_llm.assert_not_called()
    assert result.candidates == []
    assert result.accepted_count == 0
    assert result.rejected_count == 0


def test_grounded_candidates_from_real_shaped_listing_are_accepted():
    with patch(
        "app.tools.extract_listing._llm_extract_listing",
        return_value={
            "candidates": [
                {
                    "name": "Erasmus Mundus Joint Master in Multilingualism and Cultural Diversity",
                    "detail_url": "https://erasmus-plus.ec.europa.eu/projects/search/details/101241136",
                },
                {"name": "Law and Gender, Intersectionality and Diversity"},
            ]
        },
    ) as mock_llm:
        result = extract_listing_from_page(_EACEA_LIKE_HTML, source_url="https://www.eacea.ec.europa.eu/scholarships/emjmd-catalogue_en")

    mock_llm.assert_called_once()
    assert result.accepted_count == 2
    assert result.rejected_count == 0
    assert {c.name for c in result.candidates} == {
        "Erasmus Mundus Joint Master in Multilingualism and Cultural Diversity",
        "Law and Gender, Intersectionality and Diversity",
    }


def test_hallucinated_name_not_present_in_source_is_rejected_not_silently_dropped():
    with patch(
        "app.tools.extract_listing._llm_extract_listing",
        return_value={
            "candidates": [
                {"name": "Faculty Development Programme for Pakistani Universities"},  # real, grounded
                {"name": "Fully-Funded PhD Scholarship for International Students 2026"},  # fabricated, not in the text
            ]
        },
    ):
        result = extract_listing_from_page(_HEC_LIKE_HTML, source_url="https://www.hec.gov.pk/english/scholarshipsgrants/Pages/default.aspx")

    assert result.accepted_count == 1
    assert result.candidates[0].name == "Faculty Development Programme for Pakistani Universities"
    assert result.rejected_count == 1
    assert result.rejected_names == ["Fully-Funded PhD Scholarship for International Students 2026"]


def test_llm_correctly_returns_empty_list_for_a_thin_shell_page():
    """Mirrors the real KAUST finding (T135 Phase A): a long-enough page with
    no actual scholarship content must still reach the LLM (no pre-filter
    tries to guess this), and the LLM returning candidates=[] must produce
    accepted_count == 0 without raising — the caller (agent.py, Slice 2)
    is what turns that into a surfaced gap, not this tool."""
    with patch("app.tools.extract_listing._llm_extract_listing", return_value={"candidates": []}) as mock_llm:
        result = extract_listing_from_page(_THIN_SHELL_HTML, source_url="https://www.kaust.edu.sa/en/study/scholarships")

    mock_llm.assert_called_once()
    assert result.candidates == []
    assert result.accepted_count == 0
    assert result.rejected_count == 0


def test_malformed_candidate_shape_is_skipped_without_crashing_or_counting_as_rejected():
    with patch(
        "app.tools.extract_listing._llm_extract_listing",
        return_value={
            "candidates": [
                {"provider": "Some Provider"},  # missing required "name" -- invalid shape, not a rejected name
                {"name": "Faculty Development Programme for Pakistani Universities"},
            ]
        },
    ):
        result = extract_listing_from_page(_HEC_LIKE_HTML, source_url="https://www.hec.gov.pk/english/scholarshipsgrants/Pages/default.aspx")

    assert result.accepted_count == 1
    assert result.rejected_count == 0
    assert result.candidates[0].name == "Faculty Development Programme for Pakistani Universities"
