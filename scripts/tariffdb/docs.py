"""Document the tariff database from its single source of truth (spec.py, exceptions.py and the built tables).

  .venv/bin/python scripts/tariffdb/docs.py                       rewrite docs/tariffdb.md
  .venv/bin/python scripts/tariffdb/docs.py --html <path>         also write the visual report (one self-contained page)

tests/test_tariffdb.py fails when docs/tariffdb.md is not what this script generates, so the documentation cannot drift
from the schema or the data.
"""
import argparse
import csv
import html
import os
import sys
from collections import Counter, defaultdict
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exceptions as catalogue  # noqa: E402
import spec  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
TABLES_DIR = os.path.join(ROOT, "data", "tariffdb", "tables")
MD_PATH = os.path.join(ROOT, "docs", "tariffdb.md")

# Table groups, in reading order; the ER overview draws one column per group.
GROUPS = [
    ("Provenance", ["financial_year", "distributor", "document_series", "source_document", "document_url_check",
                    "document_coverage", "document_ingestion"]),
    ("Listings and prices", ["tariff_listing", "listing_flag", "charge", "charge_step", "metering_price",
                             "price_adjustment", "price_adjustment_tariff"]),
    ("Tariff identity", ["tariff", "tariff_alias", "tariff_relation"]),
    ("Rules", ["eligibility_rule", "tariff_tou", "tou_schedule", "tou_window", "tou_window_month", "tariff_demand_rule",
               "demand_rule"]),
    ("Exceptions", ["exception_type", "exception_instance"]),
]

