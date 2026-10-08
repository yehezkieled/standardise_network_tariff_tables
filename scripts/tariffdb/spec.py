"""Single source of truth for the tariff database schema: six tables of final network rates per tariff code.

`data/tariffdb/schema.sqlite.sql` (DDL) and `data/tariffdb/schema.json` (machine-readable table spec) are generated from
TABLES by `scripts/tariffdb/build.py`; docs/schema.md and docs/schema-erd.svg by `scripts/tariffdb/schema_doc.py`. The
tests fail when any of them is stale.

Column types: text, integer, numeric, date ('YYYY-MM-DD'), time ('HH:MM', end-exclusive, '24:00' allowed), boolean
(0/1). Every enum is a CHECK constraint. An empty CSV field is NULL.
"""

LAST_FIN_YEAR = 2026  # 2026-27; a new year: raise it
# pricing years: financial years (2025-26); Victoria priced by calendar year from 2001 to 2020 (with half years 2000-H2
# and 2021-H1 joining financial years on either side) and Tasmania until 2007 (2008-H1 before financial years)
FIN_YEARS = [f"{y}-{(y + 1) % 100:02d}" for y in range(1996, LAST_FIN_YEAR + 1)]
PRICING_YEARS = FIN_YEARS + [str(y) for y in range(2000, 2021)] + ["2000-H2", "2008-H1", "2021-H1"]
STATES = ["NSW", "VIC", "QLD", "SA", "TAS", "ACT", "NT"]
STATUSES = ["provisional", "final"]
PUBLISHERS = ["AER", "distributor", "regulator"]  # regulator: a state regulator before the AER (ESC, ESCOSA, QCA ...)
DOCUMENT_TYPES = [
    "aer_consolidated_stakeholder_report", "aer_stakeholder_report", "aer_landing_page", "pricing_proposal",
    "pricing_proposal_overview", "price_list", "tariff_summary", "tariff_schedule", "schedule_of_charges",
    "statement_of_tariff_classes", "price_guide", "pricing_schedule", "annual_tariff_report", "pricing_model",
]
# unverified: a distributor document hosted by the AER whose regulatory status no held source states; mixed: one AER
# version carrying approved prices for some distributors and proposed for others
PRICE_STATUS = ["proposed", "approved", "mixed", "published", "unverified"]
CHARGE_TYPES = ["daily", "usage", "demand", "capacity", "export", "metering", "other"]
# rate.tou_period: the time-of-use period a price applies in (anytime when it has none), plus the few non-period price
# bands distributors publish (capacity_minimum/remaining, critical_minimum, dynamic_minimum/maximum)
RATE_PERIODS = ["anytime", "peak", "shoulder", "off_peak", "super_off_peak", "critical_peak", "solar_soak",
                "capacity_minimum", "capacity_remaining", "critical_minimum", "dynamic_maximum", "dynamic_minimum"]
SEASONS = ["summer", "non_summer", "high", "low", "winter", "spring", "autumn"]
DAY_TYPES = ["weekday", "weekend", "all_days", "business_day", "non_business_day"]
# tou_window.tou_period: the rate vocabulary, so a window joins the rates it prices on (charge group, tou_period, season);
# controlled_load_supply is the one window that prices nothing: the hours a controlled-load circuit is switched on
TOU_PERIODS = RATE_PERIODS + ["controlled_load_supply"]
# rate.register: the meter register the priced quantity is measured on (NULL for daily, metering and other charges)
REGISTERS = ["general", "controlled_load", "export"]
# rate.condition: NULL = always charged; '<kind>:<value>' = charged only when the site meets it (opt_in:diversify,
# meter_type:accumulation); the curated YAML quotes the source for each
CONDITION_KINDS = ["opt_in", "meter_type"]
# daylight_time: the source states the times in daylight-saving time (e.g. 'ADST'); times are stored as stated
TIME_BASES = ["local_time", "standard_time", "daylight_time", "not_stated"]
HOLIDAY_RULES = ["as_weekday", "as_non_business_day", "not_stated", "unchanged"]
TOU_APPLIES = ["usage", "demand", "export", "controlled_load", "all"]
CRITERIA = [
    "customer_type", "voltage_level", "consumption_min", "consumption_max", "demand_min", "demand_max",
    "meter_type", "assignment", "availability", "requires_technology", "opt_out_to", "minimum_demand_charge", "other",
]
# ge_unstated / le_unstated: a lower / upper bound whose source does not say whether the boundary value is included
OPERATORS = ["eq", "lt", "le", "gt", "ge", "ge_unstated", "le_unstated"]
# value_text vocabulary of the categorical criteria (the others take free text)
CRITERION_VALUES = {
    "customer_type": ["residential", "small_business", "medium_business", "large_business", "business",
                      "unmetered", "public_lighting", "embedded_generation", "controlled_load", "storage",
                      "ev_charging", "any"],
    "voltage_level": ["LV", "HV", "subtransmission", "transmission", "zone_substation"],
    "meter_type": ["interval", "smart", "basic", "accumulation", "unmetered", "any"],
    "assignment": ["default", "opt_in", "opt_out", "mandatory", "assigned_by_distributor", "retailer_request"],
    "availability": ["open", "closed_to_new", "withdrawn", "obsolete", "trial", "grandfathered", "transitional"],
    "requires_technology": ["solar", "battery", "ev", "controlled_load_device", "dedicated_circuit", "export_capable",
                            "storage", "flexible_load", "heat_pump"],
}
BLOCK_UNITS = ["kWh/day", "kWh/billing_day", "kWh/quarter", "kWh"]  # kWh: the source states no reset period
# charge_rule: how a demand, capacity or export quantity is measured before its rate applies
RULE_CHARGES = ["demand", "capacity", "export"]
RULE_MEASURES = ["kW", "kVA", "kWh"]
# max: the highest interval; avg_top_n_days: the mean of the n highest daily maxima; avg_top_n_intervals: the mean of
# the n highest intervals; agreed: a value agreed with the distributor, not measured; max_of_agreed_and_measured: the
# greater of the agreed value and the highest interval
RULE_METHODS = ["max", "avg_top_n_days", "avg_top_n_intervals", "agreed", "max_of_agreed_and_measured"]
# the span the measured value is taken over before it starts again
RULE_RESETS = ["day", "month", "billing_period", "season", "year", "rolling_12_months"]


