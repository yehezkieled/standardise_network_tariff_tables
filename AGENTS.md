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
- `validate.py --sources` re-reads every value and quote (minutes); CI runs it with `--committed-only`
  (`TARIFFDB_SOURCES=committed`), since only AER files and Wayback copies are committed.
- Explanations of AER-vs-distributor differences live only in `scripts/adjustments.py`; spreadsheet numbers are read
  via `scripts/published.py` (Excel display rounding, never `round()` or f-format on the float). Every AER-authored
  file under `sources/aer/` is committed (the AER takes superseded versions private).

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
