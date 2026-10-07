-- Generated from scripts/tariffdb/spec.py by scripts/tariffdb/build.py; do not edit by hand.
-- SQLite. Tables are in foreign-key order.

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
  fin_year TEXT NOT NULL CHECK (fin_year IN ('2023-24', '2024-25', '2025-26', '2026-27')),
  publisher TEXT NOT NULL CHECK (publisher IN ('AER', 'distributor')),
  document_type TEXT NOT NULL CHECK (document_type IN ('aer_consolidated_stakeholder_report', 'aer_stakeholder_report', 'aer_landing_page', 'pricing_proposal', 'pricing_proposal_overview', 'price_list', 'tariff_summary', 'tariff_schedule', 'schedule_of_charges', 'statement_of_tariff_classes', 'price_guide', 'pricing_schedule')),
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
  customer_class TEXT,
  status TEXT NOT NULL CHECK (status IN ('provisional', 'final')),
  document_id TEXT NOT NULL,
  PRIMARY KEY (distributor_id, tariff_code, effective_from),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  CHECK (effective_from <= effective_to)
);

CREATE TABLE rate (
  rate_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  charge_type TEXT NOT NULL CHECK (charge_type IN ('daily', 'usage', 'demand', 'capacity', 'export', 'metering', 'other')),
  tou_period TEXT CHECK (tou_period IN ('anytime', 'peak', 'shoulder', 'off_peak', 'super_off_peak', 'critical_peak', 'solar_soak', 'capacity_minimum', 'capacity_remaining', 'critical_minimum', 'dynamic_maximum', 'dynamic_minimum')),
  season TEXT CHECK (season IN ('summer', 'non_summer', 'high', 'low', 'winter')),
  block INTEGER CHECK (typeof(block) IN ('integer', 'null')),
  block_from NUMERIC CHECK (typeof(block_from) IN ('integer', 'real', 'null')),
  block_to NUMERIC CHECK (typeof(block_to) IN ('integer', 'real', 'null')),
  block_unit TEXT CHECK (block_unit IN ('kWh/day', 'kWh/billing_day', 'kWh/quarter', 'kWh')),
  region TEXT,
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
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (distributor_id, tariff_code, effective_from) REFERENCES tariff (distributor_id, tariff_code, effective_from),
  CHECK (effective_from <= effective_to),
  CHECK (block IS NULL OR block > 0),
  CHECK (block_from IS NULL OR block_to IS NULL OR block_from < block_to)
);

CREATE TABLE tou_window (
  window_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  applies_to TEXT NOT NULL CHECK (applies_to IN ('usage', 'demand', 'export', 'controlled_load', 'all')),
  tou_period TEXT NOT NULL CHECK (tou_period IN ('peak', 'shoulder', 'off_peak', 'solar_soak', 'critical_peak', 'super_off_peak', 'demand_window', 'export_charge_window', 'export_reward_window', 'controlled_load_supply', 'anytime', 'high_season_peak', 'low_season_peak')),
  period_label TEXT NOT NULL,
  day_type TEXT NOT NULL CHECK (day_type IN ('weekday', 'weekend', 'all_days', 'business_day', 'non_business_day')),
  start_time TEXT NOT NULL CHECK (start_time GLOB '[0-2][0-9]:[0-5][0-9]' AND start_time <= '24:00'),
  end_time TEXT NOT NULL CHECK (end_time GLOB '[0-2][0-9]:[0-5][0-9]' AND end_time <= '24:00'),
  months TEXT,
  season TEXT,
  time_basis TEXT NOT NULL CHECK (time_basis IN ('local_time', 'standard_time', 'daylight_time', 'not_stated')),
  public_holidays TEXT NOT NULL CHECK (public_holidays IN ('as_weekday', 'as_non_business_day', 'not_stated', 'unchanged')),
  document_id TEXT NOT NULL,
  locator TEXT NOT NULL,
  PRIMARY KEY (window_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (distributor_id, tariff_code, effective_from) REFERENCES tariff (distributor_id, tariff_code, effective_from),
  CHECK (effective_from <= effective_to),
  CHECK (start_time < end_time),
  CHECK (months IS NOT NULL OR season IS NOT NULL)
);

CREATE TABLE eligibility (
  criterion_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  effective_to TEXT NOT NULL CHECK (effective_to IS NULL OR effective_to GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  criterion TEXT NOT NULL CHECK (criterion IN ('customer_type', 'voltage_level', 'consumption_min', 'consumption_max', 'demand_min', 'demand_max', 'meter_type', 'assignment', 'availability', 'requires_technology', 'opt_out_to', 'minimum_demand_charge', 'other')),
  operator TEXT CHECK (operator IN ('eq', 'lt', 'le', 'gt', 'ge', 'ge_unstated', 'le_unstated')),
  value_num NUMERIC CHECK (typeof(value_num) IN ('integer', 'real', 'null')),
  value_unit TEXT,
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
  CHECK ((value_num IS NULL) = (operator IS NULL))
);