# Cross-cutting design decisions: (decision, reason, where it lives, how it is tested)
DECISIONS = [
    ("Files, not a database: one CSV per table + generated SQLite and PostgreSQL DDL + a JSON table spec",
     "No database to run, yet any database can import the CSVs as they are; CSVs diff and review in git",
     "data/tariffdb/tables/*.csv, schema.sqlite.sql, schema.postgres.sql, load.postgres.sql, schema.json",
     "TestLoad imports every CSV into SQLite with foreign keys and every CHECK enforced; test_postgres_load does the "
     "same in a real PostgreSQL when TARIFFDB_PG_BIN is set"),
    ("One source of truth for the schema",
     "DDL, JSON spec and this document are generated from scripts/tariffdb/spec.py, so they cannot disagree",
     "scripts/tariffdb/spec.py, scripts/tariffdb/docs.py",
     "TestSchemaFiles fails when a generated file is stale"),
    ("A document version is the unit of provenance",
     "Proposed vs approved and AER vs distributor values coexist: each value hangs off the exact version it was "
     "read from (URL, SHA-256, retrieval date, version label/sequence)",
     "source_document, document_series; document_id on every value table",
     "test_versions_are_kept_side_by_side, test_document_checksums"),
    ("Every AER version is archived and reconcilable, held or not",
     "The AER reissues its consolidated report several times a year (v1..v5) and takes each superseded file private. "
     "Every AER-authored file is committed under sources/; a version whose file is gone keeps a source_document row "
     "plus its own versioned URL with the dated server answer (307 = exists but private), so the history has no "
     "silent gaps; scripts/reconcile.py --aer-version reconciles any held version",
     "source_document, document_url_check, document_coverage; sources/aer/; out/version_grid.csv",
     "test_document_not_retrievable, test_every_aer_version_has_a_versioned_url"),
    ("Every value row carries a locator and every rule a verbatim quote",
     "Anyone can re-check a number by hand: xlsx sheet!cell or PDF page, plus the exact wording",
     "charge.locator/sheet/cell/page, *.locator + *.quote",
     "test_every_charge_value_is_in_its_source, test_every_quote_is_in_its_source re-read the files"),
    ("Source facts are append-only; derived tables are recomputed; keys come from content",
     "Nothing read from a source is updated in place: a re-issued document adds rows, so earlier answers stay "
     "reproducible. Tables and columns computed from those facts (ingestion counts, flags, adjustments, exception "
     "rows, metering/LFiT inclusion) are marked derived and rebuilt every time, so they can never drift from the facts",
     "ids built from document, code and component, never row order; spec 'derived'; "
     "build.py --check-append-only <git ref>",
     "test_append_only_check, test_append_only_check_against_git, test_ids_come_from_content_not_row_order, "
     "test_rebuild_reproduces_every_table"),
    ("Every value has a financial year AND explicit effective_from / effective_to dates",
     "The yearly grain is how prices are published, the dates let a mid-year change fit without schema change",
     "effective_from/effective_to on charge, listing, rules, links",
     "test_every_value_is_tied_to_a_year_dates_and_a_document_version, test_no_duplicate_effective_ranges"),
    ("Tariff identity is separate from how a document lists it",
     "Codes drift (renames, AER labels, joint labels '010, 011*', regional suffixes); the stable tariff keeps one "
     "identity, each listing keeps the code exactly as printed",
     "tariff, tariff_listing, tariff_alias, tariff_relation",
     "test_code_label_quirk, test_joint_code_label, test_aer_id_changed_between_versions"),
    ("Published value and unit are kept verbatim next to the normalised value",
     "Normalisation (cents, per day, demand per period) is an interpretation; the original is never lost",
     "charge.value_published/unit_published/value_raw vs value_num/value_std/unit_std",
     "test_value_num_and_std_follow_the_published_value"),
    ("Metering and LFiT inclusion are explicit per charge, and the offsets are data",
     "AER and distributor daily/energy charges differ by exactly these amounts; storing the formula and the "
     "per-tariff expected delta lets the distributor price be derived from the AER price",
     "charge.includes_metering/includes_lfit, metering_price, price_adjustment, price_adjustment_tariff",
     "test_metering_excluded_by_aer, test_act_lfit re-derive every delta"),
    ("Requirements are typed rule rows, not wide columns",
     "Eligibility is heterogeneous (class, voltage, kWh/kVA thresholds, meter, opt-in/out, closure, technology); "
     "one row per stated condition with operator + number + unit stays queryable and sparse-free",
     "eligibility_rule (rule_type, operator, value_num, value_unit, value_text, target_tariff_id)",
     "test_medium_business_demand_assignment, test_curated_files_validate"),
    ("TOU is schedule -> window -> month, with end-exclusive 'HH:MM' times and '24:00'",
     "Windows are shared by many tariffs and change by year; exclusive ends let windows tile a day exactly; "
     "months make seasons plain data",
     "tou_schedule, tou_window, tou_window_month, tariff_tou",
     "test_full_day_schedules_tile_24_hours_without_overlap, test_windows_of_one_period_never_overlap"),
    ("Clock basis and public holidays are stated per schedule; time zone and DST per distributor",
     "Distributors state windows in local, standard or daylight time and treat holidays differently; QLD and NT "
     "have no DST. Times are stored exactly as stated and never converted: AusNet's 'ADST' windows are kept with "
     "time_basis daylight_time and an exception, because the standard-time hours are not stated",
     "tou_schedule.time_basis/public_holidays, distributor.iana_timezone/observes_dst",
     "test_time_stated_in_daylight_time; enum CHECKs; curated.py validation"),
    ("Demand measurement is its own rule linked per band and season",
     "A demand price means nothing without kW vs kVA, interval, aggregation, window and months",
     "demand_rule, tariff_demand_rule",
     "test_demand_rule_windows_exist"),
    ("Blocks/steps are separate from eligibility thresholds",
     "A 60 kWh/day block resets daily and is not an assignment threshold",
     "charge_step", "test_quantity_blocks"),
    ("Nothing is invented",
     "Placeholders stay unpriced; missing TOU definitions, missing price attachments and seasons whose months are "
     "never listed are recorded as exceptions instead of guessed",
     "tariff_listing.price_availability, document_ingestion, tou_window.months NULL, exception_instance",
     "test_zero_priced_placeholder, test_tou_definition_missing, test_price_attachment_not_held, "
     "test_season_months_not_stated"),
    ("Exceptions are first-class data",
     "Each irregularity has a catalogue entry saying how it is represented and a test; every occurrence is a row",
     "exception_type, exception_instance", "test_catalogue_is_complete + one test per exception"),
    ("Hand-curated facts live in reviewable YAML with verbatim quotes",
     "TOU windows, demand rules and eligibility are prose in the sources; YAML is human-editable and validated "
     "against the source text before the build accepts it",
     "data/tariffdb/curated/<distributor>.yaml, scripts/tariffdb/curated.py",
     "test_curated_files_validate, test_every_quote_is_in_its_source"),
    ("Enums and types are enforced by the database",
     "A bad value fails the import instead of hiding in a CSV",
     "CHECK constraints from spec enums; SQLite typeof() checks on numbers; booleans are 0/1",
     "test_constraints_reject_bad_rows"),
    ("An empty CSV field means NULL", "CSV cannot tell '' from NULL, so no column stores an empty string",
     "all tables", "test_empty_field_means_null"),
    ("Price status is per (document, distributor), and never asserted without a source",
     "One AER version mixes approved and proposed prices by jurisdiction (2025-26 v3). A document whose status no "
     "held source states (every distributor document hosted on aer.gov.au) is 'unverified', not assumed approved",
     "source_document.price_status, document_coverage", "test_aer_version_differs, test_price_status_unverified"),
]

