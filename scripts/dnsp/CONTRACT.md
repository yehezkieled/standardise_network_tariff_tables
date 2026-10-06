# DNSP parser contract

Goal: parse distributor-published network tariff price documents into the normalised long CSV defined in
`scripts/schema.py` (`COLUMNS`). One row per (tariff code, charge component).

Rules
- Use `.venv/bin/python` (openpyxl, pandas, pdfplumber, xlrd installed). If you need another package:
  `uv pip install --python .venv/bin/python <pkg>`.
- Write ONE script `scripts/dnsp/<slug>.py` that parses ALL listed files for the distributor(s) and writes
  `out/dnsp/<slug>.csv` with exactly `schema.COLUMNS`. It must run non-interactively from the repo root:
  `.venv/bin/python scripts/dnsp/<slug>.py`. Prefer label-based detection over hardcoded cell positions;
  keep each file's parse in its own function; import `schema` and `units` via
  `sys.path.insert(0, "scripts")`.
- `side`: "DNSP" for files under `sources/dnsp/`, "AER_HOSTED" for files under `sources/aer/2023-24_price_lists/`
  and `sources/aer/dnsp_copies/`.
- Scope: standard control service network tariffs (what the AER approves in the annual pricing proposal:
  residential, small/large business, LV/HV/sub-transmission, unmetered supply, embedded generation/export,
  public-lighting *network* tariffs where listed in the network price list). Exclude alternative control
  services (metering charges, fee-based/ancillary services, ACS public lighting asset charges) and retail/DMO
  figures. Include every network tariff code the document publishes, including obsolete/closed/legacy/trial
  tariffs if they carry prices (the reconciler needs them to detect DNSP-only tariffs). Site-specific /
  individually calculated tariffs: include if priced, note "site-specific".
- Components: one row per published price component (fixed/standing/supply charge, each energy block /
  time band / season, each demand or capacity charge, export charges/rebates). `component` = label as
  published (join header hierarchy with " - " if needed). `unit` = as published (e.g. "c/kWh",
  "$/kVA/month", "$/day", "$/customer/year"). `value` = numeric as published (negative for rebates/credits
  only if published negative). Fill `value_std`/`unit_std` with `units.to_std(value, unit, component)`.
  Fill `charge_type`, `time_band`, `season` with the helpers in `schema.py` (override when the document is
  explicit and the helper is wrong).
- `basis`: price basis as published. Total network price (NUoS = distribution + transmission/DPPC +
  jurisdictional scheme) -> "NUoS". Distribution-only -> "DUoS". If the document shows separate columns or
  tables for DUoS / TUoS / JSA / NUoS, emit ALL of them as separate rows with the matching basis. If
  unclear -> "unknown" and explain in `note`. Quote the document text that establishes the basis in your
  summary.
- `gst`: "excl" or "incl" per document; if unstated assume "excl" and say so in `note` and in your summary.
- `source_file`: repo-relative path. `source_url`: the EXACT url for that file from `sources/inventory.csv`.
- `tariff_code`: exactly as published (whitespace stripped). `tariff_name`: as published.
  `customer_class`: tariff class / customer group heading if present.
- `note`: caveats (e.g. "LFiT included", "site-specific", "closed to new customers", "mid-year variation",
  "page 37 Table 9", "proposed (pre-approval) prices").
- Metering printed in a network price table (a per-tariff metering charge column) is not a `charge` row: write it
  with `schema.write_metering(<slug>, rows)` (`schema.METERING_COLUMNS`, one row per printed cell, with locator);
  the tariff database stores it in `metering_price`.
- Never fabricate. If a value cannot be extracted, leave it out and record the gap in your summary.
- Spot-check: for each file pick 3 tariffs and compare your rows against the raw document text; include
  them in your summary.

Return in your final message (concise, no code): per file -> rows emitted, number of tariff codes, basis,
gst, how the document presents prices (page/sheet, table shape); format changes you observed between
years (structure, naming, units, code changes, new/removed tariffs); anything missing or unparseable;
the spot-checks; any uncertainty marked [UNSURE].
