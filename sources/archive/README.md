# Historical source archive (pricing years before 2023-24)

Every public document found that holds a distributor's network tariffs, metering charges or network export charges
for a pricing year before 2023-24, committed so it can be parsed later. 2023-24 onward lives in
`sources/inventory.csv` and the tariff database.

```
sources/archive/
  inventory.csv          one row per file (written by scripts/archive_sources.py add, never by hand)
  gaps.csv               hand-written: why a distributor-year has no price document
  coverage.csv           generated: per distributor and pricing year, what is held (archive_sources.py coverage)
  <distributor_id>/<pricing year>/<file>
```

## Pricing year keys

| kind | key | period | who |
|---|---|---|---|
| financial year | `2014-15` | 1 Jul 2014 - 30 Jun 2015 | NSW, ACT, QLD, SA, TAS (from 2008-09), NT; Victoria to 1999-00 and from 2021-22 |
| calendar year | `2019` | 1 Jan - 31 Dec 2019 | Victoria (CitiPower, Powercor, United Energy, Jemena, AusNet) to 2020; Tasmania (Aurora) to 2007 |
| half year | `2021-H1`, `2000-H2` | 1 Jan - 30 Jun 2021; 1 Jul - 31 Dec 2000 | Victoria, between financial and calendar years (Tasmania: `2008-H1`) |

## inventory.csv

| column | meaning |
|---|---|
| distributor_id, distributor | current name, also for a predecessor (EnergyAustralia -> Ausgrid, Integral -> Endeavour, Country Energy -> Essential, ActewAGL -> Evoenergy, ETSA Utilities -> SA Power Networks, Aurora -> TasNetworks, SP AusNet -> AusNet Services) |
| pricing_year, year_kind | key above |
| side | `AER_HOSTED` distributor document on aer.gov.au, `AER` AER-authored, `DNSP` distributor's own site, `REGULATOR_HOSTED` state regulator before the AER |
| document_kind | `price_list`, `pricing_proposal`, `tariff_summary`, `tariff_schedule`, `price_guide`, `annual_tariff_report`, `pricing_model`, `metering_price_list`, `alternative_control_services`, `tariff_structure_statement`, `statement_of_reasons`, `enforceable_undertaking`, `other` |
| title, version_label | as published; version_label tells initial / revised / AER approved apart |
| price_status | `published` (distributor's own, final), `approved` (regulator approved), `proposed`, `unverified`, `not_applicable` (no prices) |
| publication_date, publication_date_basis | only when the title, document or landing page states it (basis says which) |
| landing_page, source_url | page that links the file; exact URL fetched (Wayback: `.../web/<timestamp>id_/<original>`) |
| retrieved_via, retrieved_on | `direct` (download date) or `wayback` (capture date) |
| local_path, bytes, sha256 | the committed file |
| note | anything a reader needs (predecessor name, period oddities) |

## Rules

- Add a document: `scripts/archive_sources.py add ...` (`--help` lists the values). It downloads, hashes and
  registers; an identical file already held is skipped, a different file with the same name gets a `_2` suffix.
- No file over 95 MB (GitHub limit 100 MB); none needs Git LFS today.
- A distributor-year without a price document needs a `gaps.csv` row (reason + evidence searched).
- After any change: `scripts/archive_sources.py coverage`, then `.venv/bin/python -m unittest tests/test_archive.py`.
