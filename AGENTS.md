# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Python lives in `.venv` (`.venv/bin/python`); the README lists the reconciliation pipeline order.
- Tariff database (`data/tariffdb/`): generated CSVs, never hand-edited; the only hand-written inputs are
  `data/tariffdb/curated/*.yaml` (TOU windows, eligibility, block bounds), each fact with a verbatim `quote` at its
  `locator`, and `data/tariffdb/code_alias.csv` (AER code spellings → the distributor's codes). Recipes, every check and the pre-commit list: `docs/update-and-validate.md`.
- Schema changes go in `scripts/tariffdb/spec.py`; then rebuild (`scripts/tariffdb/build.py`) and regenerate
  `docs/schema.md` + `docs/schema-erd.svg` (`scripts/tariffdb/schema_doc.py`). `tests/test_tariffdb.py` fails on stale
  files.
- Rates are total network prices per tariff code (no DUoS/TUoS breakdown): AER = provisional, the distributor's own
  published list = final, replacing per code. Registries (`FINAL_DOCUMENT`, `EFFECTIVE_FROM`,
  `AER_CONSOLIDATED_FILES`) live in `scripts/tariffdb/build_support.py`. The pre-simplification 27-table schema is in
  git history at 11dcd5c.
- Years before 2023-24 come from `sources/archive/` (committed in full; change rows only through
  `scripts/archive_sources.py`, which locks) via `scripts/history/*.py` (`CONTRACT.md`; `check.py <slug> --sources`).
  Pricing years are financial, except Victoria (calendar 2001-2020, `2000-H2`, `2021-H1`) and Tasmania (to `2008-H1`);
  a state regulator's published schedule is final like the distributor's own list. Only years in effect on or after
  `build_support.FIRST_STORED_DAY` (2017-01-01, the captain's cutoff) are stored; `check.py` still checks every year.
- Billing rules (TOU window sets, `rate_condition`, metering, `charge_rule` demand measurement) are curated YAML; price
  lists rarely state them: the tariff structure statement and the network price/tariff guide do
  (`docs/update-and-validate.md` > "Billing rules"); a fact may list several `fin_year`s when one such document states
  it for each. `scripts/billcalc.py` bills interval data on the tables;
  `tests/test_billcalc.py` holds the published example bills and a sweep in which no tariff-period's status may
  worsen and the blocked count may only fall (`billcalc.py sweep --write` after curation improves them).
- `validate.py --sources` re-reads every value and quote (minutes); CI runs it with `--committed-only`
  (`TARIFFDB_SOURCES=committed`).
- Explanations of AER-vs-distributor differences live only in `scripts/adjustments.py`; spreadsheet numbers are read
  via `scripts/published.py` (Excel display rounding, never `round()` or f-format on the float). Every AER-authored
  file under `sources/aer/` is committed (the AER takes superseded versions private).

## Updating from new releases

The recurring job (an issue labelled `source-release`, opened monthly by `.github/workflows/release-check.yml`, or
"check for new tariffs"). Steps, commands and PR evidence: `docs/update-and-validate.md` > "Find and load new releases".

1. Find: `.venv/bin/python scripts/check_releases.py` (exit 2 = something new). Bot-blocked pages: real browser.
2. Archive first, same day: new AER files are lost once superseded (`--archive`, then commit).
3. Register each file in `sources/inventory.csv`, follow the matching recipe, `./run.sh`, rebuild.
4. AER v1 loads as `provisional`; the distributor's list replaces it per code as `final` (the build does this). TOU
   windows and eligibility wait for the distributor's documents; never infer them from the AER file.
5. Every "Before you commit" box, then `check_releases.py --no-files --accept`.
6. One PR per release with the evidence table; never merge it yourself. Unsure about a value: mark it `[UNSURE]` in
   the PR and leave it out of the data.
7. After it merges: `scripts/release.py --publish` cuts the downloadable dataset release (SQLite + CSV zip, never
   committed) from `origin/main`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
