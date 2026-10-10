"""Validate the tariff database in data/tariffdb/ (tables/*.csv). Every check prints PASS or FAIL with its findings;
the exit status is 1 when any check fails.

  .venv/bin/python scripts/tariffdb/validate.py              structure and rules (seconds)
  .venv/bin/python scripts/tariffdb/validate.py --sources    also re-read every value and quote from its source file
                                                             (minutes; with --committed-only, only the files committed
                                                             to the repository, as CI does)
  .venv/bin/python scripts/tariffdb/validate.py --coverage   also list the gaps to fill (never fails): per distributor,
                                                             year and status, the tariffs still without TOU windows,
                                                             eligibility or demand measurement rules. Eligibility
                                                             starts 2023-07-01, so every tariff of an earlier year
                                                             is listed without it

The checks, in order (docs/update-and-validate.md says what a failure means and what to do):
  load          the CSVs load into SQLite with every key, foreign key and CHECK constraint of schema.sqlite.sql
  periods       no two periods of one tariff code overlap; a source document's year contains the period it prices
  status        a tariff is final exactly when its document is the distributor's own published list (not one the AER
                hosts) or a state regulator's published schedule
  units         every rate's unit (a row of the unit table, spec.UNITS) prices a quantity its charge type is charged
                per (usage per kWh, demand per kW or kVA ...)
  magnitude     no c/kWh rate outside critical peak exceeds 200 c/kWh unless its note contains 'confirmed
                high rate:'
  blocks        a stepped price numbers its blocks 1..n without gaps, with bounds that rise from block to block
  tou           a window's season is in its own window set; windows a tariff uses for one charge group and published
                period name never overlap on the same day type and month
  joins         in a tariff-period with TOU windows, every rate priced in a period or season finds its windows, every
                window but a demand window is priced by a rate, and no demand or export rate lacks a period its
                windows name (scripts/tariffdb/joins.py)
  rules         every demand measurement rule (charge_rule) measures a rate of its tariff-period, in the quantity the
                rate is priced in
  aliases       no provisional tariff is one a final tariff of the same distributor and period prices under its own
                spelling or code (data/tariffdb/code_alias.csv), so no tariff is stored twice
  views         each view (tariff_flat, tou_flat, unit_spelling) has the columns spec.py gives it; tariff_flat and
                tou_flat return every tariff-period, and tariff_flat every rate
  files         every held document is at its path with its recorded SHA-256 (--sources)
  values        every rate's published value is at its locator: the cell as Excel displays it, or the PDF page
                (--sources)
  quotes        every eligibility, assignment, tariff link and rate condition quote, and every window set's time
                basis and public-holiday statement, is at its locator, and every
                curated YAML file validates (which re-reads the TOU window quotes)
                (--sources)
"""
import argparse
import hashlib
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import aliases  # noqa: E402
import build_support as bs  # noqa: E402
import joins  # noqa: E402
import load as loader  # noqa: E402
import spec  # noqa: E402

ROOT = bs.ROOT
# above this a c/kWh price outside critical peak is a misread unless its note says 'confirmed high rate:' (the largest
# ordinary energy price in the dataset is under 150 c/kWh)
MAX_KWH_PRICE = 200
# rates stored with the unit their document prints although it does not fit the charge: rate_id -> evidence
KNOWN_MISPRINTS = {
    rid: "Evoenergy Statement of Tariff Classes and Tariffs 2023-24 p26 prints 'Net energy c/kVA/day' (123) and "
         "'c/KkA/day' (124); the same table prints 'Net energy cents/kWh' for the LV battery tariffs 108 and 109 (p25)"
    for rid in ("evoenergy:123:2023-07-01:usage:net-energy-consumption-charge:anytime",
                "evoenergy:124:2023-07-01:usage:net-energy-consumption-charge:anytime")}


def rows(db, sql, params=()):
    cur = db.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def check_periods(db):
    bad = []
    for r in rows(db, """SELECT a.distributor_id, a.tariff_code, a.effective_from, b.effective_from AS other
                         FROM tariff a JOIN tariff b ON a.distributor_id = b.distributor_id
                          AND a.tariff_code = b.tariff_code AND a.effective_from < b.effective_from
                          AND b.effective_from <= a.effective_to"""):
        bad.append(f"tariff {r['distributor_id']} {r['tariff_code']}: period from {r['effective_from']} overlaps the "
                   f"one from {r['other']}")
    for r in rows(db, """SELECT t.distributor_id, t.tariff_code, t.effective_from, t.effective_to, d.document_id,
                         d.pricing_year FROM tariff t JOIN source_document d USING (document_id)"""):
        start, end = bs.YEAR_DATES[r["pricing_year"]]
        if not (start <= r["effective_from"] and r["effective_to"] <= end):
            bad.append(f"tariff {r['distributor_id']} {r['tariff_code']} {r['effective_from']}..{r['effective_to']}: "
                       f"outside the {r['pricing_year']} year of {r['document_id']}")
    return bad


