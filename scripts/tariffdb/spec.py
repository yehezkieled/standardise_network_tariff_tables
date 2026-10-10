"""Single source of truth for the tariff database schema: final network rates per tariff code, with the rules that
bill them, every value list and every unit.

`data/tariffdb/schema.sqlite.sql` (DDL) and `data/tariffdb/schema.json` (machine-readable table spec) are generated from
TABLES by `scripts/tariffdb/build.py`; docs/schema.md and docs/schema-erd.svg by `scripts/tariffdb/schema_doc.py`. The
tests fail when any of them is stale. The value_list, data_dictionary and unit tables are generated from this file too,
so every allowed value, column and unit has its meaning in the database.

Conventions: snake_case names; lowercase controlled values; every fixed list is a CHECK constraint and a value_list
row; NULL means no held document states it (never guessed); published text is kept beside its controlled value in a
*_published column. Column types: text, integer, numeric, date ('YYYY-MM-DD'), time ('HH:MM', end-exclusive, '24:00'
allowed), boolean (0/1). An empty CSV field is NULL.
"""

LAST_FIN_YEAR = 2026  # 2026-27; a new year: raise it
# pricing years: financial years (2025-26); Victoria priced by calendar year from 2001 to 2020 (with half years 2000-H2
# and 2021-H1 joining financial years on either side) and Tasmania until 2007 (2008-H1 before financial years)
FIN_YEARS = [f"{y}-{(y + 1) % 100:02d}" for y in range(1996, LAST_FIN_YEAR + 1)]
PRICING_YEARS = FIN_YEARS + [str(y) for y in range(2000, 2021)] + ["2000-H2", "2008-H1", "2021-H1"]

# ---------------------------------------------------------------------------------------------------- value lists
# Each list maps value -> definition; value_list holds them all and each column's CHECK constraint is built from one.
STATES = {"NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland", "SA": "South Australia",
          "TAS": "Tasmania", "ACT": "Australian Capital Territory", "NT": "Northern Territory"}
STATUSES = {"provisional": "rates from the AER's report, a document the AER hosts or the distributor's proposal",
            "final": "rates from the distributor's own published price list, or the schedule a state regulator "
                     "published or approved"}
PUBLISHERS = {"aer": "the Australian Energy Regulator",
              "distributor": "the distribution network service provider itself",
              "regulator": "a state regulator before the AER (ESC, ESCOSA, QCA, IPART, OTTER ...)"}
DOCUMENT_TYPES = {
    "aer_consolidated_stakeholder_report": "the AER's consolidated report of every distributor's approved prices",
    "aer_stakeholder_report": "the AER's report of one distributor's approved prices",
    "aer_landing_page": "an AER web page that lists a distributor's pricing documents",
    "pricing_proposal": "a distributor's annual pricing proposal to the AER",
    "pricing_proposal_overview": "a distributor's summary of its pricing proposal",
    "price_list": "a distributor's published network price list",
    "tariff_summary": "a distributor's short summary of its tariffs",
    "tariff_schedule": "a schedule of tariffs (prices by tariff code)",
    "schedule_of_charges": "a schedule of network charges",
    "statement_of_tariff_classes": "a statement of tariff classes and tariffs",
    "price_guide": "a distributor's network price or tariff guide (how tariffs apply and bill)",
    "pricing_schedule": "a pricing schedule",
    "annual_tariff_report": "a distributor's annual tariff report",
    "pricing_model": "a distributor's pricing model workbook",
    "tariff_structure_statement": "a distributor's tariff structure statement for a regulatory period",
    "tariff_trial_notification": "a distributor's notification of a tariff trial",
}
# unverified: a distributor document hosted by the AER whose regulatory status no held source states; mixed: one AER
# version carrying approved prices for some distributors and proposed for others
PRICE_STATUS = {"proposed": "prices proposed to the regulator, not yet approved",
                "approved": "prices the regulator approved",
                "mixed": "an AER version carrying approved prices for some distributors and proposed for others",
                "published": "prices the distributor published for billing",
                "unverified": "a distributor document hosted by the AER whose regulatory status no held source states"}
# tariff.customer_class: the class a published tariff-class heading names; NULL when the heading names none
CUSTOMER_CLASSES = {
    "residential": "residential (domestic) customers",
    "small_business": "small business customers",
    "medium_business": "medium business customers",
    "large_business": "large business customers (including heading names such as large industrial & commercial)",
    "major_business": "major (very large) business customers",
    "business": "business customers, size not named",
    "controlled_load": "controlled-load supply (a separately metered, switched circuit)",
    "unmetered": "unmetered supply",
    "public_lighting": "street and public lighting",
    "generation": "generators and export tariffs",
    "storage": "grid-scale or customer storage",
}
# tariff.pricing_basis: how the stored prices apply, as the published heading or document states it
PRICING_BASES = {"published": "the price list publishes the prices that apply to every customer on the tariff",
                 "site_specific": "prices set for each site; the price list prints the site-specific tariff's rates",
                 "trial": "a tariff trial: prices published for the customers who join the trial"}
CHARGE_TYPES = {"daily": "fixed charge per day (or per lamp per day)",
                "usage": "per kWh (or kVAh) consumed",
                "demand": "per kW or kVA of measured demand",
                "capacity": "per kW or kVA of agreed, assigned or contracted capacity",
                "export": "per kWh or kW exported (negative = a reward paid)",
                "metering": "the distributor's separately priced metering charge",
                "other": "a charge that fits none of the other types"}
# rate.tou_period: the time-of-use period a price applies in (anytime when it has none), plus the few non-period price
# bands distributors publish (capacity_minimum/remaining, critical_minimum, dynamic_minimum/maximum)
RATE_PERIODS = {"anytime": "all times", "peak": "peak", "shoulder": "shoulder", "off_peak": "off-peak",
                "super_off_peak": "super off-peak", "critical_peak": "critical peak events the distributor notifies",
                "solar_soak": "the solar soak (midday low-price) period",
                "capacity_minimum": "the first kW or kVA of a capacity charge, up to the minimum",
                "capacity_remaining": "the kW or kVA of a capacity charge above the minimum",
                "critical_minimum": "critical minimum-demand events the distributor notifies",
                "dynamic_maximum": "dynamic maximum events the distributor notifies",
                "dynamic_minimum": "dynamic minimum events the distributor notifies"}
SEASONS = {"summer": "summer", "non_summer": "the months summer leaves", "high": "high season", "low": "low season",
           "winter": "winter", "spring": "spring", "autumn": "autumn"}
