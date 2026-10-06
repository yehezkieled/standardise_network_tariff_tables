"""Single source of truth for the tariff database schema.

`data/tariffdb/schema.sql` (SQLite and PostgreSQL DDL) and `data/tariffdb/schema.json` (machine-readable table spec)
are generated from TABLES by `scripts/tariffdb/build.py`; the test suite fails when either file is stale.

Column types are portable: text, integer, numeric, date ('YYYY-MM-DD' text), time ('HH:MM', end-exclusive, '24:00'
allowed), boolean (integer 0/1). Every enum is enforced with a CHECK constraint.
"""

FIN_YEARS = ["2023-24", "2024-25", "2025-26", "2026-27"]
STATES = ["NSW", "VIC", "QLD", "SA", "TAS", "ACT", "NT"]
DOCUMENT_TYPES = [
    "aer_consolidated_stakeholder_report", "aer_stakeholder_report", "pricing_proposal", "pricing_proposal_overview",
    "price_list", "tariff_summary", "tariff_schedule", "schedule_of_charges", "statement_of_tariff_classes",
    "price_guide", "pricing_schedule",
]
PRICE_STATUS = ["proposed", "approved", "mixed", "published", "indicative"]
RECON_SIDES = ["AER", "AER_HOSTED", "DNSP"]
PRICE_BASES = ["NUoS", "DUoS", "TUoS", "DPPC", "JSA", "metering", "unknown"]
CHARGE_TYPES = ["fixed", "energy", "demand", "capacity", "export", "other"]
QUANTITIES = ["kWh", "kVAh", "kW", "kVA", "kW_or_kVA", "lamp", "none"]
PERIODS = ["day", "month", "year", "season", "none", "unstated"]
TRISTATE = ["yes", "no", "unknown"]
LISTING_FLAGS = [
    "trial", "closed_to_new", "withdrawn", "obsolete", "grandfathered", "site_specific", "zero_priced_placeholder",
    "transitional", "indicative", "proposed_price", "includes_lfit", "excludes_lfit", "includes_metering",
    "excludes_metering", "duplicate_listing", "joint_label_member", "regional_variant",
]
ALIAS_KINDS = ["aer_code_label", "aer_tariff_id", "joint_label_member", "regional_suffix", "name_matched", "code_variant"]
RELATION_TYPES = [
    "renamed_to", "replaced_by", "opt_out_alternative", "export_companion", "xmc_variant", "same_prices_as",
    "assigned_with", "aer_sibling_code",
]
DAY_TYPES = ["weekday", "weekend", "all_days", "business_day", "non_business_day", "saturday", "sunday",
             "public_holiday", "weekend_and_public_holiday"]
TOU_PERIODS = ["peak", "shoulder", "off_peak", "solar_soak", "critical_peak", "critical_minimum", "super_off_peak",
               "demand_window", "export_charge_window", "export_reward_window", "controlled_load_supply", "anytime",
               "high_season_peak", "low_season_peak", "overnight", "evening", "day"]
TIME_BASES = ["local_time", "standard_time", "not_stated"]
HOLIDAY_RULES = ["as_weekend", "as_weekday", "as_non_business_day", "not_stated", "unchanged"]
TOU_APPLIES = ["energy", "demand", "export", "controlled_load", "all"]
AGGREGATIONS = ["monthly_max", "billing_period_max", "rolling_12_month_max", "annual_max", "seasonal_max", "daily_average_of_monthly_max",
                "average_of_highest_days", "contracted", "agreed", "not_stated"]
RULE_TYPES = [
    "customer_type", "voltage_level", "consumption_min", "consumption_max", "demand_min", "demand_max",
    "meter_type", "assignment", "availability", "requires_technology", "opt_out_to", "minimum_demand_charge", "other",
]
OPERATORS = ["eq", "lt", "le", "gt", "ge", "in"]
# value_text vocabulary for the categorical rule types (rule types not listed take free text)
RULE_VALUES = {
    "customer_type": ["residential", "small_business", "medium_business", "large_business", "business",
                      "unmetered", "public_lighting", "embedded_generation", "controlled_load", "storage", "ev_charging",
                      "any"],
    "voltage_level": ["LV", "HV", "subtransmission", "transmission", "zone_substation"],
    "meter_type": ["interval", "smart", "basic", "accumulation", "unmetered", "any"],
    "assignment": ["default", "opt_in", "opt_out", "mandatory", "assigned_by_distributor", "retailer_request",
                   "site_specific_assessment"],
    "availability": ["open", "closed_to_new", "withdrawn", "obsolete", "trial", "grandfathered", "transitional"],
    "requires_technology": ["solar", "battery", "ev", "controlled_load_device", "dedicated_circuit", "export_capable",
                            "storage", "flexible_load", "heat_pump"],
}
ADJUSTMENT_KINDS = ["metering_adder", "lfit_adder", "lfit_rebate"]
METERING_SOURCES = ["aer_metering_sheet", "aer_tariff_schedule_1", "distributor_metering_block"]
METERING_BASES = ["per_year", "per_meter", "per_day", "unstated"]
VERIFICATIONS = ["cell_display", "page_text", "ocr_sum_check"]
LOCATOR_KINDS = ["xlsx", "pdf", "pdf-ocr"]
IDENTITY_BASES = ["distributor_code", "aer_label", "aer_tariff_id"]


