-- Generated from scripts/tariffdb/spec.py by scripts/tariffdb/build.py; do not edit by hand.
-- SQLite. Tables are in foreign-key order.

CREATE TABLE value_list (
  list_name TEXT NOT NULL,
  value TEXT NOT NULL,
  definition TEXT NOT NULL,
  PRIMARY KEY (list_name, value)
);

CREATE TABLE data_dictionary (
  table_name TEXT NOT NULL,
  column_name TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK (typeof(ordinal) IN ('integer', 'null')),
  data_type TEXT NOT NULL,
  required INTEGER NOT NULL CHECK (required IN (0, 1)),
  is_primary_key INTEGER NOT NULL CHECK (is_primary_key IN (0, 1)),
  references_table TEXT,
  value_list TEXT,
  unit TEXT,
  definition TEXT NOT NULL,
  PRIMARY KEY (table_name, column_name)
);

CREATE TABLE unit (
  unit TEXT NOT NULL,
  quantity TEXT NOT NULL CHECK (quantity IN ('customer', 'lamp', 'kWh', 'kVAh', 'kW', 'kVA')),
  billing_period TEXT CHECK (billing_period IN ('day', 'month', 'year', 'not_stated')),
  unit_std TEXT,
  multiplier NUMERIC CHECK (typeof(multiplier) IN ('integer', 'real', 'null')),
  calendar_factor TEXT CHECK (calendar_factor IN ('days_in_month', 'days_in_year')),
  definition TEXT NOT NULL,
  PRIMARY KEY (unit),
  CHECK ((multiplier IS NULL) OR (calendar_factor IS NULL)),
  CHECK ((unit_std IS NULL) = (billing_period IS 'not_stated'))
);

CREATE TABLE distributor (
  distributor_id TEXT NOT NULL,
  name TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('NSW', 'VIC', 'QLD', 'SA', 'TAS', 'ACT', 'NT')),
  iana_timezone TEXT NOT NULL,
  observes_dst INTEGER NOT NULL CHECK (observes_dst IN (0, 1)),
  PRIMARY KEY (distributor_id)
);

