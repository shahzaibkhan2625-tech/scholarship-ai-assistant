# Source Registry — Governance Record

Tracks *why* each seeded source is trusted as an authoritative `source_registry`
row, per Blueprint §29 (Source Registry & Governance) and PRD FR-VERIFY-1. New
sources otherwise enter only through the `candidate_sources` → human-approval
flow (see `adr/0001-source-registry-governance-and-seed-sources.md`) — this
document is the paper trail for the five sources that were seeded directly
instead, and should be updated whenever a source is added, approved, or
disabled.

## Seed set (backend/app/data/seeds/seed_sources.yaml)

### 1. DAAD Scholarship Database (`www2.daad.de`)

- **Why selected:** DAAD (Deutscher Akademischer Austauschdienst) is
  Germany's national academic exchange service and the canonical scholarship
  database for Germany — high scholarship volume, one of the five example
  countries the MVP targets (spec.md SC-002/SC-003 want ≥2 distinct source
  types; DAAD anchors "national education body" coverage for Europe).
- **`official_status: official` justification:** DAAD is a publicly funded
  joint organization of German higher-education institutions and the German
  federal government — it is the entity that administers these scholarships,
  not a third-party aggregator.
- **robots.txt / ToS status:** **UNVERIFIED** — `extraction_rules.robots_checked`
  is seeded `false`. A connector (WS2.2) must not fetch this domain until
  robots.txt/ToS have been reviewed and `robots_checked` flipped to `true`.

### 2. Erasmus Mundus Joint Masters Catalogue — EACEA (`www.eacea.ec.europa.eu`)

- **Why selected:** EACEA (European Education and Culture Executive Agency)
  publishes the official EU-wide catalogue of Erasmus Mundus Joint Master
  Degrees — covers pan-European, multi-country funded programs that no
  single national source lists.
- **`official_status: official` justification:** EACEA is a European
  Commission executive agency; the catalogue is the EU's own system of
  record for these scholarships, not a scraped mirror.
- **robots.txt / ToS status:** **UNVERIFIED** — same gate as above; not yet
  fetch-eligible.

### 3. Stipendium Hungaricum (`stipendiumhungaricum.hu`)

- **Why selected:** Stipendium Hungaricum is Hungary's flagship national
  scholarship programme, run by Tempus Public Foundation — adds a second,
  independently governed European national-scholarship source alongside
  DAAD, so a Europe-focused discovery query is never dependent on a single
  country's data.
- **`official_status: official` justification:** Tempus Public Foundation is
  a Hungarian government background institution that directly administers
  the programme on behalf of the Ministry of Foreign Affairs and Trade.
- **robots.txt / ToS status:** **UNVERIFIED**.

### 4. HEC Pakistan — Learning Opportunities Abroad (`www.hec.gov.pk`)

- **Why selected:** The Higher Education Commission of Pakistan is the
  government body responsible for publicizing overseas scholarship
  opportunities to Pakistani students — chosen to ground the product in the
  home market it was first built for, and to add a `gov`-type source
  distinct from the `national_education`/`international_org` sources above.
- **`official_status: official` justification:** HEC is a statutory,
  government-chartered regulatory body for higher education in Pakistan.
- **robots.txt / ToS status:** **UNVERIFIED**.

### 5. KAUST Admissions (`www.kaust.edu.sa`)

- **Why selected:** King Abdullah University of Science and Technology
  funds essentially all admitted graduate students — a `university`-type
  source (as opposed to `gov`/`national_education`/`international_org`),
  and the seed set's only Middle East / Asia-adjacent representative,
  satisfying the ≥2-source-type + multi-region discovery requirement.
- **`official_status: official` justification:** KAUST Admissions is the
  university's own admissions office publishing its own funding terms —
  first-party, not a third party describing KAUST's scholarships.
- **robots.txt / ToS status:** **UNVERIFIED**.

## What was deliberately NOT seeded

- **No aggregator.** None of the five seed rows use `source_type:
  approved_aggregator`. Third-party scholarship-listing aggregators
  (Scholars4Dev-style sites, etc.) were excluded from the initial seed
  because their own sourcing/freshness cannot be verified transitively —
  discovering scholarships *through* an aggregator would make this system's
  "official/verified" claim only as reliable as an aggregator this project
  has not audited. An aggregator can still be *proposed* later via the
  `candidate_sources` flow and promoted after review, same as any other
  source.
- **No live paid scholarship-listing API.** No production, freely-available
  scholarship-listing API meeting this project's cost/reliability bar could
  be identified. `api_connector` (WS2.2) is therefore contract-tested
  against a local fixture that mimics such an API's response shape, so the
  connector interface is proven correct without taking a dependency on a
  paid vendor before one is chosen and budgeted. See
  `adr/0001-source-registry-governance-and-seed-sources.md`.

## Updating this document

Whenever a `candidate_sources` row is approved into `source_registry`
(`POST /sources/candidates/{id}/approve`), or an existing source's
`official_status`/robots status changes, add or update its entry above in
the same PR.
