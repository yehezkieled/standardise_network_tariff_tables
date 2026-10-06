"""Hand-curated facts that no price table carries: TOU windows, demand measurement rules, eligibility and assignment
rules, tariff relations and status flags. One YAML file per distributor under data/tariffdb/curated/.

Every fact quotes its source verbatim at a locator; `validate()` re-reads each quote from the source file, checks
enums, times, months and tariff codes, and checks that schedules marked covers_full_day tile 24 hours per day type
without overlap. Run:  .venv/bin/python scripts/tariffdb/curated.py data/tariffdb/curated/<distributor>.yaml

File format (keys in [] are optional):

distributor: <distributor_id>
tou_schedules:
  - id: <slug, unique in the file, start with the distributor id>
    doc: <repo-relative source path, as in sources/inventory.csv>
    fin_year: 2025-26
    name: <what the windows are for>
    time_basis: local_time | standard_time | not_stated
    public_holidays: as_weekend | as_weekday | as_non_business_day | unchanged | not_stated
    covers_full_day: true | false        # true only when the windows partition each listed day type over 24h
    locator: pdf:p7 | xlsx:<sheet>!<cell>
    quote: <verbatim wording at the locator that states the windows>
    [note: ...]
    windows:
      - {period: <spec.TOU_PERIODS>, label: <as published>, days: <spec.DAY_TYPES>, start: "HH:MM", end: "HH:MM",
         [months: "all" | "11,12,1,2,3" | not_stated], [season: <as published>], [locator: ..., quote: ...]}
    (omit months only when the source names no season; not_stated = the source names a season but not its months)
    tariffs:
      - {codes: [<code>, ...], applies_to: energy | demand | export | controlled_load | all, [locator, quote]}
demand_rules:
  - id: <slug>
    doc, fin_year, measure: kW | kVA, [interval_minutes: 30], aggregation: <spec.AGGREGATIONS>,
    [aggregation_count: 4  (n of average_of_highest_days)],
    [window_schedule: <tou schedule id>, window_period: <period>], [months: "12,1,2,3"],
    [minimum_chargeable: 250, minimum_unit: kVA], locator, quote, [note]
    tariffs: [{codes: [...], [time_band: peak], [season: summer]}]
eligibility:
  - {codes: [...], doc, fin_year, rule_type: <spec.RULE_TYPES>, [operator: <spec.OPERATORS>], [value_num: 40],
     [value_unit: MWh/yr], [value_text: <spec.RULE_VALUES[rule_type] when listed>], [target_code: <code>], locator,
     quote, [note]}
relations:
  - {from: <code>, type: <spec.RELATION_TYPES>, to: <code>, fin_year, doc, locator, quote, [note]}
flags:
  - {codes: [...], doc, fin_year, flag: <spec.LISTING_FLAGS>, locator, quote, [column_locator, column_quote]}
steps:
  - {codes: [...], doc, fin_year, step_group: <quantity as named>, component_label, step_index: 1, lower_bound: 0,
     [upper_bound: 60], lower_inclusive: true, upper_inclusive: true, quantity_unit: kWh, reset_period: day, locator, quote}
    (column_* quotes the column header when the quote is a bare table row, e.g. a 'Yes' under 'Closed to New Entrants')
"""
import csv
import glob
import os
import re
import sys

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
                 "weekend_and_public_holiday": DAYS[5:], "saturday": ("sat",), "sunday": ("sun",), "all_days": DAYS,
                 "public_holiday": ("public_holiday",)}


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


def known_codes(distributor_name):
    """Tariff codes the parsers saw for a distributor (any year, any side), split on joint-label separators."""
    codes = set()
    paths = [os.path.join(ROOT, "out", "aer_long.csv"), os.path.join(ROOT, "out", "aer_versions_long.csv")]
    paths += glob.glob(os.path.join(ROOT, "out", "dnsp", "*.csv"))
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, newline="") as f:
            for r in csv.DictReader(f):
                if r["distributor"] == distributor_name:
                    c = r["tariff_code"].strip()
                    codes.add(c)
                    codes.update(x.strip().rstrip("*") for x in re.split(r"[/,]", c) if x.strip())
    return codes


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


