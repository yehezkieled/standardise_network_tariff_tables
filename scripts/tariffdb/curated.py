"""Hand-curated facts that no price table carries: TOU windows, eligibility criteria and consumption-block bounds. One
YAML file per distributor under data/tariffdb/curated/; scripts/tariffdb/build.py loads them into the tou_window and
eligibility tables and the block_* columns of rate.

Every fact quotes its source verbatim at a locator; `validate()` re-reads each quote from the source file (on word
boundaries), checks enums, times and months, and checks that schedules marked covers_full_day tile 24 hours per day
type without overlap. A fact whose code names no tariff with rates in that year is not loaded;
scripts/tariffdb/build.py --verbose lists each one.

  .venv/bin/python scripts/tariffdb/curated.py data/tariffdb/curated/<distributor>.yaml

File format (keys in [] are optional):

distributor: <distributor_id>
tou_schedules:
  - id: <slug, unique in the file, start with the distributor id>
    doc: <repo-relative source path, as in sources/inventory.csv>
    fin_year: 2025-26
    name: <what the windows are for>
    time_basis: local_time | standard_time | daylight_time | not_stated   (daylight_time: stated as e.g. 'ADST')
    public_holidays: as_weekday | as_non_business_day | unchanged | not_stated
    covers_full_day: true | false        # true only when the windows partition each listed day type over 24h
    locator: pdf:p7 | xlsx:<sheet>!<cell>
    quote: <verbatim wording at the locator that states the windows>
    [note: ...]
    windows:
      - {period: <spec.TOU_PERIODS>, label: <as published>, days: <spec.DAY_TYPES>, start: "HH:MM", end: "HH:MM",
         [months: "all" | "11,12,1,2,3" | not_stated], [season: <spec.SEASONS>, season_label: <as published>],
         [locator: ..., quote: ...]}
    (period is the rate.tou_period the window prices, so windows and rates join; season is the rate.season it belongs
    to, with the season named as published in season_label; omit months only when the source names no season;
    not_stated = the source names a season but not its months)
    tariffs:
      - {codes: [<code>, ...], applies_to: <spec.TOU_APPLIES>, [locator, quote]}
eligibility:
  - {codes: [...], doc, fin_year, rule_type: <spec.CRITERIA>, [operator: <spec.OPERATORS>], [value_num: 40],
     [value_unit: MWh/yr], [value_text: <spec.CRITERION_VALUES[rule_type] when listed>], [target_code: <code>],
     locator, quote, [column_locator, column_quote], [note]}
    (column_* quotes the column header when the quote is a bare table row, e.g. a 'Yes' under 'Closed to New Entrants')
steps:
  - {codes: [...], doc, fin_year, step_group: <quantity as named>, component_label, step_index: 1, lower_bound: 0,
     [upper_bound: 60], lower_inclusive: true, upper_inclusive: true, quantity_unit: kWh,
     reset_period: <RESET_PERIODS>, locator, quote}
    (bounds go onto the usage rates with block = step_index, or the export rates when step_group names export)
conditions:
  - {codes: [...], doc, fin_year, component: <rate.component exactly as stored>, condition: opt_in:<name> |
     meter_type:<spec.CRITERION_VALUES['meter_type']>, locator, quote, [note]}
    (a price charged only when the site meets the condition, e.g. a rebate for customers who join a trial; the
    quote states the condition. Every rate of those codes and year with that component gets rate.condition)
metering:
  - {codes: [...] | all, doc, fin_year, schedule: <component of a metering schedule row (no tariff code) in
     out/dnsp_metering>, [condition], locator, quote, [note]}
    (which tariffs a network-wide metering schedule price applies to, and to which sites: the quote states it; all =
    every tariff of that year, for a charge per NMI. Each tariff period whose rates come from the schedule's own
    document gets a metering rate, with the condition)
rate_periods:
  - {codes: [...], doc, fin_year, component: <rate.component exactly as stored>, [tou_period: <spec.RATE_PERIODS>],
     [season: <spec.SEASONS>], locator, quote, [note]}
    (the period of a rate the price list prints without one, where the tariff's windows name several: e.g. an
    export charge the document applies in the solar soak window; or the season of a rate whose price list column
    names another, where the document defines it: e.g. a 'Summer incentive' column that prices a winter incentive
    for tariffs ending in 3. Every rate of those codes and year with that component gets them; a period or season
    the price list stated otherwise is replaced and the rate's note says so)
charge_rules:
  - {codes: [...] | all, doc, fin_year, charge_type: <spec.RULE_CHARGES>, [tou_period: <spec.RATE_PERIODS>],
     [season: <spec.SEASONS>], measure: <spec.RULE_MEASURES>, [interval_min: 30], method: <spec.RULE_METHODS>, [n: 4],
     reset: <spec.RULE_RESETS>, [minimum_value], [threshold_value], [allowance_per_day], [allowance_rollover],
     locator, quote, [note]}
    (how the demand, capacity or export quantity is measured; omit tou_period / season for a rule that holds for every
    rate of the charge type. `codes: all` = a rule the document states for every tariff (a glossary definition): it
    covers each code of that year with a rate of the charge type that no rule naming the code covers)

Any fact's fin_year may be a list ([2019-20, 2020-21]) when its document states it for each of those pricing years,
e.g. a tariff structure statement for its regulatory period; Victoria's calendar years are 2017 .. 2020 and 2021-H1.
"""
import glob
import os
import re
import sys
from decimal import Decimal

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import locators  # noqa: E402
import spec  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
CURATED_DIR = os.path.join(ROOT, "data", "tariffdb", "curated")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$|^24:00$")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# concrete days each day type covers, for the 24-hour coverage test (public holidays are checked as their own day)
DAY_TYPE_DAYS = {"weekday": DAYS[:5], "business_day": DAYS[:5], "weekend": DAYS[5:], "non_business_day": DAYS[5:],
                 "all_days": DAYS}