def col(name, type_, desc, *, null=False, pk=False, fk=None, enum=None, unit=None):
    return {"name": name, "type": type_, "nullable": null, "primary_key": pk, "references": fk, "enum": enum,
            "unit": unit, "description": desc}


def provenance(null=False):
    return [
        col("locator", "text", "Where in the document: xlsx:<sheet>!<cell>, pdf:p<page>, pdf-ocr:p<page> or html:text "
            "(grammar in scripts/tariffdb/locators.py)", null=null),
        col("quote", "text", "Verbatim wording from the document at the locator (whitespace-insensitive match is tested)",
            null=null),
    ]


TABLES = [
    {
        "name": "financial_year",
        "description": "Australian financial years covered (1 July to 30 June).",
        "why": ["Every price, rule and window is tied to a year AND to explicit dates, so mid-year changes fit without "
                "breaking the yearly grain."],
        "columns": [
            col("fin_year", "text", "e.g. 2025-26", pk=True),
            col("start_date", "date", "1 July"),
            col("end_date", "date", "30 June (inclusive)"),
        ],
        "checks": ["start_date < end_date"],
    },
    {
        "name": "distributor",
        "description": "The 14 electricity distribution network service providers (DNSPs).",
        "why": ["Time zone and DST live here because TOU windows are stated in local or standard time and QLD/NT have "
                "no daylight saving."],
        "columns": [
            col("distributor_id", "text", "slug, e.g. ausgrid", pk=True),
            col("name", "text", "canonical name used by the reconciliation (scripts/schema.py CANON)"),
            col("aer_label", "text", "name the AER workbooks use"),
            col("state", "text", "jurisdiction", enum=STATES),
            col("iana_timezone", "text", "IANA zone of the network area, e.g. Australia/Sydney"),
            col("observes_dst", "boolean", "1 when local clocks move for daylight saving"),
        ],
        "unique": [["name"]],
    },
    {
        "name": "document_series",
        "description": "A publication that is reissued in versions (e.g. the AER 2025-26 consolidated stakeholder "
                       "report v1..v5, or one distributor's price list for a year).",
        "why": ["Versions of the same publication are grouped so 'latest version' and 'v1 vs approved' are simple "
                "queries; no version ever overwrites another."],
        "columns": [
            col("series_id", "text", "slug", pk=True),
            col("author", "text", "who wrote the prices", enum=["AER", "distributor"]),
            col("distributor_id", "text", "NULL for multi-distributor AER reports", null=True,
                fk="distributor.distributor_id"),
            col("fin_year", "text", "pricing year", fk="financial_year.fin_year"),
            col("title", "text", "series title"),
        ],
    },
    {
        "name": "source_document",
        "description": "One version of one document: the unit of provenance. Every value row points here.",
        "why": ["Proposed, approved, AER and distributor numbers coexist because each is keyed by its own document "
                "version.",
                "sha256 + URL + retrieval date make each file re-fetchable and tamper-evident.",
                "Versions known to exist but not retrievable (AER login-gated files) are still recorded so the history "
                "has no silent gaps."],
        "columns": [
            col("document_id", "text", "slug derived from the file name", pk=True),
            col("series_id", "text", "publication series", fk="document_series.series_id"),
            col("version_label", "text", "as published, e.g. v1, v5, v1.1, 'updated 17 Jul 2024'"),
            col("version_seq", "integer", "order within the series (1 = first)"),
            col("author", "text", "who wrote the prices", enum=["AER", "distributor"]),
            col("distributor_id", "text", "NULL for multi-distributor AER reports", null=True,
                fk="distributor.distributor_id"),
            col("fin_year", "text", "pricing year", fk="financial_year.fin_year"),
            col("document_type", "text", "kind of publication", enum=DOCUMENT_TYPES),
            col("price_status", "text", "regulatory status of the prices in this version (mixed: differs by distributor, "
                "see document_coverage)", enum=PRICE_STATUS),
            col("recon_side", "text", "role in the reconciliation: AER = AER-authored, AER_HOSTED = distributor document "
                "hosted on aer.gov.au, DNSP = distributor's own site", enum=RECON_SIDES),
            col("title", "text", "description from sources/inventory.csv"),
            col("publication_date", "date", "date the version was published, when known", null=True),
            col("publication_date_basis", "text", "how the date is known", null=True),
            col("retrieval_status", "text", "whether the file is held", enum=["retrieved", "not_retrievable"]),
            col("local_path", "text", "repo-relative path", null=True),
            col("source_url", "text", "exact URL the file was retrieved from (or the landing page when not retrievable)",
                null=True),
            col("access_note", "text", "access caveats from the inventory", null=True),
            col("sha256", "text", "SHA-256 of the file used", null=True),
            col("retrieved_on", "date", "date the file was retrieved (or the Wayback capture date)", null=True),
            col("retrieved_on_basis", "text", "wayback_capture | inventory_commit", null=True),
            col("committed_in_repo", "boolean", "1 when the file itself is committed (Wayback copies)"),
        ],
        "unique": [["series_id", "version_seq"], ["local_path"]],
        "checks": ["retrieval_status <> 'retrieved' OR (local_path IS NOT NULL AND sha256 IS NOT NULL)"],
    },
    {
        "name": "document_coverage",
        "description": "Which distributors' prices an AER consolidated version carries, and whether they are proposed or "
                       "approved there (from the AER changelog).",
        "why": ["One AER file mixes proposed and approved prices by jurisdiction (2025-26 v3: approved ACT/NSW/TAS/VIC, "
                "proposed QLD/SA/NT), so price status is per (document, distributor), not per document.",
                "Versions the AER no longer serves are still covered, so the history of what was published when is "
                "complete even where the file is gone."],
        "columns": [
            col("document_id", "text", "AER consolidated version", pk=True, fk="source_document.document_id"),
            col("distributor_id", "text", "distributor", pk=True, fk="distributor.distributor_id"),
            col("price_status", "text", "status of that distributor's prices in that version", enum=PRICE_STATUS),
            col("evidence_document_id", "text", "saved AER landing page holding the changelog",
                fk="source_document.document_id"),
            *provenance(),
        ],
    },
    {
        "name": "document_ingestion",
        "description": "Explicit extraction coverage for every held or unavailable source document.",
        "why": ["A source inventory entry is not evidence that its prices were extracted; independent row counts expose empty or rules-only documents."],
        "columns": [
            col("document_id", "text", "Source version", pk=True, fk="source_document.document_id"),
            col("charge_count", "integer", "Number of charge rows read from this source"),
            col("listing_count", "integer", "Tariff listings from this source"),
            col("eligibility_count", "integer", "Structured or quoted requirements from this source"),
            col("tou_schedule_count", "integer", "TOU schedules from this source"),
            col("metering_count", "integer", "Separately stored metering rates"),
            col("status", "text", "Extraction state", enum=["prices", "rules_only", "metadata_only", "unavailable"]),
        ],
        "checks": ["charge_count >= 0 AND listing_count >= 0 AND eligibility_count >= 0 AND tou_schedule_count >= 0 AND metering_count >= 0"],
    },
    {
        "name": "tariff",
        "description": "Stable identity of a network tariff across years: the distributor's own code.",
        "why": ["Codes are the only identifier both sides share; names and labels drift every year.",
                "Codes the AER prints that no distributor document uses stay as their own identity (identity_basis) "
                "instead of being force-matched."],
        "columns": [
            col("tariff_id", "text", "<distributor_id>:<code>", pk=True),
            col("distributor_id", "text", "owner", fk="distributor.distributor_id"),
            col("tariff_code", "text", "canonical code"),
            col("identity_basis", "text", "where the canonical code comes from", enum=IDENTITY_BASES),
        ],
        "unique": [["distributor_id", "tariff_code"]],
    },
    {
        "name": "tariff_alias",
        "description": "Other labels under which a tariff is published: AER code labels, AER tariff IDs, joint labels "
                       "('010, 011*'), regional suffixes.",
        "why": ["Code-label quirks are data, not code: each alias is tied to the document it appears in."],
        "columns": [
            col("alias_id", "text", "<document_id>/<alias_kind>/<alias_label>/<tariff_id>", pk=True),
            col("tariff_id", "text", "tariff the label resolves to", fk="tariff.tariff_id"),
            col("alias_label", "text", "label exactly as published"),
            col("alias_kind", "text", "kind of alias", enum=ALIAS_KINDS),
            col("document_id", "text", "document where the label appears", fk="source_document.document_id"),
            col("note", "text", "how the alias was resolved", null=True),
        ],
    },
    {
        "name": "tariff_relation",
        "description": "Relationships between tariffs: renames, replacements, opt-out alternatives, export companions.",
        "why": ["Renames across years (e.g. SAPN RELE -> RESELE) keep both identities and link them, so history is "
                "never rewritten."],
        "columns": [
            col("relation_id", "text", "<from>|<relation_type>|<to>|<document_id>", pk=True),
            col("from_tariff_id", "text", "subject", fk="tariff.tariff_id"),
            col("relation_type", "text", "relationship", enum=RELATION_TYPES),
            col("to_tariff_id", "text", "object", fk="tariff.tariff_id"),
            col("fin_year", "text", "year the relation is stated for", fk="financial_year.fin_year"),
            col("document_id", "text", "evidence", fk="source_document.document_id"),
            *provenance(null=True),
            col("note", "text", "Source qualifications or interpretation notes", null=True),
        ],
        "checks": ["from_tariff_id <> to_tariff_id"],
    },
    {
        "name": "tariff_listing",
        "description": "A tariff as listed in one document version (code, name and class as printed there).",
        "why": ["The historical grain: one row per (document version, tariff); attributes are never updated, a new "
                "version adds a new listing.",
                "is_priced = 0 represents AER zero-priced placeholder rows without inventing charges."],
        "columns": [
            col("listing_id", "text", "<document_id>/<code_published>[/<n>]", pk=True),
            col("document_id", "text", "where", fk="source_document.document_id"),
            col("tariff_id", "text", "which tariff", fk="tariff.tariff_id"),
            col("code_published", "text", "code exactly as printed (joint, starred ...); NULL when the row prints no code "
                "(AER 2025-26 v1)", null=True),
            col("name_published", "text", "tariff name as printed", null=True),
            col("class_published", "text", "tariff class / customer class heading as printed", null=True),
            col("region", "text", "pricing region or zone when the document splits one code by region", null=True),
            col("effective_from", "date", "first day the listed prices apply"),
            col("effective_to", "date", "last day (inclusive)"),
            col("price_availability", "text", "Whether this document prints prices, a zero/blank placeholder, or only rules",
                enum=["priced", "placeholder", "rules_only"]),
            col("is_priced", "boolean", "0 when the source has no priced components (placeholder); explicit zero charges remain prices"),
            col("locator", "text", "first price cell/page of the listing"),
            col("note", "text", "parser notes for the listing", null=True),
        ],
        "checks": ["effective_from <= effective_to"],
    },
    {
        "name": "listing_flag",
        "description": "Status flags of a listing: trial, closed to new customers, withdrawn, site-specific, "
                       "placeholder, proposed price, LFiT/metering inclusion.",
        "why": ["A tariff can be several of these at once and they change by year, so they are rows, not columns."],
        "columns": [
            col("listing_id", "text", "listing", pk=True, fk="tariff_listing.listing_id"),
            col("flag", "text", "status", pk=True, enum=LISTING_FLAGS),
            col("evidence_kind", "text", "where the flag comes from", enum=["published_text", "parser_note", "document",
                                                                              "curated"]),
            col("evidence", "text", "the wording that establishes it"),
        ],
    },
    {
        "name": "charge",
        "description": "One published price component of one listing in one price basis (NUoS/DUoS/TUoS/...).",
        "why": ["Value and unit are kept exactly as published (value_published, unit_published, value_raw) next to the "
                "normalised value (value_std in cents; fixed charges per day; demand per billing period).",
                "Inclusion of metering and LFiT is explicit per row because AER and distributor totals differ by them.",
                "Every row carries a locator that the tests re-read from the source file."],
        "columns": [
            col("charge_id", "text", "<listing_id>/<price_basis>/<gst>/<locator>", pk=True),
            col("listing_id", "text", "listing", fk="tariff_listing.listing_id"),
            col("price_basis", "text", "NUoS = total network price; DUoS/TUoS/DPPC/JSA components; metering",
                enum=PRICE_BASES),
            col("component_label", "text", "component label as published (header hierarchy joined with ' - ')"),
            col("charge_type", "text", "normalised component kind", enum=CHARGE_TYPES),
            col("time_band", "text", "normalised band (peak, off_peak, shoulder, block1, ...)", null=True),
            col("season", "text", "normalised season (summer, non_summer, high, low, winter)", null=True),
            col("value_published", "text", "number exactly as displayed in the document"),
            col("unit_published", "text", "unit exactly as published", null=True),
            col("unit_interpreted", "text", "Unit used for conversion; differs only where the source parser supplies a period or repairs a source typo", null=True),
            col("normalisation_note", "text", "Reason the conversion unit differs from the printed unit", null=True),
            col("value_raw", "text", "full-precision cell value for spreadsheets (NULL for PDFs)", null=True),
            col("value_num", "numeric", "value_published as a number"),
            col("value_std", "numeric", "value in standard units", null=True, unit="see unit_std"),
            col("unit_std", "text", "c/day, c/kWh, c/kVAh, c/kW/<period>, c/kVA/<period>, c/lamp/day ...", null=True),
            col("quantity", "text", "what the price is per", enum=QUANTITIES),
            col("period", "text", "billing period of the unit", enum=PERIODS),
            col("period_inferred", "boolean", "1 when the period comes from the component label, not the published unit"),
            col("gst", "text", "GST basis", enum=["excl", "incl"]),
            col("includes_metering", "text", "whether the value contains a metering charge", enum=TRISTATE),
            col("includes_lfit", "text", "whether the value contains the ACT large-scale feed-in tariff amount",
                enum=TRISTATE + ["not_applicable"]),
            col("effective_from", "date", "first day"),
            col("effective_to", "date", "last day (inclusive)"),
            col("locator", "text", "where the value is (re-read by the tests)"),
            col("locator_kind", "text", "xlsx | pdf | pdf-ocr", enum=LOCATOR_KINDS),
            col("sheet", "text", "spreadsheet tab", null=True),
            col("cell", "text", "spreadsheet cell", null=True),
            col("page", "integer", "PDF page (1-based)", null=True),
            col("verification", "text", "how the value is re-read", enum=VERIFICATIONS),
            col("note", "text", "parser caveats", null=True),
        ],
        "checks": ["effective_from <= effective_to",
                   "(locator_kind = 'xlsx' AND sheet IS NOT NULL AND cell IS NOT NULL AND page IS NULL) OR "
                   "(locator_kind <> 'xlsx' AND page IS NOT NULL AND sheet IS NULL AND cell IS NULL)"],
    },
    {
        "name": "charge_step",
        "description": "Quantity blocks or export allowances, separate from tariff eligibility thresholds.",
        "why": ["Consumption blocks reset independently of eligibility: a 60 kWh/day block is not a customer assignment threshold.",
                "Open-ended upper bounds use NULL; inclusivity and reset period prevent ambiguous boundaries."],
        "columns": [
            col("step_id", "text", "Stable evidence-derived key", pk=True),
            col("tariff_id", "text", "Tariff", fk="tariff.tariff_id"),
            col("document_id", "text", "Version stating the block", fk="source_document.document_id"),
            col("fin_year", "text", "Pricing year", fk="financial_year.fin_year"),
            col("effective_from", "date", "First applicable day"),
            col("effective_to", "date", "Last applicable day, inclusive"),
            col("step_group", "text", "The quantity divided into blocks, as the source names it (e.g. 'Anytime Energy'); "
                "the blocks of one (tariff, document, step_group) form one ladder"),
            col("component_label", "text", "Source component or allowance name"),
            col("step_index", "integer", "1-based block order"),
            col("lower_bound", "numeric", "Lower quantity boundary", null=True),
            col("upper_bound", "numeric", "Upper quantity boundary, NULL means unbounded", null=True),
            col("lower_inclusive", "boolean", "Whether lower boundary belongs to the block"),
            col("upper_inclusive", "boolean", "Whether upper boundary belongs to the block"),
            col("quantity_unit", "text", "Unit of the boundaries", enum=["kWh", "kW", "kVA"]),
            col("reset_period", "text", "Period over which quantity accumulates", enum=["day", "month", "billing_period", "year", "unstated"]),
            *provenance(),
        ],
        "checks": ["step_index > 0", "effective_from <= effective_to",
                   "lower_bound IS NULL OR upper_bound IS NULL OR lower_bound < upper_bound"],
    },
    {
        "name": "metering_price",
        "description": "Metering prices: the AER Metering worksheet (2025-26 on), the AER 2024-25 'Tariff schedule 1', and "
                       "the per-tariff Metering block of the Energex/Ergon price lists.",
        "why": ["The AER prints network prices without metering and metering separately; storing both lets the "
                "distributor's metering-inclusive daily charge be reproduced exactly ($/yr x 100 / 365).",
                "charge_basis keeps 'per year' vs exit fee vs unstated apart: the sheet mixes them in one column."],
        "columns": [
            col("metering_price_id", "text", "<document_id>/<locator>", pk=True),
            col("document_id", "text", "where", fk="source_document.document_id"),
            col("distributor_id", "text", "whose metering", fk="distributor.distributor_id"),
            col("fin_year", "text", "year", fk="financial_year.fin_year"),
            col("source_block", "text", "which table of the document", enum=METERING_SOURCES),
            col("meter_class", "text", "customer/meter class label as published"),
            col("tariff_codes_published", "text", "content of the sheet's 'Tariff code' column as printed (tariff codes, "
                "or metering-service codes such as MP7)", null=True),
            col("tariff_id", "text", "tariff the row belongs to (per-tariff blocks only)", null=True,
                fk="tariff.tariff_id"),
            col("component_label", "text", "price component the block value adds to (per-tariff blocks only)",
                null=True),
            col("charge_basis", "text", "what the price is per", enum=METERING_BASES),
            col("value_published", "text", "as displayed"),
            col("unit_published", "text", "as published", null=True),
            col("value_raw", "text", "full-precision cell value"),
            col("value_num", "numeric", "number"),
            col("value_c_per_day", "numeric", "cents per day: per_year x 100 / 365, per_day x 100 ($) ; NULL otherwise",
                null=True, unit="c/day"),
            col("locator", "text", "cell (re-read by the tests)"),
            col("sheet", "text", "spreadsheet tab"),
            col("cell", "text", "spreadsheet cell"),
        ],
    },
    {
        "name": "tou_schedule",
        "description": "A named set of time-of-use windows as stated in one document.",
        "why": ["Windows are shared by many tariffs and change by year, so they are versioned per document and linked "
                "to tariffs through tariff_tou.",
                "time_basis and public-holiday treatment are explicit because distributors differ (local vs standard "
                "time; holidays as weekends or not)."],
        "columns": [
            col("tou_schedule_id", "text", "slug", pk=True),
            col("distributor_id", "text", "owner", fk="distributor.distributor_id"),
            col("document_id", "text", "where stated", fk="source_document.document_id"),
            col("fin_year", "text", "year", fk="financial_year.fin_year"),
            col("name", "text", "what the windows are for"),
            col("time_basis", "text", "clock the times refer to", enum=TIME_BASES),
            col("public_holidays", "text", "how public holidays are treated", enum=HOLIDAY_RULES),
            col("covers_full_day", "boolean", "1 when the document's windows partition every day (tested: 24h, no "
                "overlap)"),
            *provenance(),
            col("note", "text", "Source qualifications or interpretation notes", null=True),
        ],
    },
    {
        "name": "tou_window",
        "description": "One time window: period, day type, start/end time, months.",
        "why": ["Times are 'HH:MM' with an exclusive end ('24:00' allowed) so windows tile a day without gaps or "
                "overlaps; months are explicit so seasonal windows need no special casing."],
        "columns": [
            col("window_id", "text", "<tou_schedule_id>/<n>", pk=True),
            col("tou_schedule_id", "text", "schedule", fk="tou_schedule.tou_schedule_id"),
            col("period", "text", "period name (normalised)", enum=TOU_PERIODS),
            col("period_label", "text", "period name as published"),
            col("day_type", "text", "days the window applies to", enum=DAY_TYPES),
            col("start_time", "time", "inclusive"),
            col("end_time", "time", "exclusive; 24:00 = midnight at the end of the day"),
            col("months", "text", "comma-separated month numbers 1-12 the window applies in; NULL when the source names "
                "a season without listing its months (exception season_months_not_stated)", null=True),
            col("season", "text", "season name as published", null=True),
            *provenance(),
        ],
        "checks": ["months IS NOT NULL OR season IS NOT NULL", "start_time < end_time", "length(start_time) = 5 AND length(end_time) = 5"],
    },
    {
        "name": "tou_window_month",
        "description": "Relational month membership of each TOU window.",
        "why": ["Month joins do not need comma-separated string matching; tou_window.months retains the readable extraction."],
        "columns": [col("window_id", "text", "Window", pk=True, fk="tou_window.window_id"),
                    col("month", "integer", "Calendar month 1–12", pk=True)],
        "checks": ["month BETWEEN 1 AND 12"],
    },
    {
        "name": "tariff_tou",
        "description": "Which TOU schedule a tariff uses, for which kind of charge, in which year.",
        "why": ["A tariff can have different windows for energy, demand and export charges."],
        "columns": [
            col("tariff_tou_id", "text", "<tariff_id>|<tou_schedule_id>|<applies_to>", pk=True),
            col("tariff_id", "text", "tariff", fk="tariff.tariff_id"),
            col("tou_schedule_id", "text", "schedule", fk="tou_schedule.tou_schedule_id"),
            col("applies_to", "text", "charge kind the windows price", enum=TOU_APPLIES),
            col("effective_from", "date", "first day"),
            col("effective_to", "date", "last day (inclusive)"),
            col("document_id", "text", "evidence", fk="source_document.document_id"),
            *provenance(),
        ],
        "checks": ["effective_from <= effective_to"],
    },
    {
        "name": "demand_rule",
        "description": "How billed demand is measured: kW or kVA, interval, aggregation, window, months, minimums.",
        "why": ["Demand prices are meaningless without the measurement rule; the rule is a row so one rule can serve "
                "several tariffs and components."],
        "columns": [
            col("demand_rule_id", "text", "slug", pk=True),
            col("distributor_id", "text", "owner", fk="distributor.distributor_id"),
            col("document_id", "text", "where stated", fk="source_document.document_id"),
            col("fin_year", "text", "year", fk="financial_year.fin_year"),
            col("measure", "text", "kW or kVA", enum=["kW", "kVA"]),
            col("interval_minutes", "integer", "metering interval the maximum is taken over", null=True),
            col("aggregation", "text", "how the billed figure is derived", enum=AGGREGATIONS),
            col("aggregation_count", "integer", "n for average_of_highest_days (e.g. 4 highest days in the month)",
                null=True),
            col("window_tou_schedule_id", "text", "schedule holding the demand window", null=True,
                fk="tou_schedule.tou_schedule_id"),
            col("window_period", "text", "period of that schedule", null=True, enum=TOU_PERIODS),
            col("months", "text", "months the charge applies; NULL when the rule states none (a linked window carries "
                "any season)", null=True),
            col("minimum_chargeable", "numeric", "minimum chargeable demand", null=True),
            col("minimum_unit", "text", "unit of the minimum", null=True),
            *provenance(),
            col("note", "text", "Source qualifications or interpretation notes", null=True),
        ],
    },
    {
        "name": "tariff_demand_rule",
        "description": "Which demand rule a tariff's demand component uses.",
        "why": ["Peak and off-peak demand components of one tariff can follow different rules."],
        "columns": [
            col("tariff_demand_rule_id", "text", "<tariff_id>|<demand_rule_id>|<time_band>|<season>", pk=True),
            col("tariff_id", "text", "tariff", fk="tariff.tariff_id"),
            col("demand_rule_id", "text", "rule", fk="demand_rule.demand_rule_id"),
            col("time_band", "text", "component band it governs (NULL = all demand components)", null=True),
            col("season", "text", "season it governs (NULL = all)", null=True),
            col("effective_from", "date", "first day"),
            col("effective_to", "date", "last day (inclusive)"),
            col("document_id", "text", "evidence", fk="source_document.document_id"),
        ],
        "checks": ["effective_from <= effective_to"],
    },
    {
        "name": "eligibility_rule",
        "description": "Requirements and assignment rules: customer type, voltage, consumption/demand thresholds, "
                       "meter type, default/opt-in/opt-out, closure, required technology.",
        "why": ["Requirements are heterogeneous, so each is one typed row (rule_type + operator + number + unit) rather "
                "than dozens of mostly-empty columns; every rule quotes its source."],
        "columns": [
            col("rule_id", "text", "<tariff_id>|<fin_year>|<n>", pk=True),
            col("tariff_id", "text", "tariff", fk="tariff.tariff_id"),
            col("document_id", "text", "where stated", fk="source_document.document_id"),
            col("fin_year", "text", "year", fk="financial_year.fin_year"),
            col("effective_from", "date", "first day"),
            col("effective_to", "date", "last day (inclusive)"),
            col("rule_type", "text", "what the rule constrains", enum=RULE_TYPES),
            col("operator", "text", "comparison for numeric rules", null=True, enum=OPERATORS),
            col("value_num", "numeric", "threshold", null=True),
            col("value_unit", "text", "unit of the threshold (MWh/yr, kVA, kW, kV ...)", null=True),
            col("value_text", "text", "categorical value (residential, LV, interval, default, opt_in, ...)", null=True),
            col("target_tariff_id", "text", "tariff referred to (opt-out target, required companion)", null=True,
                fk="tariff.tariff_id"),
            *provenance(),
            col("note", "text", "Source qualifications or interpretation notes", null=True),
        ],
        "checks": ["effective_from <= effective_to", "value_num IS NOT NULL OR value_text IS NOT NULL OR "
                   "target_tariff_id IS NOT NULL"],
    },
    {
        "name": "price_adjustment",
        "description": "A documented difference between AER and distributor prices for one distributor-year "
                       "(metering adder, ACT LFiT adder or rebate, rounding artefact).",
        "why": ["The known AER-vs-distributor offsets are explained by data with a formula and evidence, so a consumer "
                "can derive the distributor price from the AER price."],
        "columns": [
            col("adjustment_id", "text", "<kind>/<distributor_id>/<fin_year>", pk=True),
            col("kind", "text", "kind", enum=ADJUSTMENT_KINDS),
            col("distributor_id", "text", "distributor", fk="distributor.distributor_id"),
            col("fin_year", "text", "year", fk="financial_year.fin_year"),
            col("amount", "numeric", "uniform amount when there is one", null=True),
            col("amount_unit", "text", "unit of amount", null=True),
            col("formula", "text", "distributor value = f(AER value)"),
            col("aer_document_id", "text", "AER-side document", fk="source_document.document_id"),
            col("distributor_document_id", "text", "distributor-side document", fk="source_document.document_id"),
            col("evidence_document_id", "text", "document stating or containing the amount",
                fk="source_document.document_id"),
            *provenance(),
        ],
    },
    {
        "name": "price_adjustment_tariff",
        "description": "Tariffs an adjustment applies to, with the expected per-tariff difference.",
        "why": ["Metering adders differ by tariff (only small-customer tariffs carry them); listing the tariffs makes "
                "the rule testable."],
        "columns": [
            col("adjustment_id", "text", "adjustment", pk=True, fk="price_adjustment.adjustment_id"),
            col("tariff_id", "text", "tariff", pk=True, fk="tariff.tariff_id"),
            col("metering_price_id", "text", "metering row supplying the amount", null=True,
                fk="metering_price.metering_price_id"),
            col("expected_delta_std", "numeric", "distributor minus AER, standard units", null=True),
            col("delta_unit", "text", "unit of expected_delta_std", null=True),
        ],
    },
    {
        "name": "exception_type",
        "description": "Catalogue of irregularities the data must represent, and how it represents each.",
        "why": ["Exceptions are first-class data with a stated representation and a test, so new years can be checked "
                "against the same catalogue."],
        "columns": [
            col("exception_code", "text", "slug", pk=True),
            col("title", "text", "short name"),
            col("description", "text", "what happens in the sources"),
            col("representation", "text", "tables/columns that represent it"),
            col("test", "text", "test that exercises it (tests/test_tariffdb.py)"),
        ],
    },
    {
        "name": "exception_instance",
        "description": "Each occurrence of a catalogued exception.",
        "why": ["Occurrences point at the exact tariff/listing/charge/document affected."],
        "columns": [
            col("instance_id", "text", "<exception_code>/<n>", pk=True),
            col("exception_code", "text", "type", fk="exception_type.exception_code"),
            col("distributor_id", "text", "distributor affected (NULL for multi-distributor AER documents)", null=True,
                fk="distributor.distributor_id"),
            col("fin_year", "text", "financial year of the occurrence", null=True, fk="financial_year.fin_year"),
            col("tariff_id", "text", "tariff affected", null=True, fk="tariff.tariff_id"),
            col("listing_id", "text", "listing affected", null=True, fk="tariff_listing.listing_id"),
            col("charge_id", "text", "charge affected", null=True, fk="charge.charge_id"),
            col("document_id", "text", "document where it occurs", null=True, fk="source_document.document_id"),
            col("related_document_id", "text", "second document (e.g. approved version vs v1)", null=True,
                fk="source_document.document_id"),
            col("quantity", "numeric", "size of the effect where numeric", null=True),
            col("quantity_unit", "text", "Unit of the exception quantity", null=True),
            col("detail", "text", "what happens here"),
        ],
    },
]


