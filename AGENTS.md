# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Python lives in `.venv` (`.venv/bin/python`); the README lists the reconciliation pipeline order.
- Tariff database (`data/tariffdb/`) is generated, never hand-edited. The only hand-written inputs are
  `data/tariffdb/curated/*.yaml`, and every curated fact needs a short verbatim `quote` at its `locator`.
  Validate with `.venv/bin/python scripts/tariffdb/curated.py data/tariffdb/curated/<file>.yaml`, then rebuild
  with `scripts/tariffdb/build.py`.
- Schema changes go in `scripts/tariffdb/spec.py`. Then rebuild and regenerate `docs/tariffdb.md` with
  `scripts/tariffdb/docs.py`; `test_docs_are_current` fails otherwise.
- Source-fact tables are append-only across commits: `build.py --check-append-only <git ref>` rejects edits to
  existing rows (tables and columns marked `derived` in `spec.py` are recomputed and exempt). A correction is a new
  source document version, not an in-place change.
- An in-place correction of a committed fact row that fixes this repo's own transcription error (not a new document
  version) must be listed in `TRANSCRIPTION_FIXES` in `scripts/tariffdb/build.py` with its exact old and new value.
- Explanations of AER-vs-distributor differences live only in `scripts/adjustments.py` (shared by `reconcile.py` and
  the tariffdb build); spreadsheet numbers are read via `scripts/published.py` (Excel display rounding, never
  `round()` or f-format on the float). Every AER-authored file under `sources/aer/` is committed (the AER takes
  superseded versions private); register a new version in `build_support.AER_CONSOLIDATED_FILES`/`AER_VERSION_URLS`.
- `tests/test_tariffdb.py` re-reads every value and quote from `sources/` and takes about 5 minutes. Set
  `TARIFFDB_PG_BIN` (e.g. the bin dir of the `pgserver` pip wheel) to also run the real PostgreSQL load test.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