SECTIONS = ("distributor", "tou_schedules", "eligibility", "steps", "conditions", "metering", "rate_periods",
            "charge_rules")
# day: each day stands alone; billing_period_per_day: per-day bounds multiplied by the days in the billing period (an
# unused allowance rolls over within the period); quarter: bounds accumulate per calendar quarter; unstated
RESET_PERIODS = ("day", "billing_period_per_day", "quarter", "unstated")
BOUNDARY_RULES = {"consumption_min", "consumption_max", "demand_min", "demand_max"}


def boundary_operators(quote, value, unit):
    quote = str(quote).lower().translate(str.maketrans({"\uf0b3": "≥", "\uf0a3": "≤", "\uf020": " "}))
    number = Decimal(str(value).replace(",", ""))
    unit = (unit or "").split("/")[0].lower()
    operators = set()
    phrases = {"ge": r">=|≥|\bat least\b|\bno less than\b|\bminimum\b",
               "gt": r">(?![=])|(?<!no )\bmore\b|\bgreater\b|\bover\b|\babove\b|\bexceeds\b|\bin excess of\b",
               "le": r"<=|≤|no more than|up to(?: and including)?|does not exceed|do not exceed",
               "lt": r"<(?![=])|(?<!no )\bless\b"}
    scales = {"mwh": 1, "gwh": 1000, "kwh": Decimal("0.001"), "kva": 1, "mva": 1000, "kw": 1, "mw": 1000}
    def matches(raw, published_unit):
        published_unit = re.sub(r"\s", "", published_unit).lower()
        if unit == published_unit:
            return Decimal(raw.replace(",", "")) == number
        groups = ({"mwh", "gwh", "kwh"}, {"kva", "mva"}, {"kw", "mw"})
        if any(unit in g and published_unit in g for g in groups):
            return Decimal(raw.replace(",", "")) * scales[published_unit] == number * scales[unit]
        return False
    digits, unit_text = r"[0-9][0-9,]*(?:\.[0-9]+)?", r"(?:[kmg]\s*)?(?:w\s*h|v\s*a|w|v)"
    quantity = rf"({digits})\s*({unit_text})"
    gap = rf"(?:(?!{digits}\s*{unit_text})[^<>≥≤])*?"
    for op, phrase in phrases.items():
        for m in re.finditer(r"(?:" + phrase + r")" + gap + quantity, quote):
            if matches(m[1], m[2]):
                operators.add(op)
        for m in re.finditer(r"(?:" + phrase + r")" + gap + quantity + r"\s+or\s+" + quantity, quote):
            if matches(m[3], m[4]):
                operators.add(op)
    for m in re.finditer(quantity + r"(?:\s*/?\s*(?:per year|per annum|pa|p\.a\.))?\s+or (more|less)", quote):
        if matches(m[1], m[2]):
            operators.add("ge" if m[3] == "more" else "le")
    return operators


