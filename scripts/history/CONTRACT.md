# Historical parser contract (pricing years before 2023-24)

Read the archived documents (`sources/archive/`, inventory `sources/archive/inventory.csv`, coverage
`sources/archive/coverage.csv`) into the parser rows the tariff database is built from (`scripts/schema.py` COLUMNS).
The build (`scripts/tariffdb/build.py`) stores, per tariff code and pricing year, the **total network price charged,
GST exclusive**; the schema is `docs/schema.md`.

## Output

- One script per group: `scripts/history/<slug>.py`, run from the repository root as
  `.venv/bin/python scripts/history/<slug>.py`; it writes `out/history/<slug>.csv` with `common.write(slug, rows)`.
- Build every row with `common.row(doc, code, component, value, unit, locator, ...)` (`doc = common.document(path)`):
  it fills distributor, pricing year (`fin_year` column), side, standard units and the source URL from the inventory.
- One function per document layout; detect tables by their printed labels, not fixed positions alone.

## Which documents to parse, per distributor and pricing year

| Case | Parse |
|---|---|
| A **final** document exists: the distributor's own price list (`side` DNSP, `price_status` published) or a state regulator's published/approved schedule (`side` REGULATOR_HOSTED, approved or published) | exactly one of them for 1 July (or 1 January for a calendar year): the version customers were billed on, normally the latest. Copies of the same list: pick one |
| A re-issue that changes prices part-way through the year | that one too, and record its first day: `.venv/bin/python scripts/archive_sources.py set <path> --effective-from YYYY-MM-DD --note "<quote, page>"` |
| No final document that year | one provisional document: the AER-hosted approved version, else the latest proposed one (never two of one side) |
| The inventory's status or kind is wrong (a published price list marked unverified, a proposal marked published) | fix it first: `archive_sources.py set <path> --price-status ... --note "<evidence: quote, page>"` |

Every document kind that can carry prices counts (price list, tariff schedule, price guide, tariff summary, pricing
proposal, annual tariff report, pricing model). Never edit `sources/archive/inventory.csv` by hand.

## Rows

| Field | Rule |
|---|---|
| scope | standard control network tariffs: every tariff code the document prices (residential, business, demand, HV, subtransmission, unmetered, controlled load, export/feed-in). Not: alternative control services, fee-based services, retail prices |
| price | the **total network price** as printed (`basis` "NUoS"). When the document prints only parts (distribution and transmission separately) and no total, emit the parts with basis "DUoS"/"TUoS" (the build stores only totals) and report the year. Never add numbers up |
| GST | `gst` "excl" when the price excludes GST; a document printing both: the exclusive one. Prices for periods before 1 July 2000 carry no GST: "excl", note "before GST". Only GST-inclusive printed: emit `gst` "incl" (not stored) and report it |
| value | the number exactly as printed, without `$` and thousands separators (`1,234.50` -> `1234.50`); negative only if printed negative |
| unit | as printed or as the column heading states (`$/kVA/month`, `c/kWh`, `$ per annum`) |
| code | as printed; a document with no codes: the tariff name as printed, note "no code printed" |
| component | label as printed; set `time_band` / `season` explicitly when only a column heading says (peak, off-peak, summer) |
| zone | a code priced by zone: note `zone: <name>` |
| locator | `locators.pdf(page)` (1-based page of the file), `locators.xlsx(sheet, cell)`, `locators.pdf(page, ocr=True)` for image-only pages (rapidocr). `.xls`/`.xlsb`/`.doc` cannot be re-read by the validator: use the year's PDF or xlsx |
| metering | a per-tariff metering charge printed in the price table: `schema.write_metering(f"history_{slug}", rows)` (`schema.METERING_COLUMNS`). Separate metering price lists: report, do not parse |
| note | caveats: "closed to new customers", "site-specific", "proposed prices", page/table |

Never fabricate: a value that cannot be read is left out and reported.

## Check before you report

```
.venv/bin/python scripts/history/<slug>.py
.venv/bin/python scripts/history/check.py <slug> --sources
```

`check.py` builds the database with your file in a scratch directory (never `data/tariffdb/`), lists what is stored per
distributor-year and what the build leaves out, runs every rule check and re-reads each value at its locator. All
checks must PASS. Spot-check 3 tariffs per layout against the page text yourself.

## Do not

Edit `data/tariffdb/`, `scripts/tariffdb/`, other parsers, `data/tariffdb/curated/`, `code_alias.csv`; run
`build.py` without `--out`; commit; use `git stash`.

## Report (final message, no code)

Per distributor and pricing year: document parsed, tariffs, rates, status (final/provisional); years with nothing
stored and why; the layouts met and how prices are printed (basis, GST, units); inventory corrections made; spot-checks;
anything uncertain marked [UNSURE].