def validate(data, check_quotes=True, codes=None):
    errors = []
    did = data.get("distributor")
    if did is None:
        return ["missing distributor"]
    sched_ids = set()
    inv = {}
    with open(os.path.join(ROOT, "sources", "inventory.csv"), newline="") as f:
        for r in csv.DictReader(f):
            if r["local_path"]:
                inv[r["local_path"]] = r
    from build_support import EXTRA_DOCUMENTS  # documents added to the inventory by the tariff database
    for d in EXTRA_DOCUMENTS:
        if d.get("local_path"):
            inv.setdefault(d["local_path"], d)

    def check_doc(where, obj):
        if obj.get("doc") not in inv:
            errors.append(f"{where}: doc {obj.get('doc')!r} is not a retrieved document in sources/inventory.csv")
        if obj.get("fin_year") not in spec.FIN_YEARS:
            errors.append(f"{where}: fin_year {obj.get('fin_year')!r}")

    def check_codes(where, cs):
        if codes is None:
            return
        for c in cs:
            if re.sub(r"[^A-Z0-9]", "", str(c).upper()) not in {re.sub(r"[^A-Z0-9]", "", str(x).upper()) for x in codes}:
                errors.append(f"{where}: tariff code {c!r} not seen in any parsed document of this distributor")

    for i, s in enumerate(data.get("tou_schedules") or []):
        where = f"tou_schedules[{i}] {s.get('id')}"
        _req(errors, where, s, ["id", "doc", "fin_year", "name", "time_basis", "public_holidays", "locator", "quote", "windows"])
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
            if TIME_RE.match(str(w.get("start", ""))) and TIME_RE.match(str(w.get("end", ""))) and minutes(w["start"]) >= minutes(w["end"]):
                errors.append(f"{ww}: start must be before end (split windows that cross midnight)")
            try:
                if months_of(w.get("months")) is None and not w.get("season"):
                    errors.append(f"{ww}: months not_stated needs the season name as published")
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
            check_codes(tw, t.get("codes") or [])
            if check_quotes and t.get("quote"):
                _quote(errors, tw, t, s.get("doc"))
    for i, r in enumerate(data.get("demand_rules") or []):
        where = f"demand_rules[{i}] {r.get('id')}"
        _req(errors, where, r, ["id", "doc", "fin_year", "measure", "aggregation", "locator", "quote", "tariffs"])
        check_doc(where, r)
        _enum(errors, where, r.get("measure"), ["kW", "kVA"], "measure")
        _enum(errors, where, r.get("aggregation"), spec.AGGREGATIONS, "aggregation")
        if (r.get("aggregation") == "average_of_highest_days") != (r.get("aggregation_count") is not None):
            errors.append(f"{where}: aggregation_count goes with (and only with) average_of_highest_days")
        if r.get("window_schedule") and r["window_schedule"] not in sched_ids:
            errors.append(f"{where}: window_schedule {r['window_schedule']!r} not defined above")
        _enum(errors, where, r.get("window_period"), spec.TOU_PERIODS, "window_period")
        if r.get("months") == "not_stated":
            errors.append(f"{where}: omit months on a demand rule when they are not stated (say so in the note)")
        elif r.get("months"):
            try:
                months_of(r["months"])
            except ValueError as e:
                errors.append(f"{where}: {e}")
        if check_quotes:
            _quote(errors, where, r, r.get("doc"))
        for t in r.get("tariffs") or []:
            check_codes(where, t.get("codes") or [])
    for i, r in enumerate(data.get("eligibility") or []):
        where = f"eligibility[{i}] {r.get('codes')} {r.get('rule_type')}"
        _req(errors, where, r, ["codes", "doc", "fin_year", "rule_type", "locator", "quote"])
        check_doc(where, r)
        _enum(errors, where, r.get("rule_type"), spec.RULE_TYPES, "rule_type")
        _enum(errors, where, r.get("operator"), spec.OPERATORS, "operator")
        allowed = spec.RULE_VALUES.get(r.get("rule_type"))
        if allowed and r.get("value_text") is not None and r["value_text"] not in allowed:
            errors.append(f"{where}: value_text={r['value_text']!r} not in {allowed}")
        if r.get("value_num") is None and not r.get("value_text") and not r.get("target_code"):
            errors.append(f"{where}: needs value_num, value_text or target_code")
        if r.get("value_num") is not None and not r.get("operator"):
            errors.append(f"{where}: value_num needs an operator")
        check_codes(where, (r.get("codes") or []) + ([r["target_code"]] if r.get("target_code") else []))
        if check_quotes:
            _quote(errors, where, r, r.get("doc"))
    for i, r in enumerate(data.get("relations") or []):
        where = f"relations[{i}] {r.get('from')} {r.get('type')} {r.get('to')}"
        _req(errors, where, r, ["from", "type", "to", "doc", "fin_year", "locator", "quote"])
        check_doc(where, r)
        _enum(errors, where, r.get("type"), spec.RELATION_TYPES, "type")
        check_codes(where, [r.get("from"), r.get("to")])
        if check_quotes:
            _quote(errors, where, r, r.get("doc"))
    for i, r in enumerate(data.get("flags") or []):
        where = f"flags[{i}] {r.get('codes')} {r.get('flag')}"
        _req(errors, where, r, ["codes", "doc", "fin_year", "flag", "locator", "quote"])
        check_doc(where, r)
        _enum(errors, where, r.get("flag"), spec.LISTING_FLAGS, "flag")
        check_codes(where, r.get("codes") or [])
        if bool(r.get("column_locator")) != bool(r.get("column_quote")):
            errors.append(f"{where}: column_locator and column_quote go together")
        if check_quotes:
            _quote(errors, where, r, r.get("doc"))
            if r.get("column_locator"):
                _quote(errors, where + " column", {"locator": r["column_locator"], "quote": r["column_quote"]},
                       r.get("doc"))
    step_cols = {c["name"]: c for t in spec.TABLES if t["name"] == "charge_step" for c in t["columns"]}
    for i, r in enumerate(data.get("steps") or []):
        where = f"steps[{i}] {r.get('codes')} {r.get('step_group')} #{r.get('step_index')}"
        _req(errors, where, r, ["codes", "doc", "fin_year", "step_group", "component_label", "step_index",
                                "lower_inclusive", "upper_inclusive", "quantity_unit", "reset_period", "locator",
                                "quote"])
        check_doc(where, r)
        check_codes(where, r.get("codes") or [])
        for k in ("quantity_unit", "reset_period"):
            _enum(errors, where, r.get(k), step_cols[k]["enum"], k)
        if not isinstance(r.get("step_index"), int) or r["step_index"] < 1:
            errors.append(f"{where}: step_index must be an integer >= 1")
        if r.get("upper_bound") is not None and r.get("lower_bound") is not None and \
                not r["lower_bound"] < r["upper_bound"]:
            errors.append(f"{where}: lower_bound must be below upper_bound")
        if check_quotes:
            _quote(errors, where, r, r.get("doc"))
    return errors


if __name__ == "__main__":
    from build_support import DISTRIBUTORS
    names = {d["distributor_id"]: d["name"] for d in DISTRIBUTORS}
    bad = 0
    for p in sys.argv[1:]:
        data = load(p)
        errs = validate(data)
        n = {k: len(data.get(k) or []) for k in ("tou_schedules", "demand_rules", "eligibility", "relations", "flags",
                                                  "steps")}
        print(f"{p}: {n}; {len(errs)} problems")
        for e in errs:
            print("  " + e)
        bad += len(errs)
    sys.exit(1 if bad else 0)