CREATE TABLE source_document (
  document_id TEXT NOT NULL,
  distributor_id TEXT,
  pricing_year TEXT NOT NULL CHECK (pricing_year IN ('1996-97', '1997-98', '1998-99', '1999-00', '2000-01', '2001-02', '2002-03', '2003-04', '2004-05', '2005-06', '2006-07', '2007-08', '2008-09', '2009-10', '2010-11', '2011-12', '2012-13', '2013-14', '2014-15', '2015-16', '2016-17', '2017-18', '2018-19', '2019-20', '2020-21', '2021-22', '2022-23', '2023-24', '2024-25', '2025-26', '2026-27', '2000', '2001', '2002', '2003', '2004', '2005', '2006', '2007', '2008', '2009', '2010', '2011', '2012', '2013', '2014', '2015', '2016', '2017', '2018', '2019', '2020', '2000-H2', '2008-H1', '2021-H1')),
  publisher TEXT NOT NULL CHECK (publisher IN ('aer', 'distributor', 'regulator')),
  document_type TEXT NOT NULL CHECK (document_type IN ('aer_consolidated_stakeholder_report', 'aer_stakeholder_report', 'aer_landing_page', 'pricing_proposal', 'pricing_proposal_overview', 'price_list', 'tariff_summary', 'tariff_schedule', 'schedule_of_charges', 'statement_of_tariff_classes', 'price_guide', 'pricing_schedule', 'annual_tariff_report', 'pricing_model', 'tariff_structure_statement', 'tariff_trial_notification')),
  hosted_by_aer INTEGER NOT NULL CHECK (hosted_by_aer IN (0, 1)),
  version_label TEXT NOT NULL,
  version_seq INTEGER NOT NULL CHECK (typeof(version_seq) IN ('integer', 'null')),
  price_status TEXT NOT NULL CHECK (price_status IN ('proposed', 'approved', 'mixed', 'published', 'unverified')),
  published_on TEXT CHECK (published_on IS NULL OR published_on GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  source_url TEXT,
  local_path TEXT,
  sha256 TEXT,
  PRIMARY KEY (document_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  CHECK ((local_path IS NULL) = (sha256 IS NULL))
);

CREATE TABLE tariff (
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  tariff_name TEXT,
  customer_class TEXT CHECK (customer_class IN ('residential', 'small_business', 'medium_business', 'large_business', 'major_business', 'business', 'controlled_load', 'unmetered', 'public_lighting', 'generation', 'storage')),
  customer_class_published TEXT,
  pricing_basis TEXT CHECK (pricing_basis IN ('published', 'site_specific', 'trial')),
  status TEXT NOT NULL CHECK (status IN ('provisional', 'final')),
  document_id TEXT NOT NULL,
  PRIMARY KEY (distributor_id, tariff_code, effective_from),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  CHECK (effective_from <= effective_to),
  CHECK (customer_class IS NULL OR customer_class_published IS NOT NULL)
);

CREATE TABLE tariff_assignment (
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  assignment_no INTEGER NOT NULL CHECK (typeof(assignment_no) IN ('integer', 'null')),
  assignment TEXT NOT NULL CHECK (assignment IN ('default', 'opt_in', 'opt_out', 'mandatory', 'assigned_by_distributor', 'retailer_request')),
  applies_to TEXT CHECK (applies_to IN ('new_connection', 'meter_change', 'existing_customer')),
  document_id TEXT NOT NULL,
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  PRIMARY KEY (distributor_id, tariff_code, effective_from, assignment_no),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (distributor_id, tariff_code, effective_from) REFERENCES tariff (distributor_id, tariff_code, effective_from),
  CHECK (assignment_no > 0)
);

CREATE TABLE rate (
  rate_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  charge_type TEXT NOT NULL CHECK (charge_type IN ('daily', 'usage', 'demand', 'capacity', 'export', 'metering', 'other')),
  tou_period TEXT CHECK (tou_period IN ('anytime', 'peak', 'shoulder', 'off_peak', 'super_off_peak', 'critical_peak', 'solar_soak', 'capacity_minimum', 'capacity_remaining', 'critical_minimum', 'dynamic_maximum', 'dynamic_minimum')),
  season TEXT CHECK (season IN ('summer', 'non_summer', 'high', 'low', 'winter', 'spring', 'autumn')),
  block INTEGER CHECK (typeof(block) IN ('integer', 'null')),
  block_from NUMERIC CHECK (typeof(block_from) IN ('integer', 'real', 'null')),
  block_to NUMERIC CHECK (typeof(block_to) IN ('integer', 'real', 'null')),
  block_unit TEXT CHECK (block_unit IN ('kWh/day', 'kWh/billing_day', 'kWh/quarter', 'kWh')),
  region TEXT,
  register TEXT CHECK (register IN ('general', 'controlled_load', 'export')),
  condition TEXT,
  value NUMERIC NOT NULL CHECK (typeof(value) IN ('integer', 'real', 'null')),
  unit TEXT NOT NULL,
  value_published TEXT NOT NULL,
  unit_published TEXT,
  component TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('provisional', 'final')),
  document_id TEXT NOT NULL,
  locator TEXT NOT NULL,
  note TEXT,
  PRIMARY KEY (rate_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (unit) REFERENCES unit (unit),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (distributor_id, tariff_code, effective_from) REFERENCES tariff (distributor_id, tariff_code, effective_from),
  CHECK (effective_from <= effective_to),
  CHECK (block IS NULL OR block > 0),
  CHECK (block_from IS NULL OR block_to IS NULL OR block_from < block_to),
  CHECK ((register IS NULL) = (charge_type IN ('daily', 'metering', 'other'))),
  CHECK (charge_type != 'export' OR register = 'export'),
  CHECK (condition IS NULL OR condition GLOB 'opt_in:[a-z0-9]*' OR condition GLOB 'meter_type:[a-z0-9]*' OR condition GLOB 'meter_class:[a-z0-9]*')
);

CREATE TABLE tou_window (
  window_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  applies_to TEXT NOT NULL CHECK (applies_to IN ('usage', 'demand', 'export', 'controlled_load', 'all')),
  tou_period TEXT NOT NULL CHECK (tou_period IN ('anytime', 'peak', 'shoulder', 'off_peak', 'super_off_peak', 'critical_peak', 'solar_soak', 'capacity_minimum', 'capacity_remaining', 'critical_minimum', 'dynamic_maximum', 'dynamic_minimum', 'controlled_load_supply')),
  period_label TEXT NOT NULL,
  day_type TEXT NOT NULL CHECK (day_type IN ('weekday', 'weekend', 'all_days', 'business_day', 'non_business_day')),
  start_time TEXT NOT NULL CHECK (start_time GLOB '[0-2][0-9]:[0-5][0-9]' AND start_time <= '24:00'),
  end_time TEXT NOT NULL CHECK (end_time GLOB '[0-2][0-9]:[0-5][0-9]' AND end_time <= '24:00'),
  months TEXT,
  season TEXT CHECK (season IN ('summer', 'non_summer', 'high', 'low', 'winter', 'spring', 'autumn')),
  season_label TEXT,
  time_basis TEXT NOT NULL CHECK (time_basis IN ('local_time', 'standard_time', 'daylight_time', 'not_stated')),
  public_holidays TEXT NOT NULL CHECK (public_holidays IN ('as_weekday', 'as_non_business_day', 'unchanged', 'not_stated')),
  document_id TEXT NOT NULL,
  locator TEXT NOT NULL,
  PRIMARY KEY (window_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (distributor_id, tariff_code, effective_from) REFERENCES tariff (distributor_id, tariff_code, effective_from),
  CHECK (effective_from <= effective_to),
  CHECK (start_time < end_time),
  CHECK (months IS NOT NULL OR season_label IS NOT NULL),
  CHECK (season IS NULL OR season_label IS NOT NULL)
);

CREATE TABLE eligibility (
  criterion_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  criterion TEXT NOT NULL CHECK (criterion IN ('customer_type', 'voltage_level', 'consumption_min', 'consumption_max', 'demand_min', 'demand_max', 'meter_type', 'availability', 'requires_technology', 'opt_out_to', 'minimum_demand_charge', 'other')),
  operator TEXT CHECK (operator IN ('eq', 'lt', 'le', 'gt', 'ge', 'ge_unstated', 'le_unstated')),
  value_num NUMERIC CHECK (typeof(value_num) IN ('integer', 'real', 'null')),
  value_unit TEXT CHECK (value_unit IN ('kWh/yr', 'MWh/yr', 'GWh/yr', 'kW', 'MW', 'kVA', 'MVA')),
  value_text TEXT,
  target_tariff_code TEXT,
  document_id TEXT NOT NULL,
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  PRIMARY KEY (criterion_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (distributor_id, tariff_code, effective_from) REFERENCES tariff (distributor_id, tariff_code, effective_from),
  CHECK (effective_from <= effective_to),
  CHECK (value_num IS NOT NULL OR value_text IS NOT NULL OR target_tariff_code IS NOT NULL),
  CHECK ((value_num IS NULL) = (operator IS NULL)),
  CHECK ((value_num IS NULL) = (value_unit IS NULL)),
  CHECK (criterion != 'customer_type' OR value_text IS NULL OR value_text IN ('residential', 'small_business', 'medium_business', 'large_business', 'business', 'unmetered', 'public_lighting', 'embedded_generation', 'controlled_load', 'storage', 'ev_charging', 'any')),
  CHECK (criterion != 'voltage_level' OR value_text IS NULL OR value_text IN ('LV', 'HV', 'subtransmission', 'transmission', 'zone_substation')),
  CHECK (criterion != 'meter_type' OR value_text IS NULL OR value_text IN ('interval', 'smart', 'basic', 'accumulation', 'unmetered', 'any')),
  CHECK (criterion != 'availability' OR value_text IS NULL OR value_text IN ('open', 'closed_to_new', 'withdrawn', 'obsolete', 'trial', 'grandfathered', 'transitional')),
  CHECK (criterion != 'requires_technology' OR value_text IS NULL OR value_text IN ('solar', 'battery', 'ev', 'controlled_load_device', 'dedicated_circuit', 'export_capable', 'storage', 'flexible_load', 'heat_pump'))
);

CREATE TABLE charge_rule (
  rule_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  charge_type TEXT NOT NULL CHECK (charge_type IN ('demand', 'capacity', 'export')),
  tou_period TEXT CHECK (tou_period IN ('anytime', 'peak', 'shoulder', 'off_peak', 'super_off_peak', 'critical_peak', 'solar_soak', 'capacity_minimum', 'capacity_remaining', 'critical_minimum', 'dynamic_maximum', 'dynamic_minimum')),
  season TEXT CHECK (season IN ('summer', 'non_summer', 'high', 'low', 'winter', 'spring', 'autumn')),
  measure TEXT NOT NULL CHECK (measure IN ('kW', 'kVA', 'kWh')),
  interval_min INTEGER CHECK (typeof(interval_min) IN ('integer', 'null')),
  method TEXT NOT NULL CHECK (method IN ('max', 'avg_top_n_days', 'avg_top_n_intervals', 'agreed', 'max_of_agreed_and_measured', 'sum', 'assigned', 'avg_nominated_days', 'max_daily_window_mean', 'excess_over_window_max', 'kva_at_max_kw', 'avg_daily_max')),
  n INTEGER CHECK (typeof(n) IN ('integer', 'null')),
  reset TEXT NOT NULL CHECK (reset IN ('day', 'month', 'billing_period', 'season', 'year', 'year_from_april', 'rolling_12_months', 'rolling_13_months')),
  minimum_value NUMERIC CHECK (typeof(minimum_value) IN ('integer', 'real', 'null')),
  threshold_value NUMERIC CHECK (typeof(threshold_value) IN ('integer', 'real', 'null')),
  allowance_per_day NUMERIC CHECK (typeof(allowance_per_day) IN ('integer', 'real', 'null')),
  allowance_rollover INTEGER CHECK (allowance_rollover IN (0, 1)),
  document_id TEXT NOT NULL,
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  note TEXT,
  PRIMARY KEY (rule_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (distributor_id, tariff_code, effective_from) REFERENCES tariff (distributor_id, tariff_code, effective_from),
  CHECK (effective_from <= effective_to),
  CHECK ((n IS NULL) = (method NOT IN ('avg_top_n_days', 'avg_top_n_intervals', 'avg_nominated_days'))),
  CHECK (allowance_rollover IS NULL OR allowance_per_day IS NOT NULL)
);
