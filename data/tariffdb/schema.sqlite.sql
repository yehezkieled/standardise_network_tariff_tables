-- Generated from scripts/tariffdb/spec.py by scripts/tariffdb/build.py; do not edit by hand.
-- Dialect: sqlite. Load order follows foreign keys.

CREATE TABLE financial_year (
  fin_year TEXT NOT NULL,
  start_date TEXT NOT NULL CHECK (start_date IS NULL OR (length(start_date) = 10 AND start_date GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  end_date TEXT NOT NULL CHECK (end_date IS NULL OR (length(end_date) = 10 AND end_date GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  PRIMARY KEY (fin_year),
  CHECK (start_date < end_date)
);

CREATE TABLE distributor (
  distributor_id TEXT NOT NULL,
  name TEXT NOT NULL,
  aer_label TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('NSW', 'VIC', 'QLD', 'SA', 'TAS', 'ACT', 'NT')),
  iana_timezone TEXT NOT NULL,
  observes_dst INTEGER NOT NULL CHECK (observes_dst IN (0, 1)),
  PRIMARY KEY (distributor_id),
  UNIQUE (name)
);

CREATE TABLE document_series (
  series_id TEXT NOT NULL,
  author TEXT NOT NULL CHECK (author IN ('AER', 'distributor')),
  distributor_id TEXT,
  fin_year TEXT NOT NULL,
  title TEXT NOT NULL,
  PRIMARY KEY (series_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (fin_year) REFERENCES financial_year (fin_year)
);

CREATE TABLE source_document (
  document_id TEXT NOT NULL,
  series_id TEXT NOT NULL,
  version_label TEXT NOT NULL,
  version_seq INTEGER NOT NULL CHECK (typeof(version_seq) IN ('integer', 'null')),
  author TEXT NOT NULL CHECK (author IN ('AER', 'distributor')),
  distributor_id TEXT,
  fin_year TEXT NOT NULL,
  document_type TEXT NOT NULL CHECK (document_type IN ('aer_consolidated_stakeholder_report', 'aer_stakeholder_report', 'pricing_proposal', 'pricing_proposal_overview', 'price_list', 'tariff_summary', 'tariff_schedule', 'schedule_of_charges', 'statement_of_tariff_classes', 'price_guide', 'pricing_schedule')),
  price_status TEXT NOT NULL CHECK (price_status IN ('proposed', 'approved', 'mixed', 'published', 'unverified')),
  recon_side TEXT NOT NULL CHECK (recon_side IN ('AER', 'AER_HOSTED', 'DNSP')),
  title TEXT NOT NULL,
  publication_date TEXT CHECK (publication_date IS NULL OR (length(publication_date) = 10 AND publication_date GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  publication_date_basis TEXT,
  retrieval_status TEXT NOT NULL CHECK (retrieval_status IN ('retrieved', 'not_retrievable')),
  local_path TEXT,
  source_url TEXT,
  access_note TEXT,
  sha256 TEXT,
  retrieved_on TEXT CHECK (retrieved_on IS NULL OR (length(retrieved_on) = 10 AND retrieved_on GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  retrieved_on_basis TEXT,
  committed_in_repo INTEGER NOT NULL CHECK (committed_in_repo IN (0, 1)),
  PRIMARY KEY (document_id),
  FOREIGN KEY (series_id) REFERENCES document_series (series_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (fin_year) REFERENCES financial_year (fin_year),
  UNIQUE (series_id, version_seq),
  UNIQUE (local_path),
  CHECK (retrieval_status <> 'retrieved' OR (local_path IS NOT NULL AND sha256 IS NOT NULL))
);

CREATE TABLE document_coverage (
  document_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  price_status TEXT NOT NULL CHECK (price_status IN ('proposed', 'approved', 'mixed', 'published', 'unverified')),
  evidence_document_id TEXT NOT NULL,
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  PRIMARY KEY (document_id, distributor_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (evidence_document_id) REFERENCES source_document (document_id)
);

CREATE TABLE document_ingestion (
  document_id TEXT NOT NULL,
  charge_count INTEGER NOT NULL CHECK (typeof(charge_count) IN ('integer', 'null')),
  listing_count INTEGER NOT NULL CHECK (typeof(listing_count) IN ('integer', 'null')),
  eligibility_count INTEGER NOT NULL CHECK (typeof(eligibility_count) IN ('integer', 'null')),
  tou_schedule_count INTEGER NOT NULL CHECK (typeof(tou_schedule_count) IN ('integer', 'null')),
  metering_count INTEGER NOT NULL CHECK (typeof(metering_count) IN ('integer', 'null')),
  status TEXT NOT NULL CHECK (status IN ('prices', 'rules_only', 'metadata_only', 'unavailable')),
  PRIMARY KEY (document_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  CHECK (charge_count >= 0 AND listing_count >= 0 AND eligibility_count >= 0 AND tou_schedule_count >= 0 AND metering_count >= 0)
);

CREATE TABLE tariff (
  tariff_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  identity_basis TEXT NOT NULL CHECK (identity_basis IN ('distributor_code', 'aer_label', 'aer_tariff_id')),
  PRIMARY KEY (tariff_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  UNIQUE (distributor_id, tariff_code)
);

CREATE TABLE tariff_alias (
  alias_id TEXT NOT NULL,
  tariff_id TEXT NOT NULL,
  alias_label TEXT NOT NULL,
  alias_kind TEXT NOT NULL CHECK (alias_kind IN ('aer_tariff_id', 'joint_label_member', 'regional_suffix', 'name_matched', 'code_variant')),
  document_id TEXT NOT NULL,
  note TEXT,
  PRIMARY KEY (alias_id),
  FOREIGN KEY (tariff_id) REFERENCES tariff (tariff_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id)
);

CREATE TABLE tariff_relation (
  relation_id TEXT NOT NULL,
  from_tariff_id TEXT NOT NULL,
  relation_type TEXT NOT NULL CHECK (relation_type IN ('replaced_by', 'opt_out_alternative', 'export_companion', 'same_prices_as', 'assigned_with', 'aer_sibling_code')),
  to_tariff_id TEXT NOT NULL,
  fin_year TEXT NOT NULL,
  document_id TEXT NOT NULL,
  locator TEXT,
  quote TEXT,
  note TEXT,
  PRIMARY KEY (relation_id),
  FOREIGN KEY (from_tariff_id) REFERENCES tariff (tariff_id),
  FOREIGN KEY (to_tariff_id) REFERENCES tariff (tariff_id),
  FOREIGN KEY (fin_year) REFERENCES financial_year (fin_year),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  CHECK (from_tariff_id <> to_tariff_id)
);

CREATE TABLE tariff_listing (
  listing_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  tariff_id TEXT NOT NULL,
  code_published TEXT,
  name_published TEXT,
  class_published TEXT,
  region TEXT,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR (length(effective_from) = 10 AND effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR (length(effective_to) = 10 AND effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  price_availability TEXT NOT NULL CHECK (price_availability IN ('priced', 'placeholder', 'rules_only')),
  locator TEXT NOT NULL,
  note TEXT,
  PRIMARY KEY (listing_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (tariff_id) REFERENCES tariff (tariff_id),
  CHECK (effective_from <= effective_to)
);

CREATE TABLE listing_flag (
  listing_id TEXT NOT NULL,
  flag TEXT NOT NULL CHECK (flag IN ('trial', 'closed_to_new', 'withdrawn', 'obsolete', 'grandfathered', 'site_specific', 'zero_priced_placeholder', 'transitional', 'indicative', 'proposed_price', 'includes_lfit', 'excludes_lfit', 'includes_metering', 'excludes_metering', 'joint_label_member', 'regional_variant')),
  evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('published_text', 'parser_note', 'document', 'curated')),
  evidence TEXT NOT NULL,
  PRIMARY KEY (listing_id, flag),
  FOREIGN KEY (listing_id) REFERENCES tariff_listing (listing_id)
);

CREATE TABLE charge (
  charge_id TEXT NOT NULL,
  listing_id TEXT NOT NULL,
  price_basis TEXT NOT NULL CHECK (price_basis IN ('NUoS', 'DUoS', 'TUoS', 'DPPC', 'JSA', 'unknown')),
  component_label TEXT NOT NULL,
  charge_type TEXT NOT NULL CHECK (charge_type IN ('fixed', 'energy', 'demand', 'capacity', 'export', 'other')),
  time_band TEXT,
  season TEXT,
  value_published TEXT NOT NULL,
  unit_published TEXT,
  unit_interpreted TEXT,
  normalisation_note TEXT,
  value_raw TEXT,
  value_num NUMERIC NOT NULL CHECK (typeof(value_num) IN ('integer', 'real', 'null')),
  value_std NUMERIC CHECK (typeof(value_std) IN ('integer', 'real', 'null')),
  unit_std TEXT,
  quantity TEXT NOT NULL CHECK (quantity IN ('kWh', 'kVAh', 'kW', 'kVA', 'kW_or_kVA', 'lamp', 'none')),
  period TEXT NOT NULL CHECK (period IN ('day', 'month', 'year', 'none', 'unstated')),
  period_inferred INTEGER NOT NULL CHECK (period_inferred IN (0, 1)),
  gst TEXT NOT NULL CHECK (gst IN ('excl', 'incl')),
  includes_metering TEXT NOT NULL CHECK (includes_metering IN ('yes', 'no', 'unknown')),
  includes_lfit TEXT NOT NULL CHECK (includes_lfit IN ('yes', 'no', 'unknown', 'not_applicable')),
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR (length(effective_from) = 10 AND effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR (length(effective_to) = 10 AND effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  locator TEXT NOT NULL,
  locator_kind TEXT NOT NULL CHECK (locator_kind IN ('xlsx', 'pdf', 'pdf-ocr')),
  sheet TEXT,
  cell TEXT,
  page INTEGER CHECK (typeof(page) IN ('integer', 'null')),
  verification TEXT NOT NULL CHECK (verification IN ('cell_display', 'page_text', 'ocr_sum_check')),
  note TEXT,
  PRIMARY KEY (charge_id),
  FOREIGN KEY (listing_id) REFERENCES tariff_listing (listing_id),
  CHECK (effective_from <= effective_to),
  CHECK ((locator_kind = 'xlsx' AND sheet IS NOT NULL AND cell IS NOT NULL AND page IS NULL) OR (locator_kind <> 'xlsx' AND page IS NOT NULL AND sheet IS NULL AND cell IS NULL))
);

CREATE TABLE charge_step (
  step_id TEXT NOT NULL,
  tariff_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  fin_year TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR (length(effective_from) = 10 AND effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR (length(effective_to) = 10 AND effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  step_group TEXT NOT NULL,
  component_label TEXT NOT NULL,
  step_index INTEGER NOT NULL CHECK (typeof(step_index) IN ('integer', 'null')),
  lower_bound NUMERIC CHECK (typeof(lower_bound) IN ('integer', 'real', 'null')),
  upper_bound NUMERIC CHECK (typeof(upper_bound) IN ('integer', 'real', 'null')),
  lower_inclusive INTEGER NOT NULL CHECK (lower_inclusive IN (0, 1)),
  upper_inclusive INTEGER NOT NULL CHECK (upper_inclusive IN (0, 1)),
  quantity_unit TEXT NOT NULL CHECK (quantity_unit IN ('kWh')),
  reset_period TEXT NOT NULL CHECK (reset_period IN ('day', 'billing_period_per_day', 'unstated')),
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  PRIMARY KEY (step_id),
  FOREIGN KEY (tariff_id) REFERENCES tariff (tariff_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (fin_year) REFERENCES financial_year (fin_year),
  CHECK (step_index > 0),
  CHECK (effective_from <= effective_to),
  CHECK (lower_bound IS NULL OR upper_bound IS NULL OR lower_bound < upper_bound)
);

CREATE TABLE metering_price (
  metering_price_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  fin_year TEXT NOT NULL,
  source_block TEXT NOT NULL CHECK (source_block IN ('aer_metering_sheet', 'aer_tariff_schedule_1', 'distributor_metering_block')),
  meter_class TEXT NOT NULL,
  tariff_codes_published TEXT,
  tariff_id TEXT,
  component_label TEXT,
  charge_basis TEXT NOT NULL CHECK (charge_basis IN ('per_year', 'per_meter', 'per_day', 'unstated')),
  value_published TEXT NOT NULL,
  unit_published TEXT,
  value_raw TEXT NOT NULL,
  value_num NUMERIC NOT NULL CHECK (typeof(value_num) IN ('integer', 'real', 'null')),
  value_c_per_day NUMERIC CHECK (typeof(value_c_per_day) IN ('integer', 'real', 'null')),
  locator TEXT NOT NULL,
  sheet TEXT NOT NULL,
  cell TEXT NOT NULL,
  PRIMARY KEY (metering_price_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (fin_year) REFERENCES financial_year (fin_year),
  FOREIGN KEY (tariff_id) REFERENCES tariff (tariff_id)
);

CREATE TABLE tou_schedule (
  tou_schedule_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  fin_year TEXT NOT NULL,
  name TEXT NOT NULL,
  time_basis TEXT NOT NULL CHECK (time_basis IN ('local_time', 'standard_time', 'daylight_time', 'not_stated')),
  public_holidays TEXT NOT NULL CHECK (public_holidays IN ('as_weekday', 'as_non_business_day', 'not_stated', 'unchanged')),
  covers_full_day INTEGER NOT NULL CHECK (covers_full_day IN (0, 1)),
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  note TEXT,
  PRIMARY KEY (tou_schedule_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (fin_year) REFERENCES financial_year (fin_year)
);

CREATE TABLE tou_window (
  window_id TEXT NOT NULL,
  tou_schedule_id TEXT NOT NULL,
  period TEXT NOT NULL CHECK (period IN ('peak', 'shoulder', 'off_peak', 'solar_soak', 'critical_peak', 'super_off_peak', 'demand_window', 'export_charge_window', 'export_reward_window', 'controlled_load_supply', 'anytime', 'high_season_peak', 'low_season_peak')),
  period_label TEXT NOT NULL,
  day_type TEXT NOT NULL CHECK (day_type IN ('weekday', 'weekend', 'all_days', 'business_day', 'non_business_day')),
  start_time TEXT NOT NULL CHECK (length(start_time) = 5 AND substr(start_time, 3, 1) = ':' AND (substr(start_time, 1, 2) BETWEEN '00' AND '23' AND substr(start_time, 4, 2) BETWEEN '00' AND '59' OR start_time = '24:00')),
  end_time TEXT NOT NULL CHECK (length(end_time) = 5 AND substr(end_time, 3, 1) = ':' AND (substr(end_time, 1, 2) BETWEEN '00' AND '23' AND substr(end_time, 4, 2) BETWEEN '00' AND '59' OR end_time = '24:00')),
  months TEXT,
  season TEXT,
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  PRIMARY KEY (window_id),
  FOREIGN KEY (tou_schedule_id) REFERENCES tou_schedule (tou_schedule_id),
  CHECK (months IS NOT NULL OR season IS NOT NULL),
  CHECK (start_time < end_time),
  CHECK (length(start_time) = 5 AND length(end_time) = 5)
);

CREATE TABLE tou_window_month (
  window_id TEXT NOT NULL,
  month INTEGER NOT NULL CHECK (typeof(month) IN ('integer', 'null')),
  PRIMARY KEY (window_id, month),
  FOREIGN KEY (window_id) REFERENCES tou_window (window_id),
  CHECK (month BETWEEN 1 AND 12)
);

CREATE TABLE tariff_tou (
  tariff_tou_id TEXT NOT NULL,
  tariff_id TEXT NOT NULL,
  tou_schedule_id TEXT NOT NULL,
  applies_to TEXT NOT NULL CHECK (applies_to IN ('energy', 'demand', 'export', 'controlled_load', 'all')),
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR (length(effective_from) = 10 AND effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR (length(effective_to) = 10 AND effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  document_id TEXT NOT NULL,
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  PRIMARY KEY (tariff_tou_id),
  FOREIGN KEY (tariff_id) REFERENCES tariff (tariff_id),
  FOREIGN KEY (tou_schedule_id) REFERENCES tou_schedule (tou_schedule_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  CHECK (effective_from <= effective_to)
);

CREATE TABLE demand_rule (
  demand_rule_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  fin_year TEXT NOT NULL,
  measure TEXT NOT NULL CHECK (measure IN ('kW', 'kVA')),
  interval_minutes INTEGER CHECK (typeof(interval_minutes) IN ('integer', 'null')),
  aggregation TEXT NOT NULL CHECK (aggregation IN ('monthly_max', 'billing_period_max', 'rolling_12_month_max', 'daily_average_of_monthly_max', 'average_of_highest_days', 'agreed', 'not_stated')),
  aggregation_count INTEGER CHECK (typeof(aggregation_count) IN ('integer', 'null')),
  window_tou_schedule_id TEXT,
  window_period TEXT CHECK (window_period IN ('peak', 'shoulder', 'off_peak', 'solar_soak', 'critical_peak', 'super_off_peak', 'demand_window', 'export_charge_window', 'export_reward_window', 'controlled_load_supply', 'anytime', 'high_season_peak', 'low_season_peak')),
  months TEXT,
  minimum_chargeable NUMERIC CHECK (typeof(minimum_chargeable) IN ('integer', 'real', 'null')),
  minimum_unit TEXT,
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  note TEXT,
  PRIMARY KEY (demand_rule_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (fin_year) REFERENCES financial_year (fin_year),
  FOREIGN KEY (window_tou_schedule_id) REFERENCES tou_schedule (tou_schedule_id)
);

CREATE TABLE tariff_demand_rule (
  tariff_demand_rule_id TEXT NOT NULL,
  tariff_id TEXT NOT NULL,
  demand_rule_id TEXT NOT NULL,
  time_band TEXT,
  season TEXT,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR (length(effective_from) = 10 AND effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR (length(effective_to) = 10 AND effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  document_id TEXT NOT NULL,
  PRIMARY KEY (tariff_demand_rule_id),
  FOREIGN KEY (tariff_id) REFERENCES tariff (tariff_id),
  FOREIGN KEY (demand_rule_id) REFERENCES demand_rule (demand_rule_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  CHECK (effective_from <= effective_to)
);

CREATE TABLE eligibility_rule (
  rule_id TEXT NOT NULL,
  tariff_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  fin_year TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR (length(effective_from) = 10 AND effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR (length(effective_to) = 10 AND effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')),
  rule_type TEXT NOT NULL CHECK (rule_type IN ('customer_type', 'voltage_level', 'consumption_min', 'consumption_max', 'demand_min', 'demand_max', 'meter_type', 'assignment', 'availability', 'requires_technology', 'opt_out_to', 'minimum_demand_charge', 'other')),
  operator TEXT CHECK (operator IN ('eq', 'lt', 'le', 'gt', 'ge', 'ge_unstated', 'le_unstated')),
  value_num NUMERIC CHECK (typeof(value_num) IN ('integer', 'real', 'null')),
  value_unit TEXT,
  value_text TEXT,
  target_tariff_id TEXT,
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  note TEXT,
  PRIMARY KEY (rule_id),
  FOREIGN KEY (tariff_id) REFERENCES tariff (tariff_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (fin_year) REFERENCES financial_year (fin_year),
  FOREIGN KEY (target_tariff_id) REFERENCES tariff (tariff_id),
  CHECK (effective_from <= effective_to),
  CHECK (value_num IS NOT NULL OR value_text IS NOT NULL OR target_tariff_id IS NOT NULL)
);

CREATE TABLE price_adjustment (
  adjustment_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('metering_adder', 'lfit_adder', 'lfit_rebate')),
  distributor_id TEXT NOT NULL,
  fin_year TEXT NOT NULL,
  amount NUMERIC CHECK (typeof(amount) IN ('integer', 'real', 'null')),
  amount_unit TEXT,
  formula TEXT NOT NULL,
  aer_document_id TEXT NOT NULL,
  distributor_document_id TEXT NOT NULL,
  evidence_document_id TEXT NOT NULL,
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  PRIMARY KEY (adjustment_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (fin_year) REFERENCES financial_year (fin_year),
  FOREIGN KEY (aer_document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (distributor_document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (evidence_document_id) REFERENCES source_document (document_id)
);

CREATE TABLE price_adjustment_tariff (
  adjustment_id TEXT NOT NULL,
  tariff_id TEXT NOT NULL,
  metering_price_id TEXT,
  expected_delta_std NUMERIC CHECK (typeof(expected_delta_std) IN ('integer', 'real', 'null')),
  delta_unit TEXT,
  PRIMARY KEY (adjustment_id, tariff_id),
  FOREIGN KEY (adjustment_id) REFERENCES price_adjustment (adjustment_id),
  FOREIGN KEY (tariff_id) REFERENCES tariff (tariff_id),
  FOREIGN KEY (metering_price_id) REFERENCES metering_price (metering_price_id)
);

CREATE TABLE exception_type (
  exception_code TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  representation TEXT NOT NULL,
  test TEXT NOT NULL,
  PRIMARY KEY (exception_code)
);

CREATE TABLE exception_instance (
  instance_id TEXT NOT NULL,
  exception_code TEXT NOT NULL,
  distributor_id TEXT,
  fin_year TEXT,
  tariff_id TEXT,
  listing_id TEXT,
  charge_id TEXT,
  document_id TEXT,
  related_document_id TEXT,
  quantity NUMERIC CHECK (typeof(quantity) IN ('integer', 'real', 'null')),
  quantity_unit TEXT,
  detail TEXT NOT NULL,
  PRIMARY KEY (instance_id),
  FOREIGN KEY (exception_code) REFERENCES exception_type (exception_code),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (fin_year) REFERENCES financial_year (fin_year),
  FOREIGN KEY (tariff_id) REFERENCES tariff (tariff_id),
  FOREIGN KEY (listing_id) REFERENCES tariff_listing (listing_id),
  FOREIGN KEY (charge_id) REFERENCES charge (charge_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (related_document_id) REFERENCES source_document (document_id)
);