DAY_TYPES = {"weekday": "Monday to Friday", "weekend": "Saturday and Sunday", "all_days": "every day",
             "business_day": "Monday to Friday except public holidays",
             "non_business_day": "Saturday, Sunday and public holidays"}
# tou_window.tou_period: the rate vocabulary, so a window joins the rates it prices on (charge group, tou_period, season);
# controlled_load_supply is the one window that prices nothing: the hours a controlled-load circuit is switched on
TOU_PERIODS = RATE_PERIODS | {"controlled_load_supply": "the hours a controlled-load circuit is switched on (prices "
                                                        "nothing)"}
# rate.register: the meter register the priced quantity is measured on (NULL for daily, metering and other charges)
REGISTERS = {"general": "general-supply import", "controlled_load": "a separately metered controlled-load circuit",
             "export": "energy sent out to the network"}
# rate_condition: a rate with condition rows is charged only when the site meets one value of each kind it names
CONDITION_KINDS = {"opt_in": "only for a customer who opts in to the named offer",
                   "meter_type": "only at a site with that meter type",
                   "meter_class": "only at a site in that class of the distributor's metering schedule"}
# daylight_time: the source states the times in daylight-saving time (e.g. 'ADST'); times are stored as stated
TIME_BASES = {"local_time": "local clock time, following daylight saving where it applies",
              "standard_time": "local standard time all year",
              "daylight_time": "stated in daylight-saving time (e.g. ADST); stored as stated"}
HOLIDAY_RULES = {"as_weekday": "public holidays are priced as weekdays",
                 "as_non_business_day": "public holidays are priced as weekends (non-business days)",
                 "unchanged": "public holidays keep their day of the week"}
TOU_APPLIES = {"usage": "the tariff's usage charges", "demand": "the tariff's demand and capacity charges",
               "export": "the tariff's export charges", "controlled_load": "the tariff's controlled-load usage",
               "all": "every time-varying charge of the tariff"}
CRITERIA = {
    "customer_type": "the kind of customer (residential, small business ...)",
    "voltage_level": "the supply voltage",
    "consumption_min": "annual consumption at least (or above) the threshold",
    "consumption_max": "annual consumption at most (or below) the threshold",
    "demand_min": "demand at least (or above) the threshold",
    "demand_max": "demand at most (or below) the threshold",
    "meter_type": "the meter the site has",
    "availability": "whether the tariff is open, closed to new customers, withdrawn ...",
    "requires_technology": "equipment the site must have (solar, battery, a dedicated circuit ...)",
    "minimum_demand_charge": "the smallest demand charged",
    "supply_capacity": "the connection's supply capacity at least (or above, at most, below) the threshold",
    "export_capacity": "the site's export capacity (export limit) against the threshold",
    "storage_capacity": "the site's battery or storage size against the threshold",
    "connection": "how or where the site is connected (connection_value)",
    "agreement": "an agreement the customer or retailer must have (agreement_value)",
    "other": "a stated condition no other criterion covers (value_text as stated)",
}
# tariff_link: how a tariff relates to another code (linked_code) or to the tariffs of a customer class
LINK_TYPES = {"alias": "the AER prints this tariff as linked_code (a spelling or code_alias.csv rule)",
              "zone_variant_of": "this tariff is one pricing zone of linked_code as the AER prints it",
              "opt_out_to": "a customer may opt out of this tariff to the linked one",
              "replaces": "this tariff replaces the linked one",
              "secondary_of": "this tariff is held only beside a primary tariff: linked_code, or one of the class",
              "primary_of": "the linked tariff is held only beside this one",
              "compulsory_pair": "this tariff is taken only together with the linked one",
              "cannot_combine": "this tariff may not be combined with the linked one (both NULL: with any other)",
              "available_only_from": "only a customer now on the linked tariff may take this one"}
# season_part: a season boundary set by the daylight-saving changeover rather than a calendar date
SEASON_ANCHORS = {"dst_start": "the day daylight saving starts in the distributor's state",
                  "dst_end": "the day daylight saving ends in the distributor's state"}
# ge_unstated / le_unstated: a lower / upper bound whose source does not say whether the boundary value is included
OPERATORS = {"eq": "equal to", "lt": "below", "le": "at most", "gt": "above", "ge": "at least",
             "ge_unstated": "a lower bound; the source does not say whether the boundary value is included",
             "le_unstated": "an upper bound; the source does not say whether the boundary value is included"}
THRESHOLD_UNITS = {"kWh/yr": "kilowatt-hours a year", "MWh/yr": "megawatt-hours a year",
                   "GWh/yr": "gigawatt-hours a year", "kW": "kilowatts", "MW": "megawatts",
                   "kVA": "kilovolt-amperes", "MVA": "megavolt-amperes", "kWh": "kilowatt-hours (a storage size)",
                   "A_per_phase": "amperes per phase"}
# value_text vocabulary of the categorical criteria (the others take free text)
CRITERION_VALUES = {
    "customer_type": {"residential": "residential", "small_business": "small business",
                      "medium_business": "medium business", "large_business": "large business",
                      "business": "business, size not named", "unmetered": "unmetered supply",
                      "public_lighting": "public lighting", "embedded_generation": "embedded generation",
                      "controlled_load": "controlled load", "storage": "storage", "ev_charging": "EV charging",
                      "any": "any customer"},
    "voltage_level": {"LV": "low voltage (below 1 kV)", "HV": "high voltage (1 kV to 22 kV or 33 kV)",
                      "subtransmission": "subtransmission voltage", "transmission": "transmission voltage",
                      "zone_substation": "supplied at a zone substation"},
    "meter_type": {"interval": "an interval meter", "smart": "a smart (interval capable, remotely read) meter",
                   "basic": "a basic (accumulation) meter", "accumulation": "an accumulation meter",
                   "unmetered": "no meter", "any": "any meter"},
    "availability": {"open": "open to new customers", "closed_to_new": "closed to new customers",
                     "withdrawn": "withdrawn", "obsolete": "obsolete", "trial": "a trial tariff",
                     "grandfathered": "kept for existing customers only", "transitional": "a transitional tariff"},
    "requires_technology": {"solar": "solar PV", "battery": "a battery", "ev": "an electric vehicle",
                            "controlled_load_device": "a controlled-load device",
                            "dedicated_circuit": "a dedicated circuit", "export_capable": "an export-capable system",
                            "storage": "storage", "flexible_load": "a flexible load", "heat_pump": "a heat pump"},
    "connection": {"embedded_network_child": "a customer inside an embedded network",
                   "embedded_network_parent": "the parent connection point of an embedded network",
                   "not_embedded_network": "a connection that is not part of an embedded network",
                   "single_phase": "a single-phase connection", "three_phase": "a three-phase connection",
                   "multiple_nmis_aggregated": "several NMIs on one site, their consumption aggregated",
                   "greenfield": "a new (greenfield) connection",
                   "dedicated_circuit": "a separately wired dedicated circuit",
                   "generator_connection": "a connection point that exists primarily to connect a generator",
                   "alpine_region": "a supply in the alpine (snowfields) region",
                   "rural": "a rural supply",
                   "near_terminal_station": "within the stated distance of a terminal station",
                   "far_from_terminal_station": "beyond the stated distance from a terminal station",
                   "specific_network_location": "a named part of the network (named in the quote)"},
    "agreement": {"partner_retailer": "only through a retailer partnered with the distributor",
                  "distributor_agreement": "an agreement with the distributor",
                  "connection_agreement": "a connection agreement that sets it",
                  "trial_participant": "the customer has joined the distributor's trial",
                  "retailer_request": "at the retailer's request"},
}
# tariff_assignment: how customers come to be on a tariff, one row per quoted statement
ASSIGNMENTS = {"default": "customers are assigned to it unless they choose otherwise",
               "opt_in": "customers may choose it",
               "opt_out": "customers assigned to it may leave it",
               "mandatory": "customers must be on it",
               "assigned_by_distributor": "the distributor assigns it",
               "retailer_request": "assigned at the retailer's request"}
