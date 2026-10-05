# aer-tariff-recon

Reconciles the network tariff prices the Australian Energy Regulator (AER) publishes with each annual pricing
decision against the tariff price lists each electricity distributor publishes itself, for all 14 distributors
and financial years 2023-24 to 2026-27.

## Findings

- `REPORT.md` - the report: what the AER files are each year, which distributor documents were used, the method,
  the explained and the genuine differences, format changes, items marked `[UNSURE]`, recommendations, and a
  per-distributor-and-year detail section with the full source inventory.
- `discrepancies.csv` - every discrepancy (value differs, AER-only and distributor-only tariff codes and
  components) with its explanation class and the source URL on both sides.

## Reproduce

```
./run.sh              # needs uv (https://docs.astral.sh/uv/); creates .venv, fetches, parses, reconciles, writes out/ and refreshes REPORT.md + discrepancies.csv
./run.sh --no-fetch   # offline: skip the download step
```

`out/` (not committed) then also holds `discrepancies.xlsx` (full comparison, summary grid, format timeline and
source inventory as sheets), `recon_detail.csv` (every compared pair), `match_grid.csv`, `format_timeline.csv`,
`recon_summary.json`, `aer_long.csv` (AER side, normalised) and `dnsp/*.csv` (distributor side, normalised), plus `report.html` for visual review.

## Source documents

`sources/inventory.csv` lists every document the reconciliation reads: distributor, year, role, local path, the
exact URL it was retrieved from, an access note and the SHA-256 of the file that was used. Two kinds of rows:

- **Wayback Machine copies** (URL on `web.archive.org`): documents whose publisher blocks automated access
  (energex.com.au, ergon.com.au, powerwater.com.au) or no longer serves the file. These are committed under
  `sources/` because they cannot be re-fetched reliably.
- **Everything else** is not committed. `scripts/fetch_sources.py` (run by `./run.sh`) downloads each missing
  file from its recorded URL and verifies the checksum, so a changed checksum shows that the publisher replaced
  the document in place. `scripts/fetch_sources.py --check` only reports what is missing or differs.

Rows without a local path are documents that could not be obtained at all (the access note says why); the
report treats those distributor-years as "no distributor-side data".

## Layout

- `scripts/parse_aer.py` - reads the AER files into one long table (`out/aer_long.csv`).
- `scripts/dnsp/*.py` - one parser per distributor group, all emitting the `scripts/schema.py` columns
  (contract in `scripts/dnsp/CONTRACT.md`).
- `scripts/units.py` - unit normalisation (cents; fixed charges per day; demand per published period).
- `scripts/reconcile.py` - code and component matching, difference classification, grid and discrepancy outputs.
- `scripts/report_tables.py` and `scripts/write_report.py` - report tables and report assembly from
  `notes/report_head.md` and `notes/report_sections/*.md`.
- `notes/format_notes.json` - per distributor-year notes on document format changes.