# Gaps the held sources cannot fill: (distributor-years, what is missing, where it would come from)
KNOWN_GAPS = [
    ("Ausgrid, all years", "energy TOU hours and the months of the high and low seasons",
     "the documents point to the Tariff Structure Statement and the ES7 Network Price Guide, which are not held"),
    ("Energex and Ergon, all years", "TOU windows and demand measurement",
     "the Schedule 8 price lists and AER reports state none; no tariff structure document is held"),
    ("TasNetworks 2024-25 to 2026-27", "TOU windows and demand measurement",
     "only price schedules are held; the 2023-24 guide's rules are not carried forward"),
    ("SA Power Networks 2026-27", "TOU windows", "the price list refers to the Tariff Structure Statement, not held"),
    ("SA Power Networks site-specific variants (e.g. LBAD201, ZSS035), all years", "TOU windows",
     "no document links them to their base tariff's windows, so those windows are not assumed to apply"),
    ("Powercor 2023-24", "TOU windows", "only the Tariff Summary workbook is held; it gives month columns, no times"),
    ("Essential Energy 2023-24", "TOU windows", "only the AER-hosted price list workbook is held; it defines no periods"),
    ("CitiPower and United Energy 2025-26", "distributor prices",
     "the pricing proposals put prices in Tariff Summary attachments that are not held (price_attachment_not_held)"),
    ("Evoenergy 2024-25 to 2026-27", "the months of 'winter' and similar seasons",
     "the schedules name the season but never list its months (season_months_not_stated)"),
    ("AusNet, all years", "the standard-time hours of windows stated in 'ADST'",
     "the documents state daylight-saving times only; stored as stated (time_stated_in_daylight_time)"),
    ("All distributors 2023-24 (AER-hosted copies)", "whether the hosted prices are proposed, approved or final",
     "aer.gov.au hosts the distributor's document without stating its status (price_status_unverified)"),
]