ASSIGNMENT_GROUPS = {"new_connection": "new connections (and new or upgraded meters the statement names)",
                     "meter_change": "customers whose meter is replaced or upgraded",
                     "existing_customer": "existing customers"}
BLOCK_UNITS = {"kWh/day": "kWh per day", "kWh/billing_day": "kWh per day, multiplied by the days in the billing "
                                                            "period (an unused allowance rolls over within it)",
               "kWh/quarter": "kWh per calendar quarter", "kWh": "kWh; the source states no reset period"}
# charge_rule: how a demand, capacity or export quantity is measured before its rate applies
RULE_CHARGES = {"demand": "demand charges", "capacity": "capacity charges", "export": "export charges"}
RULE_MEASURES = {"kW": "kilowatts", "kVA": "kilovolt-amperes", "kWh": "kilowatt-hours (an energy quantity)",
                 "kva_else_kw": "kilovolt-amperes where the meter records them, else kilowatts"}
RULE_METHODS = {
    "max": "the highest interval",
    "avg_top_n_days": "the mean of the n highest daily maxima",
    "avg_top_n_intervals": "the mean of the n highest intervals",
    "agreed": "a value agreed with the distributor, not measured",
    "max_of_agreed_and_measured": "the greater of the agreed value and the highest interval",
    "sum": "the total over the span (an energy quantity, e.g. export kWh above a free daily allowance)",
    "assigned": "a value the distributor sets (a transformer or connection rating), not measured",
    "avg_nominated_days": "the mean of the daily maxima on the n days the distributor nominates (critical peak days)",
    "max_daily_window_mean": "the highest of the daily means over the rate's window",
    "excess_over_window_max": "the highest interval in the rate's window less the highest in the peak window of the "
                              "same season, floored at zero",
    "kva_at_max_kw": "the kVA of the interval with the highest kW",
    "avg_daily_max": "the mean of every day's maximum",
}
RULE_N_METHODS = ["avg_top_n_days", "avg_top_n_intervals", "avg_nominated_days"]
# the span the measured value is taken over before it starts again
RULE_RESETS = {"day": "each day", "month": "each calendar month", "billing_period": "each billing period",
               "season": "each season", "year": "each financial year", "year_from_april": "1 April to 31 March",
               "rolling_months": "the current billing month and the months before it: lookback_months in all"}

# ---------------------------------------------------------------------------------------------------------- units
UNIT_QUANTITIES = {"customer": "per customer (a fixed charge)", "lamp": "per lamp (public lighting)",
                   "kWh": "per kilowatt-hour", "kVAh": "per kilovolt-ampere-hour", "kW": "per kilowatt",
                   "kVA": "per kilovolt-ampere"}
BILLING_PERIODS = {"day": "per day", "month": "per month", "year": "per year",
                   "not_stated": "no held document states the billing period"}
CALENDAR_FACTORS = {"days_in_month": "divide by the days in the billed month to get the per-day price",
                    "days_in_year": "divide by the days in the billed year to get the per-day price"}
# unit -> (quantity, billing period or None, standard unit, multiplier to it or None, calendar factor or None, meaning)
# value_std = value x multiplier; a unit priced per month or year reaches its per-day standard only with the calendar
UNITS = {
    "c/day": ("customer", "day", "c/day", 1, None, "cents per customer per day"),
    "c/lamp/day": ("lamp", "day", "c/lamp/day", 1, None, "cents per lamp (watt) per day"),
    "c/kWh": ("kWh", None, "c/kWh", 1, None, "cents per kilowatt-hour"),
    "c/kVAh": ("kVAh", None, "c/kVAh", 1, None, "cents per kilovolt-ampere-hour"),
    "c/kW/day": ("kW", "day", "c/kW/day", 1, None, "cents per kilowatt per day"),
    "c/kW/month": ("kW", "month", "c/kW/day", None, "days_in_month", "cents per kilowatt per month"),
    "c/kW/year": ("kW", "year", "c/kW/day", None, "days_in_year", "cents per kilowatt per year"),
    "c/kW/period_not_stated": ("kW", "not_stated", None, None, None,
                               "cents per kilowatt; no held document states the billing period"),
    "c/kVA/day": ("kVA", "day", "c/kVA/day", 1, None, "cents per kilovolt-ampere per day"),
    "c/kVA/month": ("kVA", "month", "c/kVA/day", None, "days_in_month", "cents per kilovolt-ampere per month"),
    "c/kVA/year": ("kVA", "year", "c/kVA/day", None, "days_in_year", "cents per kilovolt-ampere per year"),
    "c/kVA/period_not_stated": ("kVA", "not_stated", None, None, None,
                                "cents per kilovolt-ampere; no held document states the billing period"),
}
# the quantities each charge type may be priced in
QUANTITIES_BY_CHARGE = {"daily": {"customer", "lamp"}, "metering": {"customer", "kWh"}, "usage": {"kWh", "kVAh"},
                        "demand": {"kW", "kVA"}, "capacity": {"kW", "kVA"}, "export": {"kWh", "kVAh", "kW", "kVA"},
                        "other": set(UNIT_QUANTITIES)}