def check_status(db):
    bad = []
    for r in rows(db, """SELECT t.distributor_id, t.tariff_code, t.effective_from, t.status, d.document_id,
                         d.publisher, d.hosted_by_aer, d.price_status FROM tariff t JOIN source_document d
                         USING (document_id)"""):
        final = (r["publisher"] == "distributor" and not r["hosted_by_aer"] and r["price_status"] == "published") or (
            r["publisher"] == "regulator" and r["price_status"] in ("published", "approved"))
        if (r["status"] == "final") != final:
            bad.append(f"tariff {r['distributor_id']} {r['tariff_code']} {r['effective_from']}: status {r['status']} "
                       f"but {r['document_id']} is {'' if final else 'not '}the distributor's own published list "
                       f"or a regulator's published schedule")
    return bad


def check_units(db):
    """Every rate's unit prices a quantity its charge type is charged per (spec.QUANTITIES_BY_CHARGE); the unit
    table itself is the one spec.py generates (the foreign key keeps every rate inside it)."""
    import spec
    bad = []
    key = lambda r: r["unit"]  # noqa: E731
    if sorted(rows(db, "SELECT unit, quantity, billing_period, unit_std, multiplier, calendar_factor, definition "
                       "FROM unit"), key=key) != sorted(spec.unit_rows(), key=key):
        bad.append("unit table differs from spec.UNITS: rebuild")
    for r in rows(db, "SELECT r.rate_id, r.unit, r.charge_type, u.quantity FROM rate r JOIN unit u USING (unit)"):
        if r["quantity"] not in spec.QUANTITIES_BY_CHARGE[r["charge_type"]] and r["rate_id"] not in KNOWN_MISPRINTS:
            bad.append(f"rate {r['rate_id']}: a {r['charge_type']} charge in {r['unit']}")
    return bad


def check_magnitude(db):
    return [f"rate {r['rate_id']}: {r['value']} c/kWh" for r in rows(
        db, f"""SELECT rate_id, value FROM rate WHERE unit = 'c/kWh' AND coalesce(tou_period, '') != 'critical_peak'
                AND abs(value) > {MAX_KWH_PRICE} AND coalesce(note, '') NOT LIKE '%confirmed high rate:%'""")]


def check_blocks(db):
    bad = []
    ladders = defaultdict(list)
    for r in rows(db, "SELECT * FROM rate WHERE block IS NOT NULL"):
        ladders[(r["distributor_id"], r["tariff_code"], r["effective_from"], r["charge_type"], r["tou_period"],
                 r["season"], r["region"])].append(r)
    for key, rs in sorted(ladders.items(), key=lambda kv: str(kv[0])):
        blocks = sorted({r["block"] for r in rs})
        if blocks != list(range(1, len(blocks) + 1)):
            bad.append(f"{' '.join(str(k) for k in key[:4] if k)}: blocks {blocks}, expected 1..{len(blocks)}")
        lower = [r["block_from"] for r in sorted(rs, key=lambda r: r["block"]) if r["block_from"] is not None]
        if lower != sorted(lower):
            bad.append(f"{' '.join(str(k) for k in key[:4] if k)}: block lower bounds {lower} do not rise")
    return bad


