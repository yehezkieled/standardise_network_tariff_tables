# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Python lives in `.venv` (`.venv/bin/python`); the README lists the reconciliation pipeline order.
- Tariff database (`data/tariffdb/`) is generated, never hand-edited. The only hand-written inputs are
  `data/tariffdb/curated/*.yaml`, and every curated fact needs a short verbatim `quote` at its `locator`.
  Validate with `.venv/bin/python scripts/tariffdb/curated.py data/tariffdb/curated/<file>.yaml`, then rebuild
  with `scripts/tariffdb/build.py`.
- Schema changes go in `scripts/tariffdb/spec.py`. Then rebuild and regenerate `docs/tariffdb.md` with
  `scripts/tariffdb/docs.py`; `test_docs_are_current` fails otherwise.
- The data is append-only across commits: `build.py --check-append-only <git ref>` rejects edits to existing
  rows. A correction is a new source document version, not an in-place change.
- `tests/test_tariffdb.py` re-reads every value and quote from `sources/` and takes about 5 minutes. Set
  `TARIFFDB_PG_BIN` (e.g. the bin dir of the `pgserver` pip wheel) to also run the real PostgreSQL load test.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
