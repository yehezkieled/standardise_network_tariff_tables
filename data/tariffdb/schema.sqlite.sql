-- Generated from scripts/tariffdb/spec.py by scripts/tariffdb/build.py; do not edit by hand.
-- SQLite. Tables are in foreign-key order.

CREATE TABLE value_list (
  list_name TEXT NOT NULL,
  value TEXT NOT NULL,
  definition TEXT NOT NULL,
  PRIMARY KEY (list_name, value)
);

CREATE TABLE data_dictionary (
  object_name TEXT NOT NULL,
  column_name TEXT NOT NULL,
  object_type TEXT NOT NULL CHECK (object_type IN ('table', 'view')),
  ordinal INTEGER NOT NULL CHECK (typeof(ordinal) IN ('integer', 'null')),
  data_type TEXT NOT NULL,
  required INTEGER NOT NULL CHECK (required IN (0, 1)),
  is_primary_key INTEGER NOT NULL CHECK (is_primary_key IN (0, 1)),
  references_table TEXT,
  value_list TEXT,
  unit TEXT,
  definition TEXT NOT NULL,
  PRIMARY KEY (object_name, column_name)
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

CREATE TABLE public_holiday (
  state TEXT NOT NULL CHECK (state IN ('NSW', 'VIC', 'QLD', 'SA', 'TAS', 'ACT', 'NT')),
  holiday_date TEXT NOT NULL CHECK (holiday_date IS NULL OR holiday_date GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  name TEXT NOT NULL,
  PRIMARY KEY (state, holiday_date)
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

CREATE TABLE tariff_link (
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  link_no INTEGER NOT NULL CHECK (typeof(link_no) IN ('integer', 'null')),
  link_type TEXT NOT NULL CHECK (link_type IN ('alias', 'zone_variant_of', 'opt_out_to', 'replaces', 'secondary_of', 'primary_of', 'compulsory_pair', 'cannot_combine', 'available_only_from')),
  linked_code TEXT,
  linked_customer_class TEXT CHECK (linked_customer_class IN ('residential', 'small_business', 'medium_business', 'large_business', 'major_business', 'business', 'controlled_load', 'unmetered', 'public_lighting', 'generation', 'storage')),
  document_id TEXT,
  locator TEXT,
  quote TEXT,
  note TEXT,
  PRIMARY KEY (distributor_id, tariff_code, effective_from, link_no),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (distributor_id, tariff_code, effective_from) REFERENCES tariff (distributor_id, tariff_code, effective_from),
  CHECK (link_no > 0),
  CHECK (linked_code IS NOT NULL OR linked_customer_class IS NOT NULL OR link_type IN ('cannot_combine', 'secondary_of', 'primary_of')),
  CHECK (linked_customer_class IS NULL OR linked_code IS NULL),
  CHECK ((locator IS NULL) = (quote IS NULL)),
  CHECK (quote IS NOT NULL OR link_type IN ('alias', 'zone_variant_of')),
  CHECK (document_id IS NOT NULL OR quote IS NULL)
);

CREATE TABLE rate (
  rate_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  charge_type TEXT NOT NULL CHECK (charge_type IN ('daily', 'usage', 'demand', 'capacity', 'export', 'metering', 'other')),
  tou_period TEXT CHECK (tou_period IN ('anytime', 'peak', 'shoulder', 'off_peak', 'super_off_peak', 'critical_peak', 'solar_soak', 'capacity_minimum', 'capacity_remaining', 'critical_minimum', 'dynamic_maximum', 'dynamic_minimum')),
  season TEXT CHECK (season IN ('summer', 'non_summer', 'high', 'low', 'winter', 'spring', 'autumn')),
  block INTEGER CHECK (typeof(block) IN ('integer', 'null')),
  block_from NUMERIC CHECK (typeof(block_from) IN ('integer', 'real', 'null')),
  block_to NUMERIC CHECK (typeof(block_to) IN ('integer', 'real', 'null')),
  block_unit TEXT CHECK (block_unit IN ('kWh/day', 'kWh/billing_day', 'kWh/quarter', 'kWh')),
  region TEXT,
  register TEXT CHECK (register IN ('general', 'controlled_load', 'export')),
  value NUMERIC NOT NULL CHECK (typeof(value) IN ('integer', 'real', 'null')),
  unit TEXT NOT NULL,
  value_published TEXT NOT NULL,
  unit_published TEXT,
  component TEXT NOT NULL,
  locator TEXT NOT NULL,
  note TEXT,
  PRIMARY KEY (rate_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (unit) REFERENCES unit (unit),
  FOREIGN KEY (distributor_id, tariff_code, effective_from) REFERENCES tariff (distributor_id, tariff_code, effective_from),
  CHECK (block IS NULL OR block > 0),
  CHECK (block_from IS NULL OR block_to IS NULL OR block_from < block_to),
  CHECK ((register IS NULL) = (charge_type IN ('daily', 'metering', 'other'))),
  CHECK (charge_type != 'export' OR register = 'export')
);

CREATE TABLE rate_condition (
  rate_id TEXT NOT NULL,
  condition_kind TEXT NOT NULL CHECK (condition_kind IN ('opt_in', 'meter_type', 'meter_class')),
  value TEXT NOT NULL,
  document_id TEXT NOT NULL,
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  PRIMARY KEY (rate_id, condition_kind, value),
  FOREIGN KEY (rate_id) REFERENCES rate (rate_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  CHECK (value GLOB '[a-z0-9]*' AND value NOT GLOB '*[^a-z0-9_]*'),
  CHECK (condition_kind != 'meter_type' OR value IN ('interval', 'smart', 'basic', 'accumulation', 'unmetered', 'any'))
);

CREATE TABLE window_set (
  window_set_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  name TEXT NOT NULL,
  covers_full_day INTEGER NOT NULL CHECK (covers_full_day IN (0, 1)),
  document_id TEXT NOT NULL,
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  time_basis TEXT CHECK (time_basis IN ('local_time', 'standard_time', 'daylight_time')),
  time_basis_document_id TEXT,
  time_basis_locator TEXT,
  time_basis_quote TEXT,
  public_holidays TEXT CHECK (public_holidays IN ('as_weekday', 'as_non_business_day', 'unchanged')),
  public_holidays_document_id TEXT,
  public_holidays_locator TEXT,
  public_holidays_quote TEXT,
  note TEXT,
  PRIMARY KEY (window_set_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (time_basis_document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (public_holidays_document_id) REFERENCES source_document (document_id),
  CHECK ((time_basis IS NULL) = (time_basis_document_id IS NULL)),
  CHECK ((time_basis IS NULL) = (time_basis_locator IS NULL)),
  CHECK ((time_basis IS NULL) = (time_basis_quote IS NULL)),
  CHECK ((public_holidays IS NULL) = (public_holidays_document_id IS NULL)),
  CHECK ((public_holidays IS NULL) = (public_holidays_locator IS NULL)),
  CHECK ((public_holidays IS NULL) = (public_holidays_quote IS NULL))
);

CREATE TABLE season (
  season_id TEXT NOT NULL,
  window_set_id TEXT NOT NULL,
  season TEXT CHECK (season IN ('summer', 'non_summer', 'high', 'low', 'winter', 'spring', 'autumn')),
  season_label TEXT,
  PRIMARY KEY (season_id),
  FOREIGN KEY (window_set_id) REFERENCES window_set (window_set_id),
  CHECK (season IS NULL OR season_label IS NOT NULL)
);

CREATE TABLE season_part (
  season_id TEXT NOT NULL,
  part_no INTEGER NOT NULL CHECK (typeof(part_no) IN ('integer', 'null')),
  start_month INTEGER CHECK (typeof(start_month) IN ('integer', 'null')),
  start_day INTEGER CHECK (typeof(start_day) IN ('integer', 'null')),
  start_anchor TEXT CHECK (start_anchor IN ('dst_start', 'dst_end')),
  end_month INTEGER CHECK (typeof(end_month) IN ('integer', 'null')),
  end_day INTEGER CHECK (typeof(end_day) IN ('integer', 'null')),
  end_anchor TEXT CHECK (end_anchor IN ('dst_start', 'dst_end')),
  PRIMARY KEY (season_id, part_no),
  FOREIGN KEY (season_id) REFERENCES season (season_id),
  CHECK (part_no > 0),
  CHECK ((start_month IS NULL) = (start_day IS NULL)),
  CHECK ((end_month IS NULL) = (end_day IS NULL)),
  CHECK ((start_month IS NULL) = (start_anchor IS NOT NULL)),
  CHECK ((end_month IS NULL) = (end_anchor IS NOT NULL)),
  CHECK (start_month IS NULL OR start_month BETWEEN 1 AND 12),
  CHECK (end_month IS NULL OR end_month BETWEEN 1 AND 12),
  CHECK (start_day IS NULL OR start_day BETWEEN 1 AND 31),
  CHECK (end_day IS NULL OR end_day BETWEEN 1 AND 31)
);

CREATE TABLE time_window (
  window_id TEXT NOT NULL,
  window_set_id TEXT NOT NULL,
  season_id TEXT NOT NULL,
  tou_period TEXT NOT NULL CHECK (tou_period IN ('anytime', 'peak', 'shoulder', 'off_peak', 'super_off_peak', 'critical_peak', 'solar_soak', 'capacity_minimum', 'capacity_remaining', 'critical_minimum', 'dynamic_maximum', 'dynamic_minimum', 'controlled_load_supply')),
  period_label TEXT NOT NULL,
  day_type TEXT NOT NULL CHECK (day_type IN ('weekday', 'weekend', 'all_days', 'business_day', 'non_business_day')),
  start_time TEXT NOT NULL CHECK (start_time GLOB '[0-2][0-9]:[0-5][0-9]' AND start_time <= '24:00'),
  end_time TEXT NOT NULL CHECK (end_time GLOB '[0-2][0-9]:[0-5][0-9]' AND end_time <= '24:00'),
  locator TEXT,
  quote TEXT,
  PRIMARY KEY (window_id),
  FOREIGN KEY (window_set_id) REFERENCES window_set (window_set_id),
  FOREIGN KEY (season_id) REFERENCES season (season_id),
  CHECK (start_time < end_time),
  CHECK (quote IS NULL OR locator IS NOT NULL)
);

CREATE TABLE tariff_window_set (
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  window_set_id TEXT NOT NULL,
  applies_to TEXT NOT NULL CHECK (applies_to IN ('usage', 'demand', 'export', 'controlled_load', 'all')),
  locator TEXT,
  quote TEXT,
  PRIMARY KEY (distributor_id, tariff_code, effective_from, window_set_id, applies_to),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (window_set_id) REFERENCES window_set (window_set_id),
  FOREIGN KEY (distributor_id, tariff_code, effective_from) REFERENCES tariff (distributor_id, tariff_code, effective_from),
  CHECK (quote IS NULL OR locator IS NOT NULL)
);

CREATE TABLE eligibility (
  criterion_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  criterion_group INTEGER NOT NULL CHECK (typeof(criterion_group) IN ('integer', 'null')),
  criterion TEXT NOT NULL CHECK (criterion IN ('customer_type', 'voltage_level', 'consumption_min', 'consumption_max', 'demand_min', 'demand_max', 'meter_type', 'availability', 'requires_technology', 'minimum_demand_charge', 'supply_capacity', 'export_capacity', 'storage_capacity', 'connection', 'agreement', 'other')),
  operator TEXT CHECK (operator IN ('eq', 'lt', 'le', 'gt', 'ge', 'ge_unstated', 'le_unstated')),
  value_num NUMERIC CHECK (typeof(value_num) IN ('integer', 'real', 'null')),
  value_unit TEXT CHECK (value_unit IN ('kWh/yr', 'MWh/yr', 'GWh/yr', 'kW', 'MW', 'kVA', 'MVA', 'kWh', 'A_per_phase')),
  value_text TEXT,
  document_id TEXT NOT NULL,
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  PRIMARY KEY (criterion_id),
  FOREIGN KEY (distributor_id) REFERENCES distributor (distributor_id),
  FOREIGN KEY (document_id) REFERENCES source_document (document_id),
  FOREIGN KEY (distributor_id, tariff_code, effective_from) REFERENCES tariff (distributor_id, tariff_code, effective_from),
  CHECK (criterion_group > 0),
  CHECK (value_num IS NOT NULL OR value_text IS NOT NULL),
  CHECK ((value_num IS NULL) = (operator IS NULL)),
  CHECK ((value_num IS NULL) = (value_unit IS NULL)),
  CHECK (criterion != 'customer_type' OR value_text IS NULL OR value_text IN ('residential', 'small_business', 'medium_business', 'large_business', 'business', 'unmetered', 'public_lighting', 'embedded_generation', 'controlled_load', 'storage', 'ev_charging', 'any')),
  CHECK (criterion != 'voltage_level' OR value_text IS NULL OR value_text IN ('LV', 'HV', 'subtransmission', 'transmission', 'zone_substation')),
  CHECK (criterion != 'meter_type' OR value_text IS NULL OR value_text IN ('interval', 'smart', 'basic', 'accumulation', 'unmetered', 'any')),
  CHECK (criterion != 'availability' OR value_text IS NULL OR value_text IN ('open', 'closed_to_new', 'withdrawn', 'obsolete', 'trial', 'grandfathered', 'transitional')),
  CHECK (criterion != 'requires_technology' OR value_text IS NULL OR value_text IN ('solar', 'battery', 'ev', 'controlled_load_device', 'dedicated_circuit', 'export_capable', 'storage', 'flexible_load', 'heat_pump')),
  CHECK (criterion != 'connection' OR value_text IS NULL OR value_text IN ('embedded_network_child', 'embedded_network_parent', 'not_embedded_network', 'single_phase', 'three_phase', 'multiple_nmis_aggregated', 'greenfield', 'dedicated_circuit', 'generator_connection', 'alpine_region', 'rural', 'near_terminal_station', 'far_from_terminal_station', 'specific_network_location')),
  CHECK (criterion != 'agreement' OR value_text IS NULL OR value_text IN ('partner_retailer', 'distributor_agreement', 'connection_agreement', 'trial_participant', 'retailer_request'))
);

CREATE TABLE charge_rule (
  rule_id TEXT NOT NULL,
  distributor_id TEXT NOT NULL,
  tariff_code TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from IS NULL OR effective_from GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
  charge_type TEXT NOT NULL CHECK (charge_type IN ('demand', 'capacity', 'export')),
  tou_period TEXT CHECK (tou_period IN ('anytime', 'peak', 'shoulder', 'off_peak', 'super_off_peak', 'critical_peak', 'solar_soak', 'capacity_minimum', 'capacity_remaining', 'critical_minimum', 'dynamic_maximum', 'dynamic_minimum')),
  season TEXT CHECK (season IN ('summer', 'non_summer', 'high', 'low', 'winter', 'spring', 'autumn')),
  measure TEXT NOT NULL CHECK (measure IN ('kW', 'kVA', 'kWh', 'kva_else_kw')),
  interval_min INTEGER CHECK (typeof(interval_min) IN ('integer', 'null')),
  method TEXT NOT NULL CHECK (method IN ('max', 'avg_top_n_days', 'avg_top_n_intervals', 'agreed', 'max_of_agreed_and_measured', 'sum', 'assigned', 'avg_nominated_days', 'max_daily_window_mean', 'excess_over_window_max', 'kva_at_max_kw', 'avg_daily_max')),
  n INTEGER CHECK (typeof(n) IN ('integer', 'null')),
  reset TEXT NOT NULL CHECK (reset IN ('day', 'month', 'billing_period', 'season', 'year', 'year_from_april', 'rolling_months')),
  lookback_months INTEGER CHECK (typeof(lookback_months) IN ('integer', 'null')),
  minimum_value NUMERIC CHECK (typeof(minimum_value) IN ('integer', 'real', 'null')),
  minimum_unit TEXT CHECK (minimum_unit IN ('kW', 'kVA', 'kWh', 'kva_else_kw')),
  threshold_value NUMERIC CHECK (typeof(threshold_value) IN ('integer', 'real', 'null')),
  threshold_unit TEXT CHECK (threshold_unit IN ('kW', 'kVA', 'kWh', 'kva_else_kw')),
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
  CHECK ((n IS NULL) = (method NOT IN ('avg_top_n_days', 'avg_top_n_intervals', 'avg_nominated_days'))),
  CHECK (allowance_rollover IS NULL OR allowance_per_day IS NOT NULL),
  CHECK ((lookback_months IS NULL) = (reset != 'rolling_months')),
  CHECK (lookback_months IS NULL OR lookback_months > 0),
  CHECK ((minimum_value IS NULL) = (minimum_unit IS NULL)),
  CHECK ((threshold_value IS NULL) = (threshold_unit IS NULL)),
  CHECK (minimum_unit IS NOT 'kva_else_kw' AND threshold_unit IS NOT 'kva_else_kw')
);

-- Views: the tables flattened for reading (data_dictionary describes their columns).

CREATE VIEW tariff_flat (distributor_id, distributor_name, state, tariff_code, effective_from, effective_to, pricing_year, tariff_name, customer_class, customer_class_published, pricing_basis, status, is_default, assignments, rate_id, component, charge_type, tou_period, season, register, block, block_from, block_to, block_unit, value, unit, value_std, unit_std, calendar_factor, value_published, unit_published, conditions, window_period_label, window_day_type, window_start, window_end, window_season_label, window_season_dates, time_basis, public_holidays, rule_measure, rule_interval_min, rule_method, rule_n, rule_reset, rule_lookback_months, rule_minimum_value, rule_minimum_unit, rule_threshold_value, rule_threshold_unit) AS
WITH tw AS MATERIALIZED (  -- each tariff's windows (controlled-load supply hours price nothing)
  SELECT s.distributor_id, s.tariff_code, s.effective_from, s.applies_to, w.window_set_id, w.tou_period,
         w.period_label, w.day_type, w.start_time, w.end_time, se.season, se.season_label, w.season_id,
         ws.time_basis, ws.public_holidays
  FROM tariff_window_set s JOIN time_window w USING (window_set_id) JOIN window_set ws USING (window_set_id)
  JOIN season se ON se.season_id = w.season_id
  WHERE w.tou_period != 'controlled_load_supply'),
season_dates AS MATERIALIZED (  -- a season's dates as 'MM-DD to MM-DD' (or a daylight-saving anchor), parts ', '-joined
  SELECT season_id, group_concat(coalesce(start_anchor, printf('%02d-%02d', start_month, start_day)) || ' to '
         || coalesce(end_anchor, printf('%02d-%02d', end_month, end_day)), ', ') AS dates
  FROM (SELECT * FROM season_part ORDER BY season_id, part_no) GROUP BY season_id),
asg AS MATERIALIZED (  -- each tariff-period's assignment statements
  SELECT distributor_id, tariff_code, effective_from, max(assignment = 'default') AS is_default,
         group_concat(x, '; ') AS assignments
  FROM (SELECT DISTINCT distributor_id, tariff_code, effective_from, assignment,
               assignment || coalesce(' (' || applies_to || ')', '') AS x
        FROM tariff_assignment ORDER BY distributor_id, tariff_code, effective_from, x)
  GROUP BY distributor_id, tariff_code, effective_from)
,flags AS MATERIALIZED (  -- tariff-periods with windows stated for controlled load or export (joins.group_windows)
  SELECT distributor_id, tariff_code, effective_from, max(applies_to = 'controlled_load') AS has_cl,
         max(applies_to = 'export') AS has_export
  FROM tw GROUP BY distributor_id, tariff_code, effective_from),
rg AS MATERIALIZED (  -- each priced period's rate with the window sets (applies_to) of its charge group
  SELECT r.rate_id, r.distributor_id, r.tariff_code, r.effective_from, r.tou_period, r.season,
    CASE WHEN r.charge_type = 'usage' AND r.register = 'controlled_load'
           THEN CASE WHEN f.has_cl THEN 'controlled_load' ELSE 'usage' END
         WHEN r.charge_type = 'export' THEN CASE WHEN f.has_export THEN 'export' ELSE 'usage' END
         WHEN r.charge_type = 'usage' THEN 'usage' WHEN r.charge_type IN ('demand', 'capacity') THEN 'demand'
    END AS applies
  FROM rate r JOIN flags f USING (distributor_id, tariff_code, effective_from)
  WHERE r.tou_period != 'anytime'),
matched AS (  -- the windows of that group with the rate's period, in its season (joins.rate_windows)
  SELECT rg.rate_id, tw.*, (tw.season IS rg.season) AS exact
  FROM rg JOIN tw ON tw.distributor_id = rg.distributor_id AND tw.tariff_code = rg.tariff_code
    AND tw.effective_from = rg.effective_from AND tw.tou_period = rg.tou_period
    AND (rg.season IS NULL OR tw.season IS NULL OR rg.season = tw.season)
    AND (tw.applies_to = rg.applies OR (tw.applies_to = 'all' AND rg.applies IN ('usage', 'demand')))),
win AS MATERIALIZED (  -- the windows stated for the rate's own season when there are any; identical windows once
  SELECT DISTINCT m.rate_id, m.period_label, m.day_type, m.start_time, m.end_time, m.season_label, sdt.dates,
         m.time_basis, m.public_holidays
  FROM (SELECT matched.*, max(exact) OVER (PARTITION BY rate_id) AS any_exact FROM matched) m
  LEFT JOIN season_dates sdt ON sdt.season_id = m.season_id
  WHERE m.exact OR NOT m.any_exact),
rule AS MATERIALIZED (  -- the most specific rule measuring the rate's quantity (billcalc.find_rule)
  SELECT * FROM (
    SELECT r.rate_id, c.*, row_number() OVER (PARTITION BY r.rate_id
             ORDER BY (c.tou_period IS NOT NULL) * 2 + (c.season IS NOT NULL) DESC) AS rank
    FROM rate r JOIN unit u ON u.unit = r.unit
    JOIN charge_rule c ON c.distributor_id = r.distributor_id AND c.tariff_code = r.tariff_code
      AND c.effective_from = r.effective_from AND c.charge_type = r.charge_type
      AND (c.measure = u.quantity OR (c.measure = 'kva_else_kw' AND u.quantity = 'kVA'))
      AND (c.tou_period IS NULL OR c.tou_period = r.tou_period) AND (c.season IS NULL OR c.season = r.season))
  WHERE rank = 1),
cond AS MATERIALIZED (
  SELECT rate_id, group_concat(condition_kind || ':' || value, '; ') AS conditions
  FROM (SELECT * FROM rate_condition ORDER BY rate_id, condition_kind, value) GROUP BY rate_id)
SELECT t.distributor_id, d.name, d.state, t.tariff_code, t.effective_from, t.effective_to, sd.pricing_year,
  t.tariff_name, t.customer_class, t.customer_class_published, t.pricing_basis, t.status, asg.is_default,
  asg.assignments,
  r.rate_id, r.component, r.charge_type, r.tou_period, r.season, r.register, r.block, r.block_from, r.block_to,
  r.block_unit, r.value, r.unit, CASE WHEN u.multiplier IS NOT NULL THEN r.value * u.multiplier END, u.unit_std,
  u.calendar_factor, r.value_published, r.unit_published, cond.conditions,
  win.period_label, win.day_type, win.start_time, win.end_time, win.season_label, win.dates, win.time_basis,
  win.public_holidays,
  rule.measure, rule.interval_min, rule.method, rule.n, rule.reset, rule.lookback_months, rule.minimum_value,
  rule.minimum_unit, rule.threshold_value, rule.threshold_unit
FROM tariff t JOIN distributor d USING (distributor_id)
JOIN source_document sd ON sd.document_id = t.document_id
LEFT JOIN asg ON asg.distributor_id = t.distributor_id AND asg.tariff_code = t.tariff_code
  AND asg.effective_from = t.effective_from
LEFT JOIN rate r ON r.distributor_id = t.distributor_id AND r.tariff_code = t.tariff_code
  AND r.effective_from = t.effective_from
LEFT JOIN unit u ON u.unit = r.unit
LEFT JOIN cond ON cond.rate_id = r.rate_id
LEFT JOIN win ON win.rate_id = r.rate_id
LEFT JOIN rule ON rule.rate_id = r.rate_id
ORDER BY t.distributor_id, t.tariff_code, t.effective_from, r.rate_id, win.season_label, win.day_type, win.start_time;

CREATE VIEW tou_flat (distributor_id, distributor_name, state, tariff_code, effective_from, effective_to, pricing_year, tariff_name, customer_class, customer_class_published, pricing_basis, status, is_default, assignments, applies_to, window_set_id, window_set_name, covers_full_day, time_basis, public_holidays, tou_period, period_label, day_type, start_time, end_time, season, season_label, season_part, season_start_month, season_start_day, season_start_anchor, season_end_month, season_end_day, season_end_anchor) AS
WITH asg AS MATERIALIZED (  -- each tariff-period's assignment statements
  SELECT distributor_id, tariff_code, effective_from, max(assignment = 'default') AS is_default,
         group_concat(x, '; ') AS assignments
  FROM (SELECT DISTINCT distributor_id, tariff_code, effective_from, assignment,
               assignment || coalesce(' (' || applies_to || ')', '') AS x
        FROM tariff_assignment ORDER BY distributor_id, tariff_code, effective_from, x)
  GROUP BY distributor_id, tariff_code, effective_from)
SELECT t.distributor_id, d.name, d.state, t.tariff_code, t.effective_from, t.effective_to, sd.pricing_year,
  t.tariff_name, t.customer_class, t.customer_class_published, t.pricing_basis, t.status, asg.is_default,
  asg.assignments,
  s.applies_to, ws.window_set_id, ws.name, ws.covers_full_day, ws.time_basis, ws.public_holidays, w.tou_period,
  w.period_label, w.day_type, w.start_time, w.end_time, se.season, se.season_label, sp.part_no, sp.start_month,
  sp.start_day, sp.start_anchor, sp.end_month, sp.end_day, sp.end_anchor
FROM tariff t JOIN distributor d USING (distributor_id)
JOIN source_document sd ON sd.document_id = t.document_id
LEFT JOIN asg ON asg.distributor_id = t.distributor_id AND asg.tariff_code = t.tariff_code
  AND asg.effective_from = t.effective_from
LEFT JOIN tariff_window_set s ON s.distributor_id = t.distributor_id AND s.tariff_code = t.tariff_code
  AND s.effective_from = t.effective_from
LEFT JOIN window_set ws ON ws.window_set_id = s.window_set_id
LEFT JOIN time_window w ON w.window_set_id = s.window_set_id
LEFT JOIN season se ON se.season_id = w.season_id
LEFT JOIN season_part sp ON sp.season_id = w.season_id
ORDER BY t.distributor_id, t.tariff_code, t.effective_from, s.applies_to, ws.window_set_id, se.season_label,
  w.tou_period, w.day_type, w.start_time, sp.part_no;

CREATE VIEW unit_spelling (unit, unit_published, rates) AS
SELECT unit, unit_published, count(*) FROM rate GROUP BY unit, unit_published ORDER BY unit, unit_published;