VALUE_LISTS = {
    "state": STATES, "status": STATUSES, "publisher": PUBLISHERS, "document_type": DOCUMENT_TYPES,
    "price_status": PRICE_STATUS,
    "pricing_year": {y: "half year a regulator priced by" if "-H" in y else "financial year, 1 July to 30 June"
                     if "-" in y else "calendar year a regulator priced by" for y in PRICING_YEARS},
    "customer_class": CUSTOMER_CLASSES, "pricing_basis": PRICING_BASES, "charge_type": CHARGE_TYPES,
    "rate_period": RATE_PERIODS, "season": SEASONS, "day_type": DAY_TYPES, "tou_period": TOU_PERIODS,
    "register": REGISTERS, "condition_kind": CONDITION_KINDS, "time_basis": TIME_BASES,
    "holiday_rule": HOLIDAY_RULES, "tou_applies": TOU_APPLIES, "criterion": CRITERIA, "operator": OPERATORS,
    "threshold_unit": THRESHOLD_UNITS, "assignment": ASSIGNMENTS, "assignment_group": ASSIGNMENT_GROUPS,
    "block_unit": BLOCK_UNITS, "rule_charge": RULE_CHARGES, "rule_measure": RULE_MEASURES,
    "rule_method": RULE_METHODS, "rule_reset": RULE_RESETS, "unit_quantity": UNIT_QUANTITIES,
    "billing_period": BILLING_PERIODS, "calendar_factor": CALENDAR_FACTORS, "link_type": LINK_TYPES,
    "season_anchor": SEASON_ANCHORS,
    **{f"{k}_value": v for k, v in CRITERION_VALUES.items()},
}


def col(name, type_, desc, *, null=False, pk=False, fk=None, enum=None, unit=None):
    """enum: the name of a VALUE_LISTS list."""
    assert enum is None or enum in VALUE_LISTS, enum
    return {"name": name, "type": type_, "nullable": null, "primary_key": pk, "references": fk, "enum": enum,
            "unit": unit, "description": desc}


def tariff_key(pk=False):
    """The key of the tariff period a child row belongs to; the period's dates, status and document are the tariff's."""
    return [col("distributor_id", "text", "distributor", pk=pk, fk="distributor.distributor_id"),
            col("tariff_code", "text", "tariff code", pk=pk),
            col("effective_from", "date", "first day of the tariff period", pk=pk)]


LOCATOR = ("where in the document: xlsx:<sheet>!<cell>, pdf:p<page> or pdf-ocr:p<page> (grammar in "
           "scripts/tariffdb/locators.py)")


def provenance(null=False):
    return [col("document_id", "text", "source document version", null=null, fk="source_document.document_id"),
            col("locator", "text", LOCATOR, null=null)]


def quoted(null=False):
    return [*provenance(null), col("quote", "text", "verbatim wording at the locator", null=null)]


TARIFF_FK = ("tariff", ["distributor_id", "tariff_code", "effective_from"])

