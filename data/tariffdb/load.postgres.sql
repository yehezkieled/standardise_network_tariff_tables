-- Generated from scripts/tariffdb/spec.py by scripts/tariffdb/build.py; do not edit by hand.
-- Usage (from data/tariffdb):  psql -v ON_ERROR_STOP=1 -d <database> -f load.postgres.sql
-- An empty unquoted CSV field loads as NULL, which is what the tables mean by it.
BEGIN;
\i schema.postgres.sql
\copy financial_year (fin_year, start_date, end_date) FROM 'tables/financial_year.csv' WITH (FORMAT csv, HEADER true)
\copy distributor (distributor_id, name, aer_label, state, iana_timezone, observes_dst) FROM 'tables/distributor.csv' WITH (FORMAT csv, HEADER true)
\copy document_series (series_id, author, distributor_id, fin_year, title) FROM 'tables/document_series.csv' WITH (FORMAT csv, HEADER true)
\copy source_document (document_id, series_id, version_label, version_seq, author, distributor_id, fin_year, document_type, price_status, recon_side, title, publication_date, publication_date_basis, retrieval_status, local_path, source_url, access_note, sha256, retrieved_on, retrieved_on_basis, committed_in_repo) FROM 'tables/source_document.csv' WITH (FORMAT csv, HEADER true)
\copy document_coverage (document_id, distributor_id, price_status, evidence_document_id, locator, quote) FROM 'tables/document_coverage.csv' WITH (FORMAT csv, HEADER true)
\copy document_ingestion (document_id, charge_count, listing_count, eligibility_count, tou_schedule_count, metering_count, status) FROM 'tables/document_ingestion.csv' WITH (FORMAT csv, HEADER true)
\copy tariff (tariff_id, distributor_id, tariff_code, identity_basis) FROM 'tables/tariff.csv' WITH (FORMAT csv, HEADER true)
\copy tariff_alias (alias_id, tariff_id, alias_label, alias_kind, document_id, note) FROM 'tables/tariff_alias.csv' WITH (FORMAT csv, HEADER true)
\copy tariff_relation (relation_id, from_tariff_id, relation_type, to_tariff_id, fin_year, document_id, locator, quote, note) FROM 'tables/tariff_relation.csv' WITH (FORMAT csv, HEADER true)
\copy tariff_listing (listing_id, document_id, tariff_id, code_published, name_published, class_published, region, effective_from, effective_to, price_availability, locator, note) FROM 'tables/tariff_listing.csv' WITH (FORMAT csv, HEADER true)
\copy listing_flag (listing_id, flag, evidence_kind, evidence) FROM 'tables/listing_flag.csv' WITH (FORMAT csv, HEADER true)
\copy charge (charge_id, listing_id, price_basis, component_label, charge_type, time_band, season, value_published, unit_published, unit_interpreted, normalisation_note, value_raw, value_num, value_std, unit_std, quantity, period, period_inferred, gst, includes_metering, includes_lfit, effective_from, effective_to, locator, locator_kind, sheet, cell, page, verification, note) FROM 'tables/charge.csv' WITH (FORMAT csv, HEADER true)
\copy charge_step (step_id, tariff_id, document_id, fin_year, effective_from, effective_to, step_group, component_label, step_index, lower_bound, upper_bound, lower_inclusive, upper_inclusive, quantity_unit, reset_period, locator, quote) FROM 'tables/charge_step.csv' WITH (FORMAT csv, HEADER true)
\copy metering_price (metering_price_id, document_id, distributor_id, fin_year, source_block, meter_class, tariff_codes_published, tariff_id, component_label, charge_basis, value_published, unit_published, value_raw, value_num, value_c_per_day, locator, sheet, cell) FROM 'tables/metering_price.csv' WITH (FORMAT csv, HEADER true)
\copy tou_schedule (tou_schedule_id, distributor_id, document_id, fin_year, name, time_basis, public_holidays, covers_full_day, locator, quote, note) FROM 'tables/tou_schedule.csv' WITH (FORMAT csv, HEADER true)
\copy tou_window (window_id, tou_schedule_id, period, period_label, day_type, start_time, end_time, months, season, locator, quote) FROM 'tables/tou_window.csv' WITH (FORMAT csv, HEADER true)
\copy tou_window_month (window_id, month) FROM 'tables/tou_window_month.csv' WITH (FORMAT csv, HEADER true)
\copy tariff_tou (tariff_tou_id, tariff_id, tou_schedule_id, applies_to, effective_from, effective_to, document_id, locator, quote) FROM 'tables/tariff_tou.csv' WITH (FORMAT csv, HEADER true)
\copy demand_rule (demand_rule_id, distributor_id, document_id, fin_year, measure, interval_minutes, aggregation, aggregation_count, window_tou_schedule_id, window_period, months, minimum_chargeable, minimum_unit, locator, quote, note) FROM 'tables/demand_rule.csv' WITH (FORMAT csv, HEADER true)
\copy tariff_demand_rule (tariff_demand_rule_id, tariff_id, demand_rule_id, time_band, season, effective_from, effective_to, document_id) FROM 'tables/tariff_demand_rule.csv' WITH (FORMAT csv, HEADER true)
\copy eligibility_rule (rule_id, tariff_id, document_id, fin_year, effective_from, effective_to, rule_type, operator, value_num, value_unit, value_text, target_tariff_id, locator, quote, note) FROM 'tables/eligibility_rule.csv' WITH (FORMAT csv, HEADER true)
\copy price_adjustment (adjustment_id, kind, distributor_id, fin_year, amount, amount_unit, formula, aer_document_id, distributor_document_id, evidence_document_id, locator, quote) FROM 'tables/price_adjustment.csv' WITH (FORMAT csv, HEADER true)
\copy price_adjustment_tariff (adjustment_id, tariff_id, metering_price_id, expected_delta_std, delta_unit) FROM 'tables/price_adjustment_tariff.csv' WITH (FORMAT csv, HEADER true)
\copy exception_type (exception_code, title, description, representation, test) FROM 'tables/exception_type.csv' WITH (FORMAT csv, HEADER true)
\copy exception_instance (instance_id, exception_code, distributor_id, fin_year, tariff_id, listing_id, charge_id, document_id, related_document_id, quantity, quantity_unit, detail) FROM 'tables/exception_instance.csv' WITH (FORMAT csv, HEADER true)
COMMIT;