def read(table):
    with open(os.path.join(TABLES_DIR, f"{table}.csv"), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def esc(s):
    return html.escape(str(s), quote=True)


def md_cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


class Data:
    def __init__(self):
        self.t = {name: read(name) for name in spec.TABLE_ORDER}
        self.counts = {name: len(rows) for name, rows in self.t.items()}
        self.docs = {d["document_id"]: d for d in self.t["source_document"]}
        self.listings = {l["listing_id"]: l for l in self.t["tariff_listing"]}
        self.instances = Counter(i["exception_code"] for i in self.t["exception_instance"])

    def coverage(self):
        """(distributor, year) -> counts of what the data holds."""
        cov = defaultdict(Counter)
        for c in self.t["charge"]:
            l = self.listings[c["listing_id"]]
            d = self.docs[l["document_id"]]
            cov[(l["tariff_id"].split(":")[0], d["fin_year"])][f"charges_{d['author']}"] += 1
        for table, key in (("tou_schedule", "tou_schedules"), ("demand_rule", "demand_rules")):
            for r in self.t[table]:
                cov[(r["distributor_id"], r["fin_year"])][key] += 1
        for r in self.t["eligibility_rule"]:
            cov[(r["tariff_id"].split(":")[0], r["fin_year"])]["eligibility_rules"] += 1
        for d in self.t["source_document"]:
            if d["distributor_id"]:
                cov[(d["distributor_id"], d["fin_year"])]["documents"] += 1
        for i in self.t["exception_instance"]:
            if i["exception_code"] == "tou_definition_missing":
                cov[(i["distributor_id"], i["fin_year"])]["tou_gaps"] += 1
        return cov

    def version_examples(self, n=4):
        """Jemena 2025-26 components with the largest proposed (v1) to approved (v5) change, largest first."""
        v1, v5 = "aer-consolidated-2025-26-v1", "aer-consolidated-2025-26-v5"
        by_key = defaultdict(dict)
        for c in self.t["charge"]:
            l = self.listings[c["listing_id"]]
            if l["document_id"] in (v1, v5) and l["tariff_id"].startswith("jemena:"):
                by_key[(l["tariff_id"], c["component_label"])][l["document_id"]] = c
        out = []
        for (tid, label), pair in by_key.items():
            if v1 in pair and v5 in pair and Decimal(pair[v1]["value_num"]) > 0:
                a, b = Decimal(pair[v1]["value_num"]), Decimal(pair[v5]["value_num"])
                out.append(((b - a) / a * 100, tid, label, pair[v1], pair[v5]))
        return sorted(out, key=lambda r: (r[0], r[1]))[:n]


def fk_edges():
    return [(t["name"], c["name"], c["references"].split(".")[0]) for t in spec.TABLES for c in t["columns"]
            if c["references"]]


# ---------------------------------------------------------------------------------------------------- markdown
def adjustments_section(data):
    """Documented AER-to-distributor adjustments (price_adjustment) and what they explain in the reconciliation."""
    tariffs = Counter(r["adjustment_id"] for r in data.t["price_adjustment_tariff"])
    out = ["", "## Documented AER-to-distributor adjustments", "",
           "The only differences between an AER price and the distributor's price for the same tariff that the sources "
           "explain. `scripts/adjustments.py` holds the verified scope; the build stores each adjustment with the "
           "document and verbatim quote that state it, and the reconciliation uses the same module, so a difference "
           "outside that scope stays unexplained. `lfit_rebate` records Evoenergy's 2023-24 LFiT rebate, which the "
           "source states only as an average across tariffs, so it explains no individual difference.", "",
           "| Adjustment | Amount | Formula | Tariffs | Evidence |", "|---|---|---|---|---|"]
    for a in sorted(data.t["price_adjustment"], key=lambda a: a["adjustment_id"]):
        amount = f"{a['amount']} {a['amount_unit']}" if a["amount"] else "per tariff"
        out.append(f"| `{a['adjustment_id']}` | {md_cell(amount)} | {md_cell(a['formula'])} | "
                   f"{tariffs[a['adjustment_id']]} | {a['evidence_document_id']} `{md_cell(a['locator'])}` |")
    path = os.path.join(ROOT, "discrepancies.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            differ = Counter(r["explanation"] or "unexplained" for r in csv.DictReader(f)
                             if r["status"] == "value_differs")
        out += ["", f"In the reconciliation (`discrepancies.csv`, REPORT.md) {sum(differ.values())} compared components "
                "differ beyond published rounding: " + ", ".join(f"{k} {v}" for k, v in sorted(
                    differ.items(), key=lambda kv: (kv[0] == "unexplained", -kv[1], kv[0]))) + "."]
    return out


def markdown(data):
    out = ["# Tariff database (data/tariffdb)", "",
           "<!-- Generated by scripts/tariffdb/docs.py from scripts/tariffdb/spec.py, exceptions.py and the built "
           "tables; do not edit by hand. -->", "",
           "A relational, database-ready record of every network tariff the AER and the 14 distributors published "
           "for 2023-24 to 2026-27: rates, requirements, TOU windows, demand rules, with provenance on every row.", "",
           "- Tables: `data/tariffdb/tables/*.csv` (one per table)",
           "- DDL: `data/tariffdb/schema.sqlite.sql`, `data/tariffdb/schema.postgres.sql`; table spec: "
           "`data/tariffdb/schema.json`",
           "- Rebuild: `./run.sh` (parsers) then `.venv/bin/python scripts/tariffdb/build.py`",
           "- Load check: `.venv/bin/python scripts/tariffdb/load.py` (in-memory SQLite, all constraints on)",
           "- Migrate: `.venv/bin/python scripts/tariffdb/load.py --out tariffs.sqlite`, or for PostgreSQL run "
           "`psql -v ON_ERROR_STOP=1 -d <db> -f load.postgres.sql` from `data/tariffdb`",
           "- Tests: `.venv/bin/python -m unittest tests/test_tariffdb.py`", "",
           "## Entity relationships", "", "```mermaid", "erDiagram"]
    for child, column, parent in fk_edges():
        out.append(f"    {parent} ||--o{{ {child} : {column}")
    out += ["```", "", "## Design decisions", "", "| Decision | Reason | Where | Tested by |", "|---|---|---|---|"]
    out += [f"| {md_cell(a)} | {md_cell(b)} | {md_cell(c)} | {md_cell(d)} |" for a, b, c, d in DECISIONS]
    out += ["", "## Exceptions catalogue", "", "| Code | Title | Occurrences | How the data represents it | Test |",
            "|---|---|---|---|---|"]
    for e in catalogue.EXCEPTIONS:
        out.append(f"| `{e['exception_code']}` | {md_cell(e['title'])} | {data.instances[e['exception_code']]} | "
                   f"{md_cell(e['representation'])} | `{e['test'].rsplit('::', 1)[1]}` |")
    out += adjustments_section(data)
    out += ["", "## Coverage by distributor and year", "",
            "| Distributor | Year | Documents | AER charges | Distributor charges | TOU schedules | Demand rules | "
            "Eligibility rules | Tariffs with TOU prices missing matching windows |", "|---|---|---|---|---|---|---|---|---|"]
    cov = data.coverage()
    for d in data.t["distributor"]:
        for fy in spec.FIN_YEARS:
            c = cov[(d["distributor_id"], fy)]
            out.append(f"| {d['name']} | {fy} | {c['documents']} | {c['charges_AER']} | {c['charges_distributor']} | "
                       f"{c['tou_schedules']} | {c['demand_rules']} | {c['eligibility_rules']} | {c['tou_gaps']} |")
    out += ["", "## Known gaps", "", "| Where | Missing | Why |", "|---|---|---|"]
    out += [f"| {md_cell(a)} | {md_cell(b)} | {md_cell(c)} |" for a, b, c in KNOWN_GAPS]
    out += ["", "## Tables", ""]
    for group, names in GROUPS:
        out += [f"### {group}", ""]
        for name in names:
            t = spec.BY_NAME[name]
            derived = " Derived: recomputed from the source-fact tables on every build, not append-only." \
                if t.get("derived") else ""
            out += [f"#### `{name}` ({data.counts[name]} rows)", "", t["description"] + derived, ""]
            out += [f"- Why: {w}" for w in t["why"]]
            out += ["", "| Column | Type | Key | Null | Values / unit | Description |", "|---|---|---|---|---|---|"]
            for c in t["columns"]:
                key = "PK" if c["primary_key"] else ""
                if c["references"]:
                    key = (key + " " if key else "") + f"FK {c['references']}"
                values = ", ".join(c["enum"]) if c["enum"] else (c["unit"] or "")
                desc = ("Derived. " if c.get("derived") else "") + c["description"]
                out.append(f"| `{c['name']}` | {c['type']} | {md_cell(key)} | {'yes' if c['nullable'] else ''} | "
                           f"{md_cell(values)} | {md_cell(desc)} |")
            for k in t.get("unique", []):
                out.append(f"\nUnique: `{', '.join(k)}`")
            for ch in t.get("checks", []):
                out.append(f"\nCheck: `{ch}`")
            out.append("")
    covered = sum(1 for n in spec.TABLE_ORDER for g in GROUPS if n in g[1])
    assert covered == len(spec.TABLE_ORDER), "every table must belong to one group"
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------------------------------- html
KEY_EDGES = [  # structural relationships drawn in the overview (every value table also points at its document)
    ("source_document", "document_series"), ("document_coverage", "source_document"),
    ("document_ingestion", "source_document"), ("tariff_alias", "tariff"),
    ("tariff_relation", "tariff"), ("tariff_listing", "tariff"), ("tariff_listing", "source_document"),
    ("listing_flag", "tariff_listing"), ("charge", "tariff_listing"), ("charge_step", "tariff"),
    ("metering_price", "source_document"), ("price_adjustment_tariff", "price_adjustment"),
    ("price_adjustment_tariff", "metering_price"), ("price_adjustment_tariff", "tariff"),
    ("eligibility_rule", "tariff"), ("tariff_tou", "tariff"), ("tariff_tou", "tou_schedule"),
    ("tou_window", "tou_schedule"), ("tou_window_month", "tou_window"), ("demand_rule", "tou_schedule"),
    ("tariff_demand_rule", "demand_rule"), ("tariff_demand_rule", "tariff"), ("exception_instance", "exception_type"),
]


def er_svg(data):
    col_w, box_w, box_h, gap, top = 236, 188, 40, 14, 58
    pos = {}
    parts = []
    for gi, (group, names) in enumerate(GROUPS):
        x = 16 + gi * col_w
        parts.append(f'<text x="{x}" y="28" class="er-group">{esc(group)}</text>')
        for ni, name in enumerate(names):
            y = top + ni * (box_h + gap)
            pos[name] = (x, y)
    height = top + max(len(n) for _, n in GROUPS) * (box_h + gap) + 10
    width = 16 + len(GROUPS) * col_w
    edges = []
    for child, parent in KEY_EDGES:
        (cx, cy), (px, py) = pos[child], pos[parent]
        if cx == px:  # same column: loop out on the left
            x0, y0, y1 = cx, cy + box_h / 2, py + box_h / 2
            bend = 26 + abs(y1 - y0) / 12
            d = f"M{x0},{y0} C{x0 - bend},{y0} {x0 - bend},{y1} {x0},{y1}"
        else:
            if cx > px:
                x0, x1 = cx, px + box_w
            else:
                x0, x1 = cx + box_w, px
            y0, y1 = cy + box_h / 2, py + box_h / 2
            mid = (x0 + x1) / 2
            d = f"M{x0},{y0} C{mid},{y0} {mid},{y1} {x1},{y1}"
        edges.append(f'<path id="fk-{child}-{parent}" d="{d}" class="er-edge" marker-end="url(#er-arrow)">'
                     f'<title>{esc(child)} references {esc(parent)}</title></path>')
    boxes = []
    for name, (x, y) in pos.items():
        boxes.append(f'<g id="tbl-{name}" class="er-node"><title>{esc(spec.BY_NAME[name]["description"])}</title>'
                     f'<a href="#table-{name}"><rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="7"/>'
                     f'<text x="{x + 10}" y="{y + 17}" class="er-name">{esc(name)}</text>'
                     f'<text x="{x + 10}" y="{y + 32}" class="er-count">{data.counts[name]:,} rows</text></a></g>')
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Entity relationship overview" '
            f'class="er" xmlns="http://www.w3.org/2000/svg"><defs><marker id="er-arrow" viewBox="0 0 10 10" refX="9" '
            f'refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
            f'class="er-arrowhead"/></marker></defs>{"".join(parts)}{"".join(edges)}{"".join(boxes)}</svg>')