TABLES = [
    {
        "name": "value_list",
        "grain": "one allowed value of one fixed list",
        "source": "generated from scripts/tariffdb/spec.py (VALUE_LISTS)",
        "description": "Every value a controlled column may hold, with its meaning. data_dictionary.value_list names "
                       "the list a column takes its values from.",
        "columns": [
            col("list_name", "text", "name of the list, e.g. charge_type", pk=True),
            col("value", "text", "the value as stored", pk=True),
            col("definition", "text", "what the value means"),
        ],
    },
    {
        "name": "data_dictionary",
        "grain": "one column of one table",
        "source": "generated from scripts/tariffdb/spec.py (TABLES)",
        "description": "Every column of every table: its type, whether it is required, the fixed list or unit it "
                       "takes, and its meaning.",
        "columns": [
            col("table_name", "text", "table", pk=True),
            col("column_name", "text", "column", pk=True),
            col("ordinal", "integer", "position of the column in the table (1 = first)"),
            col("data_type", "text", "text, integer, numeric, date (YYYY-MM-DD), time (HH:MM) or boolean (0/1)"),
            col("required", "boolean", "1 when the column may not be NULL"),
            col("is_primary_key", "boolean", "1 when the column is part of the table's primary key"),
            col("references_table", "text", "table the column refers to (foreign key)", null=True),
            col("value_list", "text", "the value_list.list_name the column's values come from", null=True),
            col("unit", "text", "unit of a numeric column", null=True),
            col("definition", "text", "what the column holds"),
        ],
    },
    {
        "name": "unit",
        "grain": "one stored unit",
        "source": "generated from scripts/tariffdb/spec.py (UNITS)",
        "description": "Every unit a rate may be stored in, what it prices per, and how to reach its standard unit: "
                       "value_std = value x multiplier, or divide by the days of the billed month or year "
                       "(calendar_factor) for a price per month or year. A unit whose billing period no held document "
                       "states has neither.",
        "columns": [
            col("unit", "text", "the unit as stored, e.g. c/kW/month", pk=True),
            col("quantity", "text", "what one unit of the price is charged per", enum="unit_quantity"),
            col("billing_period", "text", "the period the price is charged per; NULL for an energy price",
                null=True, enum="billing_period"),
            col("unit_std", "text", "the standard unit value_std is in: cents per day, per kWh or per kVAh",
                null=True),
            col("multiplier", "numeric", "value_std = value x multiplier; NULL when the calendar decides it",
                null=True),
            col("calendar_factor", "text", "how a price per month or year becomes a price per day", null=True,
                enum="calendar_factor"),
            col("definition", "text", "what the unit means"),
        ],
        "checks": ["(multiplier IS NULL) OR (calendar_factor IS NULL)",
                   "(unit_std IS NULL) = (billing_period IS 'not_stated')"],
    },
    {
        "name": "distributor",
        "grain": "one distributor (DNSP)",
        "source": "reference data in scripts/tariffdb/build_support.py (DISTRIBUTORS)",
        "description": "The electricity distribution network service providers whose tariffs are stored.",
        "columns": [
            col("distributor_id", "text", "slug, e.g. ausgrid", pk=True),
            col("name", "text", "name"),
            col("state", "text", "jurisdiction", enum="state"),
            col("iana_timezone", "text", "time zone of the network area, e.g. Australia/Sydney; TOU times are local "
                "clock times there"),
            col("observes_dst", "boolean", "1 when local clocks move for daylight saving (not QLD, NT)"),
        ],
    },
    {
        "name": "public_holiday",
        "grain": "one public holiday of one state",
        "source": "generated by scripts/tariffdb/build.py from the python-holidays package (version "
                  "build_support.HOLIDAYS_VERSION), for the years the database stores",
        "description": "The public-holiday calendar a distributor's windows treat as public holidays: the holidays of "
                       "its state (distributor.state). How each window set prices them is window_set.public_holidays.",
        "columns": [
            col("state", "text", "jurisdiction", pk=True, enum="state"),
            col("holiday_date", "date", "the day", pk=True),
            col("name", "text", "the holiday's name (several joined with '; ' when they fall on one day)"),
        ],
    },
    {
        "name": "source_document",
        "grain": "one version of one source document, held or not",
        "source": "sources/inventory.csv (2023-24 on) and sources/archive/inventory.csv (earlier years) read by "
                  "build_support.documents(), plus the AER versions it registers",
        "description": "Every document version the rates, TOU windows and criteria are read from, with where it came "
                       "from. A re-issued document is a new row; no version replaces another.",
        "columns": [
            col("document_id", "text", "slug derived from the file name", pk=True),
            col("distributor_id", "text", "distributor whose prices it carries; NULL for an AER report covering every "
                "distributor", null=True, fk="distributor.distributor_id"),
            col("pricing_year", "text", "pricing year: a financial year (2025-26), or the calendar year (2005) or half "
                "year (2021-H1) a regulator priced by (Victoria 2000-H2 to 2021-H1, Tasmania to 2008-H1)",
                enum="pricing_year"),
            col("publisher", "text", "who published the prices", enum="publisher"),
            col("document_type", "text", "kind of publication", enum="document_type"),
            col("hosted_by_aer", "boolean", "1 for a distributor document taken from aer.gov.au rather than the "
                "distributor's own site"),
            col("version_label", "text", "version as published, e.g. v1, v5, 'updated 17 Jul 2024'"),
            col("version_seq", "integer", "order within its publication series (1 = first)"),
            col("price_status", "text", "regulatory status of the prices, as a held source states it",
                enum="price_status"),
            col("published_on", "date", "publication date, when known", null=True),
            col("source_url", "text", "exact URL the file was retrieved from (the landing page when not held)",
                null=True),
            col("local_path", "text", "repo-relative path of the file; NULL when it could not be retrieved",
                null=True),
            col("sha256", "text", "SHA-256 of the file used", null=True),
        ],
        "checks": ["(local_path IS NULL) = (sha256 IS NULL)"],
    },
    {
        "name": "tariff",
        "grain": "one tariff code of one distributor for one period (a pricing year, or part of one after a "
                 "mid-year change)",
        "source": "built by scripts/tariffdb/build.py from the parsed price lists (out/aer_long.csv, out/dnsp/*.csv, "
                  "out/history/*.csv)",
        "description": "A network tariff code in effect for a period, with its name and customer class as published "
                       "and whether its rates are provisional (AER) or final (the distributor's own list). A tariff "
                       "with no rate rows is one its document lists with every price zero.",
        "columns": [
            col("distributor_id", "text", "distributor", pk=True, fk="distributor.distributor_id"),
            col("tariff_code", "text", "tariff code as the source document prints it", pk=True),
            col("effective_from", "date", "first day the tariff applies", pk=True),
            col("effective_to", "date", "last day the tariff applies (inclusive)"),
            col("tariff_name", "text", "name as published", null=True),
            col("customer_class", "text", "the customer class the published heading names; NULL when it names none",
                null=True, enum="customer_class"),
            col("customer_class_published", "text", "tariff class or customer class heading as published",
                null=True),
            col("pricing_basis", "text", "how the prices apply: trial or site_specific when the published heading "
                "says so, else published; NULL for a tariff with no rates", null=True, enum="pricing_basis"),
            col("status", "text", "provisional = rates from the AER's report or a proposal; final = rates from the "
                "distributor's own published price list, or the schedule a state regulator published",
                enum="status"),
            col("document_id", "text", "document the tariff and its rates are read from",
                fk="source_document.document_id"),
        ],
        "checks": ["effective_from <= effective_to", "customer_class IS NULL OR customer_class_published IS NOT NULL"],
    },
    {
        "name": "tariff_assignment",
        "grain": "one quoted statement of how customers come to be on one tariff, for one period",
        "source": "data/tariffdb/curated/*.yaml (eligibility facts with rule_type assignment), quoted from the "
                  "distributor's documents",
        "description": "Whether a tariff is the default, opt-in, opt-out, mandatory or assigned by the distributor, "
                       "and for whom the statement says so. A tariff may be the default for one group and opt-in for "
                       "another; every published statement is kept.",
        "columns": [
            *tariff_key(pk=True),
            col("assignment_no", "integer", "statement number within the tariff period (1 = first)", pk=True),
            col("assignment", "text", "how customers come to be on the tariff", enum="assignment"),
            col("applies_to", "text", "the customers the statement names; NULL = it names no group", null=True,
                enum="assignment_group"),
            *quoted(),
        ],
        "foreign_keys": [TARIFF_FK],
        "checks": ["assignment_no > 0"],
    },
    {
        "name": "tariff_link",
        "grain": "one stated relation of one tariff period to another tariff code or customer class",
        "source": "built by scripts/tariffdb/build.py: alias and zone_variant_of from the AER spellings the build "
                  "stores under the distributor's code (data/tariffdb/code_alias.csv); the rest from "
                  "data/tariffdb/curated/*.yaml, quoted",
        "description": "How a tariff relates to others: the AER's spelling of it, the tariff a customer may opt out "
                       "to, the tariff it replaces, a primary tariff it must sit beside, a tariff it may not be "
                       "combined with ... linked_code is a code as printed and need not be a stored tariff.",
        "columns": [
            *tariff_key(pk=True),
            col("link_no", "integer", "link number within the tariff period (1 = first)", pk=True),
            col("link_type", "text", "the relation", enum="link_type"),
            col("linked_code", "text", "the other tariff code as printed; NULL when the link names a class or every "
                "other tariff", null=True),
            col("linked_customer_class", "text", "the customer class whose tariffs the link names", null=True,
                enum="customer_class"),
            *quoted(null=True),
            col("note", "text", "the code_alias.csv rule, or an exception the statement names", null=True),
        ],
        "foreign_keys": [TARIFF_FK],
        "checks": ["link_no > 0",
                   "linked_code IS NOT NULL OR linked_customer_class IS NOT NULL "
                   "OR link_type IN ('cannot_combine', 'secondary_of', 'primary_of')",
                   "linked_customer_class IS NULL OR linked_code IS NULL",
                   "(locator IS NULL) = (quote IS NULL)",
                   "quote IS NOT NULL OR link_type IN ('alias', 'zone_variant_of')",
                   "document_id IS NOT NULL OR quote IS NULL"],
    },
    {
        "name": "rate",
        "grain": "one price of one tariff for one period: charge type x TOU period x season x block",
        "source": "built by scripts/tariffdb/build.py from the parsed price lists (total network price, GST "
                  "exclusive) and, for block bounds and stated units, data/tariffdb/curated/*.yaml",
        "description": "The network price charged for one component of a tariff: the total network price (no "
                       "DUoS/TUoS/jurisdictional breakdown), GST exclusive, in a unit of the unit table next to the "
                       "value as published.",
        "columns": [
            col("rate_id", "text", "<distributor_id>:<tariff_code>:<effective_from>:<charge_type>:<component>"
                "[:<tou_period>][:<season>][:<block>][:<region>]", pk=True),
            *tariff_key(),
            col("charge_type", "text", "daily = fixed charge per day; usage = per kWh or kVAh; demand / capacity = per "
                "kW or kVA; export = per exported kWh or kW (negative = a reward paid); metering = metering charge; "
                "other", enum="charge_type"),
            col("tou_period", "text", "time-of-use period the price applies in (anytime = all times); NULL for daily "
                "and metering charges", null=True, enum="rate_period"),
            col("season", "text", "season the price applies in; NULL = all year", null=True, enum="season"),
            col("block", "integer", "consumption block number (1 = first) of a stepped price; NULL otherwise",
                null=True),
            col("block_from", "numeric", "lower bound of the block, from the curated block ladder", null=True,
                unit="see block_unit"),
            col("block_to", "numeric", "upper bound of the block; NULL = unbounded or not stated", null=True,
                unit="see block_unit"),
            col("block_unit", "text", "unit and reset period of the bounds", null=True, enum="block_unit"),
            col("region", "text", "pricing zone, when the document prices one code by zone", null=True),
            col("register", "text", "meter register the quantity is measured on: general = general-supply import, "
                "controlled_load = a separately metered controlled-load circuit, export = energy sent out; NULL for "
                "daily, metering and other charges", null=True, enum="register"),
            col("value", "numeric", "price in the rate's unit", unit="see unit"),
            col("unit", "text", "unit of value (the unit table says what it prices per and how to reach its "
                "standard unit)", fk="unit.unit"),
            col("value_published", "text", "number exactly as printed"),
            col("unit_published", "text", "unit exactly as printed", null=True),
            col("component", "text", "component label as printed"),
            col("locator", "text", "where in the tariff's document: " + LOCATOR),
            col("note", "text", "caveat from the source or the parser", null=True),
        ],
        "foreign_keys": [TARIFF_FK],
        "checks": ["block IS NULL OR block > 0",
                   "block_from IS NULL OR block_to IS NULL OR block_from < block_to",
                   "(register IS NULL) = (charge_type IN ('daily', 'metering', 'other'))",
                   "charge_type != 'export' OR register = 'export'"],
    },
    {
        "name": "rate_condition",
        "grain": "one value of one condition a rate is charged under",
        "source": "data/tariffdb/curated/*.yaml (conditions, metering), quoted from the distributor's documents",
        "description": "A rate with condition rows is charged only at a site that meets, for each condition kind it "
                       "names, one of that kind's values (values of one kind are alternatives). A rate with none is "
                       "always charged.",
        "columns": [
            col("rate_id", "text", "the rate", pk=True, fk="rate.rate_id"),
            col("condition_kind", "text", "what the site must have or do", pk=True, enum="condition_kind"),
            col("value", "text", "opt_in: the offer's name; meter_type: a meter_type_value; meter_class: the class in "
                "the distributor's metering schedule (lower case, underscores)", pk=True),
            *quoted(),
        ],
        "checks": ["value GLOB '[a-z0-9]*' AND value NOT GLOB '*[^a-z0-9_]*'",
                   "condition_kind != 'meter_type' OR value IN (" +
                   ", ".join(repr(v) for v in CRITERION_VALUES["meter_type"]) + ")"],
    },
    {
        "name": "window_set",
        "grain": "one stated time-of-use schedule: the windows one passage of one document sets out",
        "source": "data/tariffdb/curated/*.yaml (tou_schedules), as stated in the distributor's documents",
        "description": "A named set of time windows, with the clock its times refer to and how public holidays are "
                       "priced, as the document states them. Tariffs use it through tariff_window_set; its windows "
                       "are in time_window and its seasons in season.",
        "columns": [
            col("window_set_id", "text", "slug, unique across distributors (the curated schedule id)", pk=True),
            col("distributor_id", "text", "distributor", fk="distributor.distributor_id"),
            col("name", "text", "what the windows are for, as the curator names them"),
            col("covers_full_day", "boolean", "1 when the windows partition each day type they name over 24 hours"),
            *quoted(),
            col("time_basis", "text", "clock the times refer to, as stated; NULL = no held document states it",
                null=True, enum="time_basis"),
            col("time_basis_document_id", "text", "document stating the time basis", null=True,
                fk="source_document.document_id"),
            col("time_basis_locator", "text", "where it states it", null=True),
            col("time_basis_quote", "text", "verbatim wording at that locator", null=True),
            col("public_holidays", "text", "how public holidays are priced, as stated; NULL = no held document "
                "says", null=True, enum="holiday_rule"),
            col("public_holidays_document_id", "text", "document stating the holiday rule", null=True,
                fk="source_document.document_id"),
            col("public_holidays_locator", "text", "where it states it", null=True),
            col("public_holidays_quote", "text", "verbatim wording at that locator", null=True),
            col("note", "text", "caveat from the curator", null=True),
        ],
        "checks": [f"({k} IS NULL) = ({k}_{x} IS NULL)" for k in ("time_basis", "public_holidays")
                   for x in ("document_id", "locator", "quote")],
    },
    {
        "name": "season",
        "grain": "one season of one window set: a named rate season, or the months windows apply in",
        "source": "data/tariffdb/curated/*.yaml (the months and season of each window)",
        "description": "The part of the year a window set's windows apply in. season is the rate.season it prices "
                       "(NULL = the windows belong to no rate season); its dates are season_part rows. A season with no "
                       "parts is one the document names without its dates.",
        "columns": [
            col("season_id", "text", "<window_set_id>:<season or any>:<months, dst, not-dst or months-not-stated>",
                pk=True),
            col("window_set_id", "text", "window set", fk="window_set.window_set_id"),
            col("season", "text", "the rate.season the windows price; NULL = no rate season", null=True,
                enum="season"),
            col("season_label", "text", "season name as published", null=True),
        ],
        "checks": ["season IS NULL OR season_label IS NOT NULL"],
    },
    {
        "name": "season_part",
        "grain": "one span of dates of one season, repeating every year",
        "source": "data/tariffdb/curated/*.yaml (the months of each window)",
        "description": "A span of a season, from a start day to an end day (inclusive), each a calendar day or the day "
                       "daylight saving starts or ends. A span may wrap the year end (1 November to 31 March).",
        "columns": [
            col("season_id", "text", "season", pk=True, fk="season.season_id"),
            col("part_no", "integer", "span number within the season (1 = first)", pk=True),
            col("start_month", "integer", "month of the first day; NULL with start_anchor", null=True),
            col("start_day", "integer", "day of month of the first day", null=True),
            col("start_anchor", "text", "the first day is a daylight-saving changeover", null=True,
                enum="season_anchor"),
            col("end_month", "integer", "month of the last day; NULL with end_anchor", null=True),
            col("end_day", "integer", "day of month of the last day (29 for February: the last day in any year)",
                null=True),
            col("end_anchor", "text", "the span ends the day before this daylight-saving changeover", null=True,
                enum="season_anchor"),
        ],
        "checks": ["part_no > 0", "(start_month IS NULL) = (start_day IS NULL)",
                   "(end_month IS NULL) = (end_day IS NULL)",
                   "(start_month IS NULL) = (start_anchor IS NOT NULL)", "(end_month IS NULL) = (end_anchor IS NOT NULL)",
                   "start_month IS NULL OR start_month BETWEEN 1 AND 12",
                   "end_month IS NULL OR end_month BETWEEN 1 AND 12",
                   "start_day IS NULL OR start_day BETWEEN 1 AND 31", "end_day IS NULL OR end_day BETWEEN 1 AND 31"],
    },
    {
        "name": "time_window",
        "grain": "one time window of one window set",
        "source": "data/tariffdb/curated/*.yaml (tou_schedules windows)",
        "description": "When one time-of-use period applies: day type, start and end time, in a season of the set. "
                       "Times are local clock times as the document states them (window_set.time_basis).",
        "columns": [
            col("window_id", "text", "<season_id>:<tou_period>:<day_type>:<start>-<end>", pk=True),
            col("window_set_id", "text", "window set", fk="window_set.window_set_id"),
            col("season_id", "text", "the season the window applies in", fk="season.season_id"),
            col("tou_period", "text", "the rate.tou_period the window prices (peak, off_peak, solar_soak ...); "
                "controlled_load_supply = the hours a controlled-load circuit is switched on (prices nothing)",
                enum="tou_period"),
            col("period_label", "text", "period name as published"),
            col("day_type", "text", "days the window applies on", enum="day_type"),
            col("start_time", "time", "inclusive"),
            col("end_time", "time", "exclusive; 24:00 = midnight at the end of the day"),
            col("locator", "text", "where the window is stated, when not at the set's locator", null=True),
            col("quote", "text", "verbatim wording at that locator, when the curator quoted it", null=True),
        ],
        "checks": ["start_time < end_time", "quote IS NULL OR locator IS NOT NULL"],
    },
    {
        "name": "tariff_window_set",
        "grain": "one window set used by one charge group of one tariff period",
        "source": "data/tariffdb/curated/*.yaml (tou_schedules tariffs)",
        "description": "Which window sets say when a tariff's usage, demand, export or controlled-load rates apply.",
        "columns": [
            *tariff_key(pk=True),
            col("window_set_id", "text", "window set", pk=True, fk="window_set.window_set_id"),
            col("applies_to", "text", "which charges of the tariff the set's windows price", pk=True,
                enum="tou_applies"),
            col("locator", "text", "where the document names the tariff for the set, when not at the set's locator",
                null=True),
            col("quote", "text", "verbatim wording at that locator", null=True),
        ],
        "foreign_keys": [TARIFF_FK],
        "checks": ["quote IS NULL OR locator IS NOT NULL"],
    },
    {
        "name": "eligibility",
        "grain": "one stated criterion of one tariff for one period",
        "source": "data/tariffdb/curated/*.yaml (eligibility), quoted from the distributor's documents",
        "description": "Who can be on the tariff: customer type, voltage, consumption or demand thresholds, meter "
                       "type, availability, required technology. How customers are assigned is tariff_assignment.",
        "columns": [
            col("criterion_id", "text", "<distributor_id>:<tariff_code>:<effective_from>:<criterion>:<n>", pk=True),
            *tariff_key(),
            col("criterion_group", "integer", "alternative routes onto the tariff: a site qualifies when every "
                "criterion of one group holds (criteria of one kind within a group are alternatives); 1 when the "
                "document states no alternatives"),
            col("criterion", "text", "what is constrained", enum="criterion"),
            col("operator", "text", "comparison for a numeric threshold", null=True, enum="operator"),
            col("value_num", "numeric", "threshold", null=True, unit="see value_unit"),
            col("value_unit", "text", "unit of the threshold", null=True, enum="threshold_unit"),
            col("value_text", "text", "categorical value (residential, LV, interval, closed_to_new ...); the "
                "<criterion>_value list for a categorical criterion, as stated for other", null=True),
            *quoted(),
        ],
        "foreign_keys": [TARIFF_FK],
        "checks": ["criterion_group > 0", "value_num IS NOT NULL OR value_text IS NOT NULL",
                   "(value_num IS NULL) = (operator IS NULL)", "(value_num IS NULL) = (value_unit IS NULL)",
                   *(f"criterion != '{c}' OR value_text IS NULL OR value_text IN "
                     f"({', '.join(repr(v) for v in vs)})" for c, vs in CRITERION_VALUES.items())],
    },
]