def col(name, type_, desc, *, null=False, pk=False, fk=None, enum=None, unit=None):
    return {"name": name, "type": type_, "nullable": null, "primary_key": pk, "references": fk, "enum": enum,
            "unit": unit, "description": desc}


def period(what):
    return [col("effective_from", "date", f"first day {what} applies"),
            col("effective_to", "date", f"last day {what} applies (inclusive)")]


def provenance():
    return [col("document_id", "text", "source document version", fk="source_document.document_id"),
            col("locator", "text", "where in the document: xlsx:<sheet>!<cell>, pdf:p<page> or pdf-ocr:p<page> "
                "(grammar in scripts/tariffdb/locators.py)")]


TARIFF_FK = ("tariff", ["distributor_id", "tariff_code", "effective_from"])

TABLES = [
    {
        "name": "distributor",
        "grain": "one distributor (DNSP)",
        "source": "reference data in scripts/tariffdb/build_support.py (DISTRIBUTORS)",
        "description": "The electricity distribution network service providers whose tariffs are stored.",
        "columns": [
            col("distributor_id", "text", "slug, e.g. ausgrid", pk=True),
            col("name", "text", "name"),
            col("state", "text", "jurisdiction", enum=STATES),
            col("iana_timezone", "text", "time zone of the network area, e.g. Australia/Sydney; TOU times are local "
                "clock times there"),
            col("observes_dst", "boolean", "1 when local clocks move for daylight saving (not QLD, NT)"),
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
                enum=PRICING_YEARS),
            col("publisher", "text", "who published the prices", enum=PUBLISHERS),
            col("document_type", "text", "kind of publication", enum=DOCUMENT_TYPES),
            col("hosted_by_aer", "boolean", "1 for a distributor document taken from aer.gov.au rather than the "
                "distributor's own site"),
            col("version_label", "text", "version as published, e.g. v1, v5, 'updated 17 Jul 2024'"),
            col("version_seq", "integer", "order within its publication series (1 = first)"),
            col("price_status", "text", "regulatory status of the prices, as a held source states it",
                enum=PRICE_STATUS),
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
            col("customer_class", "text", "tariff class or customer class heading as published", null=True),
            col("status", "text", "provisional = rates from the AER's report or a proposal; final = rates from the "
                "distributor's own published price list, or the schedule a state regulator published", enum=STATUSES),
            col("document_id", "text", "document the tariff and its rates are read from",
                fk="source_document.document_id"),
        ],
        "checks": ["effective_from <= effective_to"],
    },
    {
        "name": "rate",
        "grain": "one price of one tariff for one period: charge type x TOU period x season x block",
        "source": "built by scripts/tariffdb/build.py from the parsed price lists (total network price, GST "
                  "exclusive) and, for block bounds, data/tariffdb/curated/*.yaml",
        "description": "The network price charged for one component of a tariff: the total network price (no "
                       "DUoS/TUoS/jurisdictional breakdown), GST exclusive, in standard units next to the value as "
                       "published.",
        "columns": [
            col("rate_id", "text", "<distributor_id>:<tariff_code>:<effective_from>:<component>[:<region>]", pk=True),
            col("distributor_id", "text", "distributor", fk="distributor.distributor_id"),
            col("tariff_code", "text", "tariff code"),
            *period("the price"),
            col("charge_type", "text", "daily = fixed charge per day; usage = per kWh or kVAh; demand / capacity = per "
                "kW or kVA; export = per exported kWh or kW (negative = a reward paid); metering = metering charge; "
                "other", enum=CHARGE_TYPES),
            col("tou_period", "text", "time-of-use period the price applies in (anytime = all times); NULL for daily "
                "and metering charges", null=True, enum=RATE_PERIODS),
            col("season", "text", "season the price applies in; NULL = all year", null=True, enum=SEASONS),
            col("block", "integer", "consumption block number (1 = first) of a stepped price; NULL otherwise",
                null=True),
            col("block_from", "numeric", "lower bound of the block, from the curated block ladder", null=True),
            col("block_to", "numeric", "upper bound of the block; NULL = unbounded or not stated", null=True),
            col("block_unit", "text", "unit and reset period of the bounds", null=True, enum=BLOCK_UNITS),
            col("region", "text", "pricing zone, when the document prices one code by zone", null=True),
            col("register", "text", "meter register the quantity is measured on: general = general-supply import, "
                "controlled_load = a separately metered controlled-load circuit, export = energy sent out; NULL for "
                "daily, metering and other charges", null=True, enum=REGISTERS),
            col("condition", "text", "NULL = always charged; opt_in:<name> = only for a customer who opts in to "
                "<name>; meter_type:<type> = only at a site with that meter (curated, quoted in "
                "data/tariffdb/curated/*.yaml)", null=True),
            col("value", "numeric", "price in standard units", unit="see unit"),
            col("unit", "text", "standard unit: c/day, c/kWh, c/kVAh, c/kW/day, c/kW/month, c/kVA/month ... (? = "
                "billing period not stated)"),
            col("value_published", "text", "number exactly as printed"),
            col("unit_published", "text", "unit exactly as printed", null=True),
            col("component", "text", "component label as printed"),
            col("status", "text", "provisional or final (the tariff's status)", enum=STATUSES),
            *provenance(),
            col("note", "text", "caveat from the source or the parser", null=True),
        ],
        "foreign_keys": [TARIFF_FK],
        "checks": ["effective_from <= effective_to", "block IS NULL OR block > 0",
                   "block_from IS NULL OR block_to IS NULL OR block_from < block_to",
                   "(register IS NULL) = (charge_type IN ('daily', 'metering', 'other'))",
                   "charge_type != 'export' OR register = 'export'",
                   "condition IS NULL OR " + " OR ".join(f"condition GLOB '{k}:[a-z0-9]*'" for k in CONDITION_KINDS)],
    },
    {
        "name": "tou_window",
        "grain": "one time window that one tariff's charges use, for one period",
        "source": "data/tariffdb/curated/*.yaml (tou_schedules), as stated in the distributor's documents",
        "description": "When each time-of-use period applies: day type, start and end time, months. Times are local "
                       "clock times as the document states them.",
        "columns": [
            col("window_id", "text", "<distributor_id>:<tariff_code>:<effective_from>:<applies_to>:<tou_period>:"
                "<day_type>:<start>-<end>:<months>", pk=True),
            col("distributor_id", "text", "distributor", fk="distributor.distributor_id"),
            col("tariff_code", "text", "tariff code"),
            *period("the window"),
            col("applies_to", "text", "which charges of the tariff the window prices", enum=TOU_APPLIES),
            col("tou_period", "text", "the rate.tou_period the window prices (peak, off_peak, solar_soak ...); "
                "controlled_load_supply = the hours a controlled-load circuit is switched on (prices nothing)",
                enum=TOU_PERIODS),
            col("period_label", "text", "period name as published"),
            col("day_type", "text", "days the window applies on", enum=DAY_TYPES),
            col("start_time", "time", "inclusive"),
            col("end_time", "time", "exclusive; 24:00 = midnight at the end of the day"),
            col("months", "text", "comma-separated months 1-12; NULL when the source names a season without its "
                "months", null=True),
            col("season", "text", "the rate.season the window belongs to; NULL = every season", null=True,
                enum=SEASONS),
            col("season_label", "text", "season name as published", null=True),
            col("time_basis", "text", "clock the times refer to, as stated", enum=TIME_BASES),
            col("public_holidays", "text", "how public holidays are treated", enum=HOLIDAY_RULES),
            *provenance(),
        ],
        "foreign_keys": [TARIFF_FK],
        "checks": ["effective_from <= effective_to", "start_time < end_time",
                   "months IS NOT NULL OR season_label IS NOT NULL", "season IS NULL OR season_label IS NOT NULL"],
    },
    {
        "name": "eligibility",
        "grain": "one stated criterion of one tariff for one period",
        "source": "data/tariffdb/curated/*.yaml (eligibility), quoted from the distributor's documents",
        "description": "Who can or must be on the tariff: customer type, voltage, consumption or demand thresholds, "
                       "meter type, assignment (default, opt-in, opt-out), availability, required technology.",
        "columns": [
            col("criterion_id", "text", "<distributor_id>:<tariff_code>:<effective_from>:<criterion>:<n>", pk=True),
            col("distributor_id", "text", "distributor", fk="distributor.distributor_id"),
            col("tariff_code", "text", "tariff code"),
            *period("the criterion"),
            col("criterion", "text", "what is constrained", enum=CRITERIA),
            col("operator", "text", "comparison for a numeric threshold", null=True, enum=OPERATORS),
            col("value_num", "numeric", "threshold", null=True),
            col("value_unit", "text", "unit of the threshold (MWh/yr, kVA, kW, kV ...)", null=True),
            col("value_text", "text", "categorical value (residential, LV, interval, default, opt_in ...)", null=True),
            col("target_tariff_code", "text", "tariff referred to (opt-out target, required companion)", null=True),
            *provenance(),
            col("quote", "text", "verbatim wording at the locator"),
        ],
        "foreign_keys": [TARIFF_FK],
        "checks": ["effective_from <= effective_to",
                   "value_num IS NOT NULL OR value_text IS NOT NULL OR target_tariff_code IS NOT NULL",
                   "(value_num IS NULL) = (operator IS NULL)"],
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
            "all>:<season or all>", pk=True),
        col("distributor_id", "text", "distributor", fk="distributor.distributor_id"),
        col("tariff_code", "text", "tariff code"),
        *period("the rule"),
        col("charge_type", "text", "the rates the rule measures for", enum=RULE_CHARGES),
        col("tou_period", "text", "the rate.tou_period it measures for; NULL = every rate of the charge type",
            null=True, enum=RATE_PERIODS),
        col("season", "text", "the rate.season it measures for; NULL = every season", null=True, enum=SEASONS),
        col("measure", "text", "quantity measured", enum=RULE_MEASURES),
        col("interval_min", "integer", "length of the metering interval the demand is averaged over, minutes",
            null=True),
        col("method", "text", "max = the highest interval; avg_top_n_days = the mean of the n highest daily maxima; "
            "avg_top_n_intervals = the mean of the n highest intervals; agreed = a value agreed with the distributor; "
            "max_of_agreed_and_measured = the greater of the two", enum=RULE_METHODS),
        col("n", "integer", "n of the avg_top_n methods", null=True),
        col("reset", "text", "span the measured value is taken over before it starts again", enum=RULE_RESETS),
        col("minimum_value", "numeric", "smallest quantity charged, when stated", null=True),
        col("threshold_value", "numeric", "the charge applies only to the quantity above this, when stated "
            "(e.g. export above 1.5 kW)", null=True),
        col("allowance_per_day", "numeric", "free quantity per day before the charge applies, when stated", null=True),
        col("allowance_rollover", "boolean", "1 when an unused daily allowance carries over within the billing period",
            null=True),
        *provenance(),
        col("quote", "text", "verbatim wording at the locator"),
        col("note", "text", "caveat from the curator", null=True),
    ],
    "foreign_keys": [TARIFF_FK],
    "checks": ["effective_from <= effective_to", "(n IS NULL) = (method NOT IN ('avg_top_n_days', "
               "'avg_top_n_intervals'))", "allowance_rollover IS NULL OR allowance_per_day IS NOT NULL"],
})

TABLE_ORDER = [t["name"] for t in TABLES]
BY_NAME = {t["name"]: t for t in TABLES}
SQL_TYPES = {"text": "TEXT", "integer": "INTEGER", "numeric": "NUMERIC", "date": "TEXT", "time": "TEXT",
             "boolean": "INTEGER"}


def ddl():
    out = ["-- Generated from scripts/tariffdb/spec.py by scripts/tariffdb/build.py; do not edit by hand.",
           "-- SQLite. Tables are in foreign-key order."]
    for t in TABLES:
        lines = []
        for c in t["columns"]:
            n = c["name"]
            s = f"  {n} {SQL_TYPES[c['type']]}" + ("" if c["nullable"] else " NOT NULL")
            if c["enum"]:
                s += f" CHECK ({n} IN ({', '.join(repr(v) for v in c['enum'])}))"
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