def minutes(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def tariff_windows(db):
    """{(distributor_id, tariff_code, effective_from): [window]}: the windows each tariff period uses (joins.py)."""
    return joins.tariff_windows(*(rows(db, f"SELECT * FROM {t}") for t in (
        "window_set", "season", "season_part", "time_window", "tariff_window_set")))


def check_tou(db):
    """A window's season belongs to the window's own set; windows a tariff uses for one charge group and published
    period name never overlap on a day type and month."""
    bad = [f"window {r['window_id']}: its season {r['season_id']} belongs to another window set" for r in rows(
        db, "SELECT w.window_id, w.season_id FROM time_window w JOIN season s USING (season_id) "
            "WHERE s.window_set_id != w.window_set_id")]
    spans = defaultdict(list)
    for key, ws in tariff_windows(db).items():
        for w in ws:
            for m in (w["months"] or w["dst"] or "season").split(","):
                spans[(*key, w["applies_to"], w["period_label"], w["day_type"], m,
                       w["season_label"] if w["months"] is None else None)].append(
                    (minutes(w["start_time"]), minutes(w["end_time"]), w["window_id"]))
    for key, ss in spans.items():
        ss.sort()
        for (a0, a1, aid), (b0, b1, bid) in zip(ss, ss[1:]):
            if b0 < a1:
                bad.append(f"{' '.join(key[:3])}: windows {aid} and {bid} overlap (month {key[6]})")
    return sorted(set(bad))


def check_joins(db):
    """In a tariff-period with TOU windows, every rate priced in a period or season finds its windows, every window
    except a demand window of a period its charge group prices is priced by a rate of that group (in the window's
    season), and no demand, capacity or export rate without a period sits beside windows naming periods (joins.py says
    how they join). A tariff-period without windows for a charge group, a season whose months no held document states
    (its window says months not_stated) and an event period with no fixed hours are gaps the bill calculator reports
    (billcalc.py sweep), not failures."""
    windows, rates = tariff_windows(db), defaultdict(list)
    for r in rows(db, "SELECT * FROM rate"):
        rates[(r["distributor_id"], r["tariff_code"], r["effective_from"])].append(r)
    bad = []
    for key, ws in sorted(windows.items()):
        for r in (r for r in rates.get(key, []) if joins.group_of(r)):
            if r["tou_period"] not in joins.ALL_TIMES + joins.EVENT_PERIODS + joins.BANDS \
                    and joins.group_windows(joins.group_of(r), ws) and not joins.rate_windows(r, ws):
                bad.append(f"rate {r['rate_id']}: no {joins.group_of(r)} window for {r['tou_period']}"
                           + (f" in season {r['season']}" if r["season"] else ""))
            if r["season"] and not joins.season_named(r["season"], ws):
                bad.append(f"rate {r['rate_id']}: no window belongs to season {r['season']}")
            if joins.ambiguous(r, ws):
                bad.append(f"rate {r['rate_id']}: no period, but the tariff's {joins.group_of(r)} windows name "
                           f"{', '.join(joins.ambiguous(r, ws))}")
        grouped = [r for r in rates.get(key, []) if joins.group_of(r)]
        priced = {w["window_id"] for r in grouped for w in joins.priced_windows(r, ws)}
        bad += [f"window {w['window_id']}: no rate prices {w['applies_to']} {w['tou_period']}"
                + (f" in season {w['season']}" if w["season"] else "") for w in ws
                if w["window_id"] not in priced and w["applies_to"] != "demand" and w["tou_period"] != joins.SUPPLY
                and any(r["tou_period"] == w["tou_period"] and w in joins.group_windows(joins.group_of(r), ws)
                        for r in grouped)]
    return bad


def check_rules(db):
    """Every charge_rule measures at least one rate of its tariff-period (same charge type, and the rule's period and
    season when it names them), and its measure is the quantity those rates are priced in (kW, kVA or kWh)."""
    bad = []
    for c in rows(db, "SELECT * FROM charge_rule"):
        units = [r["unit"] for r in rows(db, """SELECT unit FROM rate WHERE distributor_id = ? AND tariff_code = ?
                     AND effective_from = ? AND charge_type = ? AND (? IS NULL OR tou_period = ?)
                     AND (? IS NULL OR season = ?)""", (c["distributor_id"], c["tariff_code"], c["effective_from"],
                                                       c["charge_type"], c["tou_period"], c["tou_period"],
                                                       c["season"], c["season"]))]
        if not units:
            bad.append(f"charge_rule {c['rule_id']}: no {c['charge_type']} rate of its tariff-period to measure")
        elif not any(re.search(rf"/{c['measure']}(/|$)", u) for u in units):
            bad.append(f"charge_rule {c['rule_id']}: measures {c['measure']} but the rates are in {sorted(set(units))}")
    return bad


def check_aliases(db, rules=None):
    rules = aliases.load() if rules is None else rules
    final = defaultdict(list)
    for t in rows(db, "SELECT distributor_id, tariff_code, effective_from, effective_to FROM tariff "
                      "WHERE status = 'final'"):
        final[t["distributor_id"]].append(t)
    bad = []
    for p in rows(db, "SELECT distributor_id, tariff_code, effective_from, effective_to FROM tariff "
                      "WHERE status = 'provisional'"):
        overlap = [f["tariff_code"] for f in final[p["distributor_id"]]
                   if f["effective_from"] <= p["effective_to"] and p["effective_from"] <= f["effective_to"]]
        same = [c for c in overlap if aliases.norm(c) == aliases.norm(p["tariff_code"])]
        same += aliases.targets(rules, p["distributor_id"], p["tariff_code"], p["effective_from"], overlap)
        if same:
            bad.append(f"tariff {p['distributor_id']} {p['tariff_code']} {p['effective_from']}: provisional, but the "
                       f"final list prices it as {', '.join(sorted(set(same)))}")
    return bad


def check_files(db, committed_only):
    bad, n = [], 0
    for d in rows(db, "SELECT document_id, local_path, sha256 FROM source_document WHERE local_path IS NOT NULL"):
        path = os.path.join(ROOT, d["local_path"])
        if not os.path.exists(path):
            if not committed_only:
                bad.append(f"{d['document_id']}: {d['local_path']} is missing (run ./run.sh to fetch it)")
            continue
        n += 1
        with open(path, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() != d["sha256"]:
                bad.append(f"{d['document_id']}: {d['local_path']} does not match its recorded SHA-256")
    return bad, n


def check_values(db, committed_only):
    import locators
    bad, n = [], 0
    for r in rows(db, """SELECT r.rate_id, r.locator, r.value_published, d.local_path FROM rate r
                         JOIN tariff t USING (distributor_id, tariff_code, effective_from)
                         JOIN source_document d ON d.document_id = t.document_id ORDER BY d.local_path, r.locator"""):
        path = os.path.join(ROOT, r["local_path"])
        if not os.path.exists(path):
            if not committed_only:
                bad.append(f"rate {r['rate_id']}: {r['local_path']} is missing")
            continue
        n += 1
        loc = locators.parse(r["locator"])
        if loc["kind"] == "xlsx":
            _, shown = locators.read_cell_excel(path, loc["sheet"], loc["cell"])
            if str(shown) != r["value_published"]:
                bad.append(f"rate {r['rate_id']}: {r['locator']} shows {shown!r}, not {r['value_published']!r}")
        else:
            ok, why = locators.verify(path, r["locator"], r["value_published"])
            if not ok:
                bad.append(f"rate {r['rate_id']}: {why}")
    return bad, n


def check_quotes(db, committed_only):
    import curated
    import locators
    bad, n = [], 0
    for r in rows(db, """SELECT e.criterion_id, e.locator, e.quote, d.local_path FROM eligibility e
                         JOIN source_document d USING (document_id)
                         UNION ALL SELECT a.distributor_id || ':' || a.tariff_code || ':' || a.effective_from
                         || ':assignment:' || a.assignment_no, a.locator, a.quote, d.local_path
                         FROM tariff_assignment a JOIN source_document d USING (document_id)
                         UNION ALL SELECT l.distributor_id || ':' || l.tariff_code || ':' || l.effective_from
                         || ':link:' || l.link_no, l.locator, l.quote, d.local_path
                         FROM tariff_link l JOIN source_document d USING (document_id) WHERE l.quote IS NOT NULL
                         UNION ALL SELECT c.rate_id || ':' || c.condition_kind || ':' || c.value, c.locator, c.quote,
                         d.local_path FROM rate_condition c JOIN source_document d USING (document_id)
                         UNION ALL SELECT w.window_set_id || ':time_basis', w.time_basis_locator, w.time_basis_quote,
                         d.local_path FROM window_set w JOIN source_document d
                         ON d.document_id = w.time_basis_document_id
                         UNION ALL SELECT w.window_set_id || ':public_holidays', w.public_holidays_locator,
                         w.public_holidays_quote, d.local_path FROM window_set w JOIN source_document d
                         ON d.document_id = w.public_holidays_document_id"""):
        path = os.path.join(ROOT, r["local_path"])
        if not os.path.exists(path):
            if not committed_only:
                bad.append(f"{r['criterion_id']}: {r['local_path']} is missing")
            continue
        n += 1
        ok, why = locators.verify_quote(path, r["locator"], r["quote"])
        if not ok:
            bad.append(f"{r['criterion_id']}: {why}")
    if not committed_only:  # every source is committed now; the skip only guards checkouts that lack them
        for name, data in curated.load_all().items():
            bad += [f"curated/{name}.yaml: {e}" for e in curated.validate(data)]
    return bad, n


def check_views(db):
    """Each view has the columns spec.py gives it, tariff_flat and tou_flat return every tariff-period, and tariff_flat
    every rate."""
    bad = []
    for v in spec.VIEWS:
        got = [c[0] for c in db.execute(f"SELECT * FROM {v['name']} LIMIT 0").description]
        if got != [c["name"] for c in v["columns"]]:
            bad.append(f"view {v['name']}: columns {got} differ from spec.py")
    for view in ("tariff_flat", "tou_flat"):
        missing = db.execute(f"""SELECT count(*) FROM tariff t WHERE NOT EXISTS (SELECT 1 FROM {view} v
            WHERE v.distributor_id = t.distributor_id AND v.tariff_code = t.tariff_code
            AND v.effective_from = t.effective_from)""").fetchone()[0]
        if missing:
            bad.append(f"view {view}: {missing} tariff-periods missing")
    missing = db.execute("""SELECT count(*) FROM rate r WHERE NOT EXISTS (SELECT 1 FROM tariff_flat v
        WHERE v.rate_id = r.rate_id)""").fetchone()[0]
    if missing:
        bad.append(f"view tariff_flat: {missing} rates missing")
    return bad


# the checks main() runs after load, in order: (name, check); SOURCE_CHECKS only with --sources
CHECKS = (("periods", check_periods), ("status", check_status), ("units", check_units),
          ("magnitude", check_magnitude), ("blocks", check_blocks), ("tou", check_tou), ("joins", check_joins),
          ("rules", check_rules), ("aliases", check_aliases), ("views", check_views))
SOURCE_CHECKS = (("files", check_files), ("values", check_values), ("quotes", check_quotes))


def coverage(db):
    """One line per distributor-year and status: tariffs, those pricing a time-of-use period with no TOU window, those
    with no eligibility criterion, and those with demand or capacity rates but no charge_rule (the facts curated from
    the distributor's documents). billcalc.py sweep counts what each gap blocks."""
    out = []
    for r in rows(db, """SELECT t.distributor_id, d.pricing_year, t.status, count(*) AS n,
                         sum(EXISTS (SELECT 1 FROM rate r WHERE r.distributor_id = t.distributor_id
                               AND r.tariff_code = t.tariff_code AND r.effective_from = t.effective_from
                               AND r.tou_period IS NOT NULL AND r.tou_period != 'anytime')
                             AND NOT EXISTS (SELECT 1 FROM tariff_window_set w
                               WHERE w.distributor_id = t.distributor_id AND w.tariff_code = t.tariff_code
                               AND w.effective_from = t.effective_from)) AS no_tou,
                         sum(NOT EXISTS (SELECT 1 FROM eligibility e WHERE e.distributor_id = t.distributor_id
                               AND e.tariff_code = t.tariff_code AND e.effective_from = t.effective_from)) AS no_elig,
                         sum(EXISTS (SELECT 1 FROM rate r WHERE r.distributor_id = t.distributor_id
                               AND r.tariff_code = t.tariff_code AND r.effective_from = t.effective_from
                               AND r.charge_type IN ('demand', 'capacity') AND NOT EXISTS (SELECT 1 FROM charge_rule c
                                 WHERE c.distributor_id = r.distributor_id AND c.tariff_code = r.tariff_code
                                 AND c.effective_from = r.effective_from AND c.charge_type = r.charge_type))) AS no_rule
                         FROM tariff t JOIN source_document d USING (document_id)
                         GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""):
        out.append(f"{r['distributor_id']:13} {r['pricing_year']:7} {r['status']:11} {r['n']:4} tariffs, {r['no_tou']:3} "
                   f"pricing a TOU period without windows, {r['no_elig']:3} without eligibility, {r['no_rule']:3} "
                   f"with demand or capacity rates but no measurement rule")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sources", action="store_true", help="also re-read every value and quote from its source file")
    ap.add_argument("--committed-only", action="store_true",
                    help="with --sources: re-read only the files committed to the repository (as CI does)")
    ap.add_argument("--coverage", action="store_true",
                    help="also list the gaps to fill (informational; eligibility starts 2023-07-01, so every "
                    "tariff of an earlier year is listed without it)")
    ap.add_argument("--data", default=loader.DEFAULT_DATA, help="database directory (default data/tariffdb)")
    a = ap.parse_args(argv)
    failed = False

    def report(name, bad, n=None):
        nonlocal failed
        failed |= bool(bad)
        print(f"{'FAIL' if bad else 'PASS'} {name}" + (f" ({n} checked)" if n is not None else "")
              + (f": {len(bad)} problems" if bad else ""))
        for b in bad[:50]:
            print("  " + b)
        if len(bad) > 50:
            print(f"  ... {len(bad) - 50} more")

    try:
        db = loader.load(a.data)
    except Exception as e:  # noqa: BLE001 - every load failure is reported the same way
        report("load", [str(e)])
        return 1
    report("load", [])
    for name, check in CHECKS:
        report(name, check(db))
    if a.sources:
        for name, check in SOURCE_CHECKS:
            bad, n = check(db, a.committed_only)
            report(name, bad, n)
    if a.coverage:
        print("coverage (informational):")
        for line in coverage(db):
            print("  " + line)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