TABLES.append({
    "name": "charge_rule",
    "grain": "one stated measurement rule of one tariff's demand, capacity or export charges, for one period",
    "source": "data/tariffdb/curated/*.yaml (charge_rules), quoted from the distributor's documents",
    "description": "How the quantity a demand, capacity or export rate is applied to is measured: in kW or kVA, over "
                   "which interval, highest or an average of the highest, reset when, with any minimum, threshold or "
                   "free allowance the document states.",
    "columns": [
        col("rule_id", "text", "<distributor_id>:<tariff_code>:<effective_from>:<charge_type>:<tou_period or "
            "all>:<season or all>:<measure> (a tariff priced both per kW and per kVA has a rule for each)", pk=True),
        *tariff_key(),
        col("charge_type", "text", "the rates the rule measures for", enum="rule_charge"),
        col("tou_period", "text", "the rate.tou_period it measures for; NULL = every rate of the charge type",
            null=True, enum="rate_period"),
        col("season", "text", "the rate.season it measures for; NULL = every season", null=True, enum="season"),
        col("measure", "text", "quantity measured", enum="rule_measure"),
        col("interval_min", "integer", "length of the metering interval the demand is averaged over, minutes",
            null=True, unit="minutes"),
        col("method", "text", "how the quantity is taken from the intervals (value_list rule_method)",
            enum="rule_method"),
        col("n", "integer", "n of the avg_top_n methods and of avg_nominated_days", null=True),
        col("reset", "text", "span the measured value is taken over before it starts again", enum="rule_reset"),
        col("lookback_months", "integer", "months a rolling_months reset looks back over, the billing month "
            "included", null=True, unit="months"),
        col("minimum_value", "numeric", "smallest quantity charged, when stated", null=True, unit="see minimum_unit"),
        col("minimum_unit", "text", "unit of minimum_value", null=True, enum="rule_measure"),
        col("threshold_value", "numeric", "the charge applies only to the quantity above this, when stated "
            "(e.g. export above 1.5 kW)", null=True, unit="see threshold_unit"),
        col("threshold_unit", "text", "unit of threshold_value", null=True, enum="rule_measure"),
        col("allowance_per_day", "numeric", "free quantity per day before the charge applies, when stated", null=True,
            unit="see measure"),
        col("allowance_rollover", "boolean", "1 when an unused daily allowance carries over within the billing period",
            null=True),
        *quoted(),
        col("note", "text", "caveat from the curator", null=True),
    ],
    "foreign_keys": [TARIFF_FK],
    "checks": [f"(n IS NULL) = (method NOT IN ({', '.join(repr(m) for m in RULE_N_METHODS)}))",
               "allowance_rollover IS NULL OR allowance_per_day IS NOT NULL",
               "(lookback_months IS NULL) = (reset != 'rolling_months')", "lookback_months IS NULL OR lookback_months > 0",
               "(minimum_value IS NULL) = (minimum_unit IS NULL)", "(threshold_value IS NULL) = (threshold_unit IS NULL)",
               "minimum_unit IS NOT 'kva_else_kw' AND threshold_unit IS NOT 'kva_else_kw'"],
})