TABLE_ORDER = [t["name"] for t in TABLES]
BY_NAME = {t["name"]: t for t in TABLES}

SQL_TYPES = {
    "sqlite": {"text": "TEXT", "integer": "INTEGER", "numeric": "NUMERIC", "date": "TEXT", "time": "TEXT",
               "boolean": "INTEGER"},
    "postgres": {"text": "TEXT", "integer": "INTEGER", "numeric": "NUMERIC", "date": "DATE", "time": "TEXT",
                 "boolean": "SMALLINT"},
}


def ddl(dialect="sqlite"):
    out = [f"-- Generated from scripts/tariffdb/spec.py by scripts/tariffdb/build.py; do not edit by hand.",
           f"-- Dialect: {dialect}. Load order follows foreign keys."]
    for t in TABLES:
        lines = []
        pks = [c["name"] for c in t["columns"] if c["primary_key"]]
        for c in t["columns"]:
            s = f"  {c['name']} {SQL_TYPES[dialect][c['type']]}"
            if not c["nullable"]:
                s += " NOT NULL"
            if c["enum"]:
                vals = ", ".join("'" + v.replace("'", "''") + "'" for v in c["enum"])
                s += f" CHECK ({c['name']} IN ({vals}))"
            if c["type"] == "boolean":
                s += f" CHECK ({c['name']} IN (0, 1))"
            # SQLite keeps a non-numeric string in a numeric column as text; reject it (PostgreSQL does so natively)
            if c["type"] == "integer" and dialect == "sqlite":
                s += f" CHECK (typeof({c['name']}) IN ('integer', 'null'))"
            if c["type"] == "numeric" and dialect == "sqlite":
                s += f" CHECK (typeof({c['name']}) IN ('integer', 'real', 'null'))"
            if c["type"] == "date" and dialect == "sqlite":
                s += f" CHECK ({c['name']} IS NULL OR (length({c['name']}) = 10 AND {c['name']} GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'))"
            if c["type"] == "time":
                s += (f" CHECK (length({c['name']}) = 5 AND substr({c['name']}, 3, 1) = ':' AND "
                      f"(substr({c['name']}, 1, 2) BETWEEN '00' AND '23' AND substr({c['name']}, 4, 2) BETWEEN '00' AND '59' "
                      f"OR {c['name']} = '24:00'))")
            lines.append(s)
        lines.append(f"  PRIMARY KEY ({', '.join(pks)})")
        for c in t["columns"]:
            if c["references"]:
                rt, rc = c["references"].split(".")
                lines.append(f"  FOREIGN KEY ({c['name']}) REFERENCES {rt} ({rc})")
        for u in t.get("unique", []):
            lines.append(f"  UNIQUE ({', '.join(u)})")
        for ch in t.get("checks", []):
            lines.append(f"  CHECK ({ch})")
        out.append(f"\nCREATE TABLE {t['name']} (\n" + ",\n".join(lines) + "\n);")
    return "\n".join(out) + "\n"


def postgres_load():
    """psql script that creates the schema and imports every CSV in foreign-key order (run from data/tariffdb)."""
    out = ["-- Generated from scripts/tariffdb/spec.py by scripts/tariffdb/build.py; do not edit by hand.",
           "-- Usage (from data/tariffdb):  psql -v ON_ERROR_STOP=1 -d <database> -f load.postgres.sql",
           "-- An empty unquoted CSV field loads as NULL, which is what the tables mean by it.",
           "BEGIN;", "\\i schema.postgres.sql"]
    for t in TABLES:
        cols = ", ".join(c["name"] for c in t["columns"])
        out.append(f"\\copy {t['name']} ({cols}) FROM 'tables/{t['name']}.csv' WITH (FORMAT csv, HEADER true)")
    out.append("COMMIT;")
    return "\n".join(out) + "\n"


def json_spec():
    return {"generated_from": "scripts/tariffdb/spec.py", "tables": [
        {"name": t["name"], "description": t["description"], "why": t["why"], "file": f"tables/{t['name']}.csv",
         "primary_key": [c["name"] for c in t["columns"] if c["primary_key"]],
         "unique": t.get("unique", []), "checks": t.get("checks", []),
         "columns": t["columns"]} for t in TABLES]}