def per_year(data):
    """The file with each fact whose fin_year is a list split into one fact per year (what the build loads)."""
    return {k: [dict(f, fin_year=str(y)) for f in v for y in (f["fin_year"] if isinstance(f.get("fin_year"), list)
                                                         else [f.get("fin_year")])]
            if isinstance(v, list) else v for k, v in data.items()}


def minutes(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def months_of(spec_months):
    """Month numbers a window applies in; None when the source names a season without listing its months."""
    if spec_months == "not_stated":
        return None
    if spec_months in (None, "", "all"):
        return list(range(1, 13))
    out = [int(x) for x in str(spec_months).split(",")]
    if not out or any(m < 1 or m > 12 for m in out) or len(set(out)) != len(out):
        raise ValueError(f"bad months {spec_months!r}")
    return out


def load(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_all():
    return {os.path.basename(p)[:-5]: load(p) for p in sorted(glob.glob(os.path.join(CURATED_DIR, "*.yaml")))}


def _req(errors, where, obj, keys):
    for k in keys:
        if obj.get(k) in (None, ""):
            errors.append(f"{where}: missing {k}")


def _enum(errors, where, value, allowed, key):
    if value is not None and value not in allowed:
        errors.append(f"{where}: {key}={value!r} not in {allowed}")


def _quote(errors, where, obj, default_doc):
    doc = obj.get("doc", default_doc)
    loc, quote = obj.get("locator"), obj.get("quote")
    if not (loc and quote):
        return
    try:
        locators.parse(loc)
    except ValueError as e:
        errors.append(f"{where}: {e}")
        return
    ok, why = locators.verify_quote(os.path.join(ROOT, doc), loc, str(quote))
    if not ok:
        errors.append(f"{where}: quote check failed ({why}): {str(quote)[:80]!r}")


def coverage_errors(windows):
    """For each concrete day and month the windows must tile 00:00-24:00 exactly once (no gap, no overlap)."""
    errs = []
    by = {}
    for w in windows:
        if months_of(w.get("months")) is None:
            return [f"window {w.get('period')} {w.get('start')}-{w.get('end')} has months not_stated"]
        for d in DAY_TYPE_DAYS[w["days"]]:
            for m in months_of(w.get("months")):
                by.setdefault((d, m), []).append((minutes(w["start"]), minutes(w["end"]), w.get("period")))
    for d in DAYS:
        for m in range(1, 13):
            if (d, m) not in by:
                errs.append(f"{d} month {m}: no windows")
    for (d, m), spans in sorted(by.items()):
        spans.sort()
        t = 0
        for s, e, p in spans:
            if s != t:
                errs.append(f"{d} month {m}: {'gap' if s > t else 'overlap'} at {t // 60:02d}:{t % 60:02d}")
                break
            t = e
        else:
            if t != 1440:
                errs.append(f"{d} month {m}: day ends at {t // 60:02d}:{t % 60:02d}, not 24:00")
    return errs


def condition_errors(where, condition):
    """rate.condition is <kind>:<value>[|<value>...]: met when the site's value is any of them."""
    kind, _, values = str(condition or "").partition(":")
    if kind not in spec.CONDITION_KINDS or not re.fullmatch(r"[a-z0-9_]+(\|[a-z0-9_]+)*", values):
        return [f"{where}: condition {condition!r} is not <{'|'.join(spec.CONDITION_KINDS)}>:<name>[|<name>...]"]
    if kind == "meter_type":
        return [f"{where}: meter type {v!r} not in {spec.CRITERION_VALUES['meter_type']}"
                for v in values.split("|") if v not in spec.CRITERION_VALUES["meter_type"]]
    return []


def validate(data, check_quotes=True):
    errors = [f"unknown section {k!r} (sections: {', '.join(SECTIONS)})" for k in data if k not in SECTIONS]
    did = data.get("distributor")
    if did is None:
        return ["missing distributor"]
    sched_ids = set()
    import build_support  # the documents the database registers: sources/inventory.csv and the archive's price documents
    inv = {d["local_path"]: d for d in build_support.documents() if d["local_path"]}

    def check_doc(where, obj):
        if obj.get("doc") not in inv:
            errors.append(f"{where}: doc {obj.get('doc')!r} is not a retrieved price document in sources/inventory.csv "
                          f"or sources/archive/inventory.csv")
        years = obj.get("fin_year") if isinstance(obj.get("fin_year"), list) else [obj.get("fin_year")]
        if not years or len(set(years)) != len(years) or any(str(y) not in spec.PRICING_YEARS for y in years):
            errors.append(f"{where}: fin_year {obj.get('fin_year')!r}")

    for i, s in enumerate(data.get("tou_schedules") or []):
        where = f"tou_schedules[{i}] {s.get('id')}"
        _req(errors, where, s, ["id", "doc", "fin_year", "name", "time_basis", "public_holidays", "locator", "quote",
                                "windows"])
        if s.get("id") in sched_ids:
            errors.append(f"{where}: duplicate id")
        sched_ids.add(s.get("id"))
        check_doc(where, s)
        _enum(errors, where, s.get("time_basis"), spec.TIME_BASES, "time_basis")
        _enum(errors, where, s.get("public_holidays"), spec.HOLIDAY_RULES, "public_holidays")
        if check_quotes:
            _quote(errors, where, s, s.get("doc"))
        for j, w in enumerate(s.get("windows") or []):
            ww = f"{where} window[{j}]"
            _req(errors, ww, w, ["period", "label", "days", "start", "end"])
            _enum(errors, ww, w.get("period"), spec.TOU_PERIODS, "period")
            _enum(errors, ww, w.get("days"), spec.DAY_TYPES, "days")
            for k in ("start", "end"):
                if not TIME_RE.match(str(w.get(k, ""))):
                    errors.append(f"{ww}: {k}={w.get(k)!r} is not HH:MM")
            times_ok = TIME_RE.match(str(w.get("start", ""))) and TIME_RE.match(str(w.get("end", "")))
            if times_ok and minutes(w["start"]) >= minutes(w["end"]):
                errors.append(f"{ww}: start must be before end (split windows that cross midnight)")
            try:
                if months_of(w.get("months")) is None and not w.get("season_label"):
                    errors.append(f"{ww}: months not_stated needs the season name as published (season_label)")
                if w.get("season_label") and "months" not in w:
                    errors.append(f"{ww}: a window with a season needs months (the months listed, or not_stated)")
                if w.get("season") and not w.get("season_label"):
                    errors.append(f"{ww}: season needs the season name as published (season_label)")
                _enum(errors, ww, w.get("season"), spec.SEASONS, "season")
            except ValueError as e:
                errors.append(f"{ww}: {e}")
            if check_quotes and w.get("quote"):
                _quote(errors, ww, w, s.get("doc"))
        if s.get("covers_full_day") and not errors:
            for e in coverage_errors(s.get("windows") or []):
                errors.append(f"{where}: covers_full_day but {e}")
        for j, t in enumerate(s.get("tariffs") or []):
            tw = f"{where} tariffs[{j}]"
            _req(errors, tw, t, ["codes", "applies_to"])
            _enum(errors, tw, t.get("applies_to"), spec.TOU_APPLIES, "applies_to")
            if check_quotes and t.get("quote"):
                _quote(errors, tw, t, s.get("doc"))
    for i, r in enumerate(data.get("eligibility") or []):
        where = f"eligibility[{i}] {r.get('codes')} {r.get('rule_type')}"
        _req(errors, where, r, ["codes", "doc", "fin_year", "rule_type", "locator", "quote"])
        check_doc(where, r)
        _enum(errors, where, r.get("rule_type"), spec.CRITERIA, "rule_type")
        _enum(errors, where, r.get("operator"), spec.OPERATORS, "operator")
        allowed = spec.CRITERION_VALUES.get(r.get("rule_type"))
        if allowed and r.get("value_text") is not None and r["value_text"] not in allowed:
            errors.append(f"{where}: value_text={r['value_text']!r} not in {allowed}")
        if r.get("value_num") is None and not r.get("value_text") and not r.get("target_code"):
            errors.append(f"{where}: needs value_num, value_text or target_code")
        if r.get("value_num") is not None and not r.get("operator"):
            errors.append(f"{where}: value_num needs an operator")
        if r.get("rule_type") in BOUNDARY_RULES and r.get("value_num") is not None:
            op = r.get("operator")
            unknown = "ge_unstated" if r["rule_type"].endswith("_min") else "le_unstated"
            if op in ("ge_unstated", "le_unstated"):
                if op != unknown:
                    errors.append(f"{where}: unknown boundary operator has the wrong direction")
            elif op not in ({"ge", "gt"} if r["rule_type"].endswith("_min") else {"le", "lt"}):
                errors.append(f"{where}: numeric boundary operator has the wrong direction")
            elif op not in boundary_operators(r.get("quote", ""), r["value_num"], r.get("value_unit")):
                errors.append(f"{where}: operator {op!r} is not supported by the quoted boundary")
        elif r.get("operator") in ("ge_unstated", "le_unstated"):
            errors.append(f"{where}: unstated boundary operator needs a numeric boundary rule")
        if bool(r.get("column_locator")) != bool(r.get("column_quote")):
            errors.append(f"{where}: column_locator and column_quote go together")
        if check_quotes:
            _quote(errors, where, r, r.get("doc"))
            if r.get("column_locator"):
                _quote(errors, where + " column", {"locator": r["column_locator"], "quote": r["column_quote"]},
                       r.get("doc"))
    for i, r in enumerate(data.get("steps") or []):
        where = f"steps[{i}] {r.get('codes')} {r.get('step_group')} #{r.get('step_index')}"
        _req(errors, where, r, ["codes", "doc", "fin_year", "step_group", "component_label", "step_index",
                                "lower_inclusive", "upper_inclusive", "quantity_unit", "reset_period", "locator",
                                "quote"])
        check_doc(where, r)
        _enum(errors, where, r.get("quantity_unit"), ["kWh"], "quantity_unit")
        _enum(errors, where, r.get("reset_period"), RESET_PERIODS, "reset_period")
        if not isinstance(r.get("step_index"), int) or r["step_index"] < 1:
            errors.append(f"{where}: step_index must be an integer >= 1")
        if r.get("upper_bound") is not None and r.get("lower_bound") is not None and \
                not r["lower_bound"] < r["upper_bound"]:
            errors.append(f"{where}: lower_bound must be below upper_bound")
        if check_quotes:
            _quote(errors, where, r, r.get("doc"))
    for i, r in enumerate(data.get("conditions") or []):
        where = f"conditions[{i}] {r.get('codes')} {r.get('component')}"
        _req(errors, where, r, ["codes", "doc", "fin_year", "component", "condition", "locator", "quote"])
        check_doc(where, r)
        errors += condition_errors(where, r.get("condition"))
        if check_quotes:
            _quote(errors, where, r, r.get("doc"))
    for i, r in enumerate(data.get("metering") or []):
        where = f"metering[{i}] {r.get('codes')} {r.get('schedule')}"
        _req(errors, where, r, ["codes", "doc", "fin_year", "schedule", "locator", "quote"])
        check_doc(where, r)
        if r.get("condition") is not None:
            errors += condition_errors(where, r["condition"])
        if check_quotes:
            _quote(errors, where, r, r.get("doc"))
    for i, r in enumerate(data.get("rate_periods") or []):
        where = f"rate_periods[{i}] {r.get('codes')} {r.get('component')}"
        _req(errors, where, r, ["codes", "doc", "fin_year", "component", "locator", "quote"])
        check_doc(where, r)
        if r.get("tou_period") is None and r.get("season") is None:
            errors.append(f"{where}: states neither tou_period nor season")
        _enum(errors, where, r.get("tou_period"), spec.RATE_PERIODS, "tou_period")
        _enum(errors, where, r.get("season"), spec.SEASONS, "season")
        if check_quotes:
            _quote(errors, where, r, r.get("doc"))
    for i, r in enumerate(data.get("charge_rules") or []):
        where = f"charge_rules[{i}] {r.get('codes')} {r.get('charge_type')} {r.get('tou_period') or ''}"
        _req(errors, where, r, ["codes", "doc", "fin_year", "charge_type", "measure", "method", "reset", "locator",
                                "quote"])
        check_doc(where, r)
        for key, allowed in (("charge_type", spec.RULE_CHARGES), ("tou_period", spec.RATE_PERIODS),
                             ("season", spec.SEASONS), ("measure", spec.RULE_MEASURES),
                             ("method", spec.RULE_METHODS), ("reset", spec.RULE_RESETS)):
            _enum(errors, where, r.get(key), allowed, key)
        if (r.get("n") is None) != (r.get("method") not in spec.RULE_N_METHODS):
            errors.append(f"{where}: n goes with the methods {spec.RULE_N_METHODS}, and only with them")
        if r.get("method") == "kva_at_max_kw" and r.get("measure") != "kVA":
            errors.append(f"{where}: kva_at_max_kw measures kVA")
        for key in ("interval_min", "n"):
            if r.get(key) is not None and (not isinstance(r[key], int) or r[key] < 1):
                errors.append(f"{where}: {key} must be a whole number >= 1")
        if (r.get("method") == "sum") != (r.get("measure") == "kWh"):
            errors.append(f"{where}: an energy (kWh) quantity is summed, and only it")
        if not (r.get("codes") == "all" or isinstance(r.get("codes"), list)):
            errors.append(f"{where}: codes must be a list or all")
        if r.get("allowance_rollover") is not None and r.get("allowance_per_day") is None:
            errors.append(f"{where}: allowance_rollover needs allowance_per_day")
        unknown = set(r) - {"codes", "doc", "fin_year", "charge_type", "tou_period", "season", "measure",
                            "interval_min", "method", "n", "reset", "minimum_value", "threshold_value",
                            "allowance_per_day", "allowance_rollover", "locator", "quote", "note"}
        if unknown:
            errors.append(f"{where}: unknown keys {sorted(unknown)}")
        if check_quotes:
            _quote(errors, where, r, r.get("doc"))
    return errors


if __name__ == "__main__":
    bad = 0
    for p in sys.argv[1:]:
        data = load(p)
        errs = validate(data)
        n = {k: len(data.get(k) or []) for k in SECTIONS[1:]}
        print(f"{p}: {n}; {len(errs)} problems")
        for e in errs:
            print("  " + e)
        bad += len(errs)
    sys.exit(1 if bad else 0)
