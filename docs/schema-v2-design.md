# Tariff database schema v2: design record

Goal: store strict (normalised, controlled values and units, bad data rejected at load), show simple (flat views and an
.xlsx). `scripts/tariffdb/spec.py` stays the single source; SQL, JSON and docs stay generated. Landed as commits A to E
on one branch, each building and green.

## Conventions

- snake_case names, lowercase controlled values, ISO dates (`YYYY-MM-DD`), times `HH:MM` (end-exclusive, `24:00`).
- Every fixed list is a CHECK constraint generated from spec.py and a `value_list` row with its definition.
- NULL = no held document states it (never guessed). A stated value carries its document, locator and quote.
- Published text is kept beside its controlled value in a `*_published` column.
- One fact once: child tables carry the parent key only (no repeated `effective_to`, status or document).
- Derived numbers (`value_std`, `is_default`) live in views, not stored tables.

## Tables

| table | key | holds |
|---|---|---|
| value_list | list_name, value | every allowed value and its definition |
| data_dictionary | object_name, column_name | every table and view column: type, required, list, unit, meaning |
| unit | unit | stored units: quantity, billing period, unit_std, calendar_factor |
| distributor | distributor_id | name, state, time zone, DST, holiday_region |
| source_document | document_id | document versions: URL, sha256, publisher, status |
| tariff | distributor_id, tariff_code, effective_from | name, customer_class (+ _published), pricing_basis, status, document |
| tariff_assignment | tariff key, assignment_no | default / opt_in / opt_out / mandatory ... per quoted statement, with who it applies to |
| tariff_link | tariff key, link_no | link_type (alias, zone_variant_of, opt_out_to, replaces, secondary_of, primary_of, compulsory_pair, cannot_combine, available_only_from) to linked_code or linked_customer_class, quoted (aliases come from code_alias.csv) |
| eligibility | criterion_id | criterion_group (same group = all hold, any group qualifies), thresholds in controlled units |
| rate | rate_id | one price: charge type, period, season, block, value, unit (FK unit), value_published, unit_published |
| rate_condition | rate_id, condition_kind, value | opt_in / meter_type / meter_class (values of one kind = either) |
| window_set | window_set_id | one stated TOU schedule (one statement, however many years it covers): name, time_basis and public_holidays each with its own quote |
| season | season_id | a window set's season: controlled season + season_label as published |
| season_part | season_id, part_no | start/end month and day, or a DST anchor (dst_start / dst_end) |
| time_window | window_id | period, day type, start, end, season |
| tariff_window_set | tariff key, window_set_id, applies_to | which charges of a tariff a window set prices (two sets stating the same window: the readers keep it once, `joins.tariff_windows`) |
| charge_rule | rule_id | demand/capacity/export measurement: measure (kW, kVA, kva_else_kw), method, reset, lookback_months, minimum with unit, threshold |

Views: `tariff_flat` (tariff x rate x matching window, value_std, is_default, demand rule), `tou_flat` (tariff x window
x season part), `unit_spelling` (published unit spellings per stored unit).

## Decisions

- Assignment (captain decision A): child table `tariff_assignment`, one quoted row per statement; `is_default` in
  `tariff_flat` and the xlsx. Nothing published is dropped; 17 tariff-periods are both default and opt-in (README).
- '?' units: replaced only by a quoted per-rate unit fact (curated `rate_units`); where no held document states the
  billing period the unit is `c/kW/period_not_stated` (listed known gap, no value_std, no calendar factor).
- value_std = value x unit.multiplier; units per month or year carry calendar_factor (days_in_month, days_in_year).
- Rate status, document and effective_to were copies of the tariff's in every row: dropped from rate (and
  effective_to from every child table).
- Holidays: `distributor.holiday_region` names the state calendar; `window_set.public_holidays` says how windows
  treat them, quoted, NULL where silent.
- Release adds `tariffdb.xlsx` (one sheet per flat view + a columns sheet) beside the SQLite, CSV zip and SHA256SUMS.