def trace_svg(ex):
    """How one AER v1 price and its approved v5 price coexist, each traced to its own file and cell."""
    _, tid, label, a, b = ex
    steps = [("tariff", tid), ("tariff_listing", "v1 listing | v5 listing"), ("charge", label),
             ("source_document", "v1 | v5: URL + SHA-256"),
             ("locator", " | ".join(x["locator"].rsplit("!", 1)[1] for x in (a, b)))]
    w, h = 1180, 76
    parts = []
    for i, (k, v) in enumerate(steps):
        x = 10 + i * 236
        parts.append(f'<g id="trace-{k}"><rect x="{x}" y="10" width="212" height="56" rx="8" class="tr-box"/>'
                     f'<text x="{x + 10}" y="32" class="tr-k">{esc(k)}</text>'
                     f'<text x="{x + 10}" y="52" class="tr-v">{esc(v if len(v) <= 28 else v[:27] + "…")}</text><title>{esc(v)}</title></g>')
        if i:
            parts.append(f'<path d="M{x - 22},38 L{x - 4},38" class="er-edge" marker-end="url(#tr-arrow)"/>')
    return (f'<svg viewBox="0 0 {w} {h}" class="er" role="img" aria-label="Provenance chain of one price" '
            f'xmlns="http://www.w3.org/2000/svg"><defs><marker id="tr-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" class="er-arrowhead"/>'
            f'</marker></defs>{"".join(parts)}</svg>')