TABLE_ORDER = [t["name"] for t in TABLES]
BY_NAME = {t["name"]: t for t in TABLES}
SQL_TYPES = {"text": "TEXT", "integer": "INTEGER", "numeric": "NUMERIC", "date": "TEXT", "time": "TEXT",
             "boolean": "INTEGER"}


def value_list_rows():
    return [{"list_name": name, "value": v, "definition": d} for name, vs in VALUE_LISTS.items()
            for v, d in vs.items()]


def data_dictionary_rows():
    return [{"table_name": t["name"], "column_name": c["name"], "ordinal": i, "data_type": c["type"],
             "required": int(not c["nullable"]), "is_primary_key": int(c["primary_key"]),
             "references_table": (c["references"] or "").split(".")[0] or next(
                 (rt for rt, cols in t.get("foreign_keys", []) if c["name"] in cols), None),
             "value_list": c["enum"], "unit": c["unit"], "definition": c["description"]}
            for t in TABLES for i, c in enumerate(t["columns"], 1)]


def unit_rows():
    return [{"unit": u, "quantity": q, "billing_period": p, "unit_std": std, "multiplier": m, "calendar_factor": cf,
             "definition": d} for u, (q, p, std, m, cf, d) in UNITS.items()]


def ddl():
    out = ["-- Generated from scripts/tariffdb/spec.py by scripts/tariffdb/build.py; do not edit by hand.",
           "-- SQLite. Tables are in foreign-key order."]
    for t in TABLES:
        lines = []
        for c in t["columns"]:
            n = c["name"]
            s = f"  {n} {SQL_TYPES[c['type']]}" + ("" if c["nullable"] else " NOT NULL")
            if c["enum"]:
                s += f" CHECK ({n} IN ({', '.join(repr(v) for v in VALUE_LISTS[c['enum']])}))"
            if c["type"] == "boolean":
                s += f" CHECK ({n} IN (0, 1))"
            if c["type"] == "integer":
                s += f" CHECK (typeof({n}) IN ('integer', 'null'))"
            if c["type"] == "numeric":
                s += f" CHECK (typeof({n}) IN ('integer', 'real', 'null'))"
            if c["type"] == "date":
                s += f" CHECK ({n} IS NULL OR {n} GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]')"
            if c["type"] == "time":
                s += f" CHECK ({n} GLOB '[0-2][0-9]:[0-5][0-9]' AND {n} <= '24:00')"
            lines.append(s)
        lines.append(f"  PRIMARY KEY ({', '.join(c['name'] for c in t['columns'] if c['primary_key'])})")
        for c in t["columns"]:
            if c["references"]:
                rt, rc = c["references"].split(".")
                lines.append(f"  FOREIGN KEY ({c['name']}) REFERENCES {rt} ({rc})")
        for rt, cols in t.get("foreign_keys", []):
            lines.append(f"  FOREIGN KEY ({', '.join(cols)}) REFERENCES {rt} ({', '.join(cols)})")
        for ch in t.get("checks", []):
            lines.append(f"  CHECK ({ch})")
        out.append(f"\nCREATE TABLE {t['name']} (\n" + ",\n".join(lines) + "\n);")
    return "\n".join(out) + "\n"


def json_spec():
    return {"generated_from": "scripts/tariffdb/spec.py", "tables": [
        {"name": t["name"], "grain": t["grain"], "source": t["source"], "description": t["description"],
         "file": f"tables/{t['name']}.csv", "primary_key": [c["name"] for c in t["columns"] if c["primary_key"]],
         "foreign_keys": [{"columns": cols, "references": rt} for rt, cols in t.get("foreign_keys", [])],
         "checks": t.get("checks", []), "columns": t["columns"]} for t in TABLES]}