def html_page(data):
    examples = data.version_examples()
    cov = data.coverage()
    total_rows = sum(data.counts.values())
    stats = [("Tables", len(spec.TABLES)), ("Rows", f"{total_rows:,}"), ("Charges", f"{data.counts['charge']:,}"),
             ("Source documents", data.counts["source_document"]), ("Distributors", data.counts["distributor"]),
             ("Years", len(spec.FIN_YEARS)), ("Exception types", data.counts["exception_type"])]
    h = ['<!doctype html><html lang="en" data-theme="luxury"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         '<title>Tariff database schema</title>',
         '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/daisyui@5.5.19/daisyui.css">',
         '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/daisyui@5.5.19/themes.css">',
         '<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4.2.4/dist/index.global.js"></script>',
         """<style>
  *, *::before, *::after { box-sizing: border-box; }
  :where(.grid, .flex) > * { min-width: 0; }
  :where(p, li, .badge) { overflow-wrap: anywhere; }
  :where(td, th) { overflow-wrap: break-word; }
  body { background: var(--color-base-100); color: var(--color-base-content); }
  svg.er { width: 100%; height: auto; display: block; }
  .er-group { font: 600 13px ui-sans-serif, system-ui; fill: var(--color-primary); }
  .er-node rect { fill: var(--color-base-200); stroke: var(--color-base-content); stroke-opacity: .35; }
  .er-node:hover rect { stroke: var(--color-primary); stroke-opacity: 1; }
  .er-name { font: 600 12.5px ui-monospace, monospace; fill: var(--color-base-content); }
  .er-count { font: 11px ui-sans-serif, system-ui; fill: var(--color-base-content); fill-opacity: .6; }
  .er-edge { fill: none; stroke: var(--color-base-content); stroke-opacity: .35; stroke-width: 1.3; }
  .er-edge:hover { stroke: var(--color-primary); stroke-opacity: 1; }
  .er-arrowhead { fill: var(--color-base-content); fill-opacity: .55; }
  .tr-box { fill: var(--color-base-200); stroke: var(--color-primary); stroke-opacity: .6; }
  .tr-k { font: 600 12px ui-monospace, monospace; fill: var(--color-primary); }
  .tr-v { font: 11.5px ui-monospace, monospace; fill: var(--color-base-content); }
  code { font-size: .85em; }
  td, th { vertical-align: top; }
</style></head>""",
         '<body><main class="max-w-7xl mx-auto px-4 py-8 space-y-10">',
         '<header class="space-y-3"><h1 class="text-3xl font-bold">Tariff database: schema and design</h1>',
         '<ul class="list-disc pl-6 opacity-90">'
         '<li>Question answered: how is every AER and distributor network tariff stored, 2023-24 to 2026-27, so it '
         'can move into any database, stays historical, and every value can be re-checked by hand?</li>'
         f'<li>Answer: {len(spec.TABLES)} relational tables as CSV files with generated SQLite/PostgreSQL DDL; every value row is tied '
         'to a year, explicit dates, a document version and a cell or page.</li>'
         '<li>Repo copy: <code>docs/tariffdb.md</code>; source of truth: <code>scripts/tariffdb/spec.py</code>.</li>'
         '</ul>',
         '<div class="stats stats-vertical sm:stats-horizontal shadow bg-base-200 w-full">']
    h += [f'<div class="stat"><div class="stat-title">{esc(k)}</div><div class="stat-value text-2xl">{esc(v)}</div>'
          f'</div>' for k, v in stats]
    h += ['</div></header>']
    # ER overview
    h += ['<section id="er" class="space-y-3"><h2 class="text-2xl font-semibold">Entity relationships</h2>',
          '<ul class="list-disc pl-6 text-sm opacity-80"><li>Columns = groups; arrows point from the referencing '
          'table to the referenced one; click a table for its columns.</li>'
          '<li>Not drawn to keep it readable: every value table also references <code>source_document</code>, <code>distributor</code> '
          '(provenance) and <code>financial_year</code>, and <code>exception_instance</code> points at the affected tariff, listing, charge or document; the full FK list is in each table card and in '
          '<code>docs/tariffdb.md</code>.</li></ul>',
          f'<div class="rounded-box border border-base-content/10 p-3 bg-base-100 overflow-x-auto">{er_svg(data)}'
          '</div></section>']
    # history example
    if examples:
        h += ['<section id="history" class="space-y-3"><h2 class="text-2xl font-semibold">History: proposed and '
              'approved side by side</h2>',
              '<ul class="list-disc pl-6 text-sm opacity-80"><li>Same tariff, same component, two AER versions: '
              'neither overwrites the other; each traces to its own file and cell.</li>'
              '<li>Jemena 2025-26 components with the largest change from proposed v1 (reopener application '
              'included) to approved v5; each pair below is two charge rows.</li></ul>',
              f'<div class="rounded-box border border-base-content/10 p-3 overflow-x-auto">{trace_svg(examples[0])}'
              '</div>',
              '<div class="overflow-x-auto rounded-box border border-base-content/10"><table class="table table-sm">'
              '<thead><tr><th>Tariff</th><th>Component</th><th>v1 (proposed)</th><th>v5 (approved)</th>'
              '<th class="text-right">Change</th><th>Locators v1 / v5</th></tr></thead><tbody>']
        for pct, tid, label, a, b in examples:
            h.append(f'<tr><td><code>{esc(tid)}</code></td><td>{esc(label)}</td>'
                     f'<td>{esc(a["value_published"])} {esc(a["unit_published"])}</td>'
                     f'<td>{esc(b["value_published"])} {esc(b["unit_published"])}</td>'
                     f'<td class="text-right">{pct:.1f}%</td>'
                     f'<td><code>{esc(a["locator"])}</code> / <code>{esc(b["locator"])}</code></td></tr>')
        h += ['</tbody></table></div></section>']
    # decisions
    h += ['<section id="decisions" class="space-y-3"><h2 class="text-2xl font-semibold">Design decisions and why</h2>',
          '<div class="overflow-x-auto rounded-box border border-base-content/10"><table class="table table-sm '
          'table-zebra"><thead><tr><th>#</th><th>Decision</th><th>Reason</th><th>Where</th><th>Tested by</th></tr>'
          '</thead><tbody>']
    for i, (a, b, c, d) in enumerate(DECISIONS, 1):
        h.append(f'<tr id="decision-{i}"><td>{i}</td><td class="font-medium">{esc(a)}</td><td>{esc(b)}</td>'
                 f'<td><code>{esc(c)}</code></td><td><code>{esc(d)}</code></td></tr>')
    h += ['</tbody></table></div></section>']
    # exceptions
    h += ['<section id="exceptions" class="space-y-3"><h2 class="text-2xl font-semibold">Exceptions catalogue</h2>',
          '<ul class="list-disc pl-6 text-sm opacity-80"><li>Every irregularity found in the sources, how the '
          'schema represents it, how many occurrences the data holds, and the test that exercises it.</li></ul>',
          '<div class="overflow-x-auto rounded-box border border-base-content/10"><table class="table table-sm '
          'table-zebra"><thead><tr><th>Exception</th><th>In the sources</th><th>Represented by</th>'
          '<th class="text-right">Rows</th><th>Test</th></tr></thead><tbody>']
    for e in catalogue.EXCEPTIONS:
        h.append(f'<tr id="exc-{esc(e["exception_code"])}"><td><div class="font-medium">{esc(e["title"])}</div>'
                 f'<code class="opacity-70">{esc(e["exception_code"])}</code></td><td>{esc(e["description"])}</td>'
                 f'<td>{esc(e["representation"])}</td><td class="text-right">'
                 f'{data.instances[e["exception_code"]]:,}</td><td><code>{esc(e["test"].rsplit("::", 1)[1])}</code>'
                 f'</td></tr>')
    h += ['</tbody></table></div></section>']
    # coverage
    h += ['<section id="coverage" class="space-y-3"><h2 class="text-2xl font-semibold">Coverage by distributor and '
          'year</h2><ul class="list-disc pl-6 text-sm opacity-80"><li>Charges per side, plus the rule tables. '
          'A zero is a gap the sources have (see the exceptions), not a dropped row.</li></ul>',
          '<div class="overflow-x-auto rounded-box border border-base-content/10"><table class="table table-xs '
          'table-zebra"><thead><tr><th>Distributor</th><th>Year</th><th class="text-right">Docs</th>'
          '<th class="text-right">AER charges</th><th class="text-right">Distributor charges</th>'
          '<th class="text-right">TOU schedules</th><th class="text-right">Demand rules</th>'
          '<th class="text-right">Eligibility rules</th><th class="text-right">TOU gaps</th></tr></thead><tbody>']
    for d in data.t["distributor"]:
        for fy in spec.FIN_YEARS:
            c = cov[(d["distributor_id"], fy)]
            cells = [c["documents"], c["charges_AER"], c["charges_distributor"], c["tou_schedules"],
                     c["demand_rules"], c["eligibility_rules"]]
            h.append(f'<tr><td>{esc(d["name"])}</td><td class="whitespace-nowrap">{fy}</td>' + "".join(
                f'<td class="text-right{" text-warning" if v == 0 else ""}">{v:,}</td>' for v in cells)
                + f'<td class="text-right{" text-warning" if c["tou_gaps"] else ""}">{c["tou_gaps"]:,}</td></tr>')
    h += ['</tbody></table></div>',
          '<h3 class="text-xl font-semibold">Known gaps</h3>',
          '<ul class="list-disc pl-6 text-sm opacity-80"><li>What the held sources cannot supply. Each is recorded as '
          'exception rows, never filled by guessing.</li><li>TOU gaps above = tariffs with peak/off-peak prices but no '
          'window for the charge kind and band in that year (<code>tou_definition_missing</code>).</li></ul>',
          '<div class="overflow-x-auto rounded-box border border-base-content/10"><table class="table table-sm '
          'table-zebra"><thead><tr><th>Where</th><th>Missing</th><th>Why</th></tr></thead><tbody>']
    h += [f'<tr><td class="font-medium">{esc(a)}</td><td>{esc(b)}</td><td>{esc(c)}</td></tr>' for a, b, c in KNOWN_GAPS]
    h += ['</tbody></table></div></section>']
    # tables
    h += ['<section id="tables" class="space-y-6"><h2 class="text-2xl font-semibold">Tables</h2>']
    for group, names in GROUPS:
        h.append(f'<h3 class="text-xl font-semibold text-primary">{esc(group)}</h3>')
        for name in names:
            t = spec.BY_NAME[name]
            h += [f'<div id="table-{name}" class="card card-border bg-base-200"><div class="card-body gap-3">',
                  f'<h4 class="card-title font-mono">{esc(name)} <span class="badge badge-soft badge-primary">'
                  f'{data.counts[name]:,} rows</span>'
                  + ('<span class="badge badge-outline badge-sm">derived</span>' if t.get("derived") else "")
                  + '</h4>',
                  f'<ul class="list-disc pl-6"><li>{esc(t["description"])}</li>'
                  + ('<li>Derived: recomputed from the source-fact tables on every build, not append-only.</li>'
                     if t.get("derived") else "")
                  + "".join(f'<li><span class="font-medium">Why:</span> {esc(w)}</li>' for w in t["why"]) + '</ul>',
                  '<div class="overflow-x-auto rounded-box border border-base-content/10 bg-base-100"><table '
                  'class="table table-xs"><thead><tr><th>Column</th><th>Type</th><th>Key</th><th>Null</th>'
                  '<th>Values / unit</th><th>Description</th></tr></thead><tbody>']
            for c in t["columns"]:
                key = []
                if c["primary_key"]:
                    key.append('<span class="badge badge-xs badge-primary">PK</span>')
                if c["references"]:
                    rt = c["references"].split(".")[0]
                    key.append(f'<a class="link" href="#table-{rt}">FK {esc(c["references"])}</a>')
                values = ", ".join(c["enum"]) if c["enum"] else (c["unit"] or "")
                h.append(f'<tr><td><code>{esc(c["name"])}</code></td><td>{esc(c["type"])}</td>'
                         f'<td>{" ".join(key)}</td><td>{"yes" if c["nullable"] else ""}</td>'
                         f'<td class="text-xs">{esc(values)}</td><td>{"<em>Derived.</em> " if c.get("derived") else ""}'
                         f'{esc(c["description"])}</td></tr>')
            h.append('</tbody></table></div>')
            extra = [f'Unique: <code>{esc(", ".join(k))}</code>' for k in t.get("unique", [])]
            extra += [f'Check: <code>{esc(ch)}</code>' for ch in t.get("checks", [])]
            if extra:
                h.append('<ul class="list-disc pl-6 text-sm opacity-80">' + "".join(f"<li>{x}</li>" for x in extra)
                         + '</ul>')
            h.append('</div></div>')
    h += ['</section>']
    # how to re-check
    h += ['<section id="recheck" class="space-y-3"><h2 class="text-2xl font-semibold">How to re-check</h2>',
          '<ul class="list-disc pl-6">',
          '<li>Rebuild: <code>./run.sh</code> then <code>.venv/bin/python scripts/tariffdb/build.py</code> '
          '(deterministic: same inputs give byte-identical CSVs)</li>',
          '<li>Import check: <code>.venv/bin/python scripts/tariffdb/load.py</code></li>',
          '<li>Migrate: <code>load.py --out tariffs.sqlite</code>, or PostgreSQL: <code>psql -v ON_ERROR_STOP=1 -d '
          '&lt;db&gt; -f load.postgres.sql</code> from <code>data/tariffdb</code></li>',
          '<li>All tests (re-read every number and quote from the source files): '
          '<code>.venv/bin/python -m unittest tests/test_tariffdb.py</code></li>',
          '<li>One value by hand: <code>charge.locator</code> = <code>xlsx:&lt;sheet&gt;!&lt;cell&gt;</code> or '
          '<code>pdf:p&lt;page&gt;</code> in <code>source_document.local_path</code> (SHA-256 in '
          '<code>source_document.sha256</code>)</li>',
          '<li>One rule by hand: the <code>quote</code> column is verbatim text at its <code>locator</code></li>',
          '<li>History guard: <code>build.py --check-append-only &lt;git ref&gt;</code> fails if a committed row '
          'of a source-fact table changed or vanished (derived tables are recomputed and not compared)</li></ul>'
          '</section>',
          '</main></body></html>']
    return "\n".join(h) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", help="also write the visual report to this path")
    args = ap.parse_args()
    data = Data()
    os.makedirs(os.path.dirname(MD_PATH), exist_ok=True)
    with open(MD_PATH, "w", encoding="utf-8") as f:
        f.write(markdown(data))
    print(f"wrote {os.path.relpath(MD_PATH, ROOT)}")
    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(html_page(data))
        print(f"wrote {args.html}")


if __name__ == "__main__":
    main()
