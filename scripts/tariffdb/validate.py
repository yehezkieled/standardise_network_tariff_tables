"""Validate the tariff database in data/tariffdb/ (tables/*.csv). Every check prints PASS or FAIL with its findings;
the exit status is 1 when any check fails.

  .venv/bin/python scripts/tariffdb/validate.py              structure and rules (seconds)
  .venv/bin/python scripts/tariffdb/validate.py --sources    also re-read every value and quote from its source file
                                                             (minutes; with --committed-only, only the files committed
                                                             to the repository, as CI does)
  .venv/bin/python scripts/tariffdb/validate.py --coverage   also list the gaps to fill (never fails): per distributor,
                                                             year and status, the tariffs still without TOU windows or
                                                             eligibility

The checks, in order (docs/update-and-validate.md says what a failure means and what to do):
  load          the CSVs load into SQLite with every key, foreign key and CHECK constraint of schema.sqlite.sql
  periods       no two periods of one tariff code overlap; every rate, window and criterion lies inside its tariff's
                period; a source document's year contains the period it prices
  status        a tariff is final exactly when its document is the distributor's own published list (not one the AER
                hosts) or a state regulator's published schedule, and each rate carries its tariff's status and
                document
  units         every standard unit is one the docs list and fits its charge type (usage per kWh, demand per kW...)
  magnitude     no c/kWh rate outside critical peak exceeds 200 c/kWh unless its note contains 'confirmed
                high rate:'
  blocks        a stepped price numbers its blocks 1..n without gaps, with bounds that rise from block to block
  tou           windows of one tariff, charge group and published period name never overlap on the same day type
                and month
  aliases       no provisional tariff is one a final tariff of the same distributor and period prices under its own
                spelling or code (data/tariffdb/code_alias.csv), so no tariff is stored twice
  files         every held document is at its path with its recorded SHA-256 (--sources)
  values        every rate's published value is at its locator: the cell as Excel displays it, or the PDF page
                (--sources)
  quotes        every eligibility quote is at its locator, and every curated YAML file validates (--sources)
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
import load as loader  # noqa: E402

ROOT = bs.ROOT
UNIT_RE = re.compile(r"^c/(?:day|kWh|kVAh|(?:kW|kVA|k\?|lamp)/(?:day|month|year|season|\?)\??)$")
# the standard units each charge type may carry (a demand or capacity charge per kW or kVA; '?' = period not stated)
UNITS_BY_CHARGE = {"daily": r"c/(day|lamp/day)$", "metering": r"c/(day|kWh)$", "usage": r"c/(kWh|kVAh)$",
                   "demand": r"c/k(W|VA|\?)/", "capacity": r"c/k(W|VA|\?)/",
                   "export": r"c/(kWh|kVAh)$|c/k(W|VA)/", "other": r"c/"}
# above this a c/kWh price outside critical peak is a misread unless its note says 'confirmed high rate:' (the largest
# ordinary energy price in the dataset is under 150 c/kWh)
MAX_KWH_PRICE = 200
# rates stored with the unit their document prints although it does not fit the charge: rate_id -> evidence
KNOWN_MISPRINTS = {
    rid: "Evoenergy Statement of Tariff Classes and Tariffs 2023-24 p26 prints 'Net energy c/kVA/day' (123) and "
         "'c/KkA/day' (124); the same table prints 'Net energy cents/kWh' for the LV battery tariffs 108 and 109 (p25)"
    for rid in ("evoenergy:123:2023-07-01:usage:net-energy-consumption-charge:anytime",
                "evoenergy:124:2023-07-01:usage:net-energy-consumption-charge:anytime")}


def rows(db, sql):
    cur = db.execute(sql)
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
    for table in ("rate", "tou_window", "eligibility"):
        for r in rows(db, f"""SELECT x.distributor_id, x.tariff_code, x.effective_from, x.effective_to, t.effective_to
                               AS tariff_to FROM {table} x JOIN tariff t USING (distributor_id, tariff_code,
                               effective_from) WHERE x.effective_to > t.effective_to"""):
            bad.append(f"{table} {r['distributor_id']} {r['tariff_code']} {r['effective_from']}: ends "
                       f"{r['effective_to']}, after its tariff ({r['tariff_to']})")
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
    for r in rows(db, """SELECT r.rate_id, r.status, r.document_id, t.status AS t_status, t.document_id AS t_doc
                         FROM rate r JOIN tariff t USING (distributor_id, tariff_code, effective_from)
                         WHERE r.status != t.status OR r.document_id != t.document_id"""):
        bad.append(f"rate {r['rate_id']}: {r['status']} from {r['document_id']}, its tariff {r['t_status']} from "
                   f"{r['t_doc']}")
    return bad


def check_units(db):
    bad = []
    for r in rows(db, "SELECT rate_id, unit, charge_type FROM rate"):
        if not UNIT_RE.match(r["unit"]):
            bad.append(f"rate {r['rate_id']}: unit {r['unit']!r} is not a standard unit")
        elif not re.match(UNITS_BY_CHARGE[r["charge_type"]], r["unit"]) and r["rate_id"] not in KNOWN_MISPRINTS:
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


def check_tou(db):
    bad = []
    spans = defaultdict(list)
    for w in rows(db, "SELECT * FROM tou_window"):
        for m in (w["months"] or "season").split(","):
            spans[(w["distributor_id"], w["tariff_code"], w["effective_from"], w["applies_to"], w["period_label"],
                   w["day_type"], m, w["season"] if w["months"] is None else None)].append(
                (minutes(w["start_time"]), minutes(w["end_time"]), w["window_id"]))
    for key, ss in spans.items():
        ss.sort()
        for (a0, a1, aid), (b0, b1, bid) in zip(ss, ss[1:]):
            if b0 < a1:
                bad.append(f"windows {aid} and {bid} overlap (month {key[6]})")
    return sorted(set(bad))


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
                         JOIN source_document d USING (document_id) ORDER BY d.local_path, r.locator"""):
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
                         JOIN source_document d USING (document_id)"""):
        path = os.path.join(ROOT, r["local_path"])
        if not os.path.exists(path):
            if not committed_only:
                bad.append(f"eligibility {r['criterion_id']}: {r['local_path']} is missing")
            continue
        n += 1
        ok, why = locators.verify_quote(path, r["locator"], r["quote"])
        if not ok:
            bad.append(f"eligibility {r['criterion_id']}: {why}")
    if not committed_only:  # the YAML quotes documents that are not committed
        for name, data in curated.load_all().items():
            bad += [f"curated/{name}.yaml: {e}" for e in curated.validate(data)]
    return bad, n


# the checks main() runs after load, in order: (name, check); SOURCE_CHECKS only with --sources
CHECKS = (("periods", check_periods), ("status", check_status), ("units", check_units),
          ("magnitude", check_magnitude), ("blocks", check_blocks), ("tou", check_tou), ("aliases", check_aliases))
SOURCE_CHECKS = (("files", check_files), ("values", check_values), ("quotes", check_quotes))


def coverage(db):
    """One line per distributor-year and status: tariffs, those pricing a time-of-use period with no TOU window, and
    those with no eligibility criterion (the facts curated from the distributor's documents)."""
    out = []
    for r in rows(db, """SELECT t.distributor_id, d.pricing_year, t.status, count(*) AS n,
                         sum(EXISTS (SELECT 1 FROM rate r WHERE r.distributor_id = t.distributor_id
                               AND r.tariff_code = t.tariff_code AND r.effective_from = t.effective_from
                               AND r.tou_period IS NOT NULL AND r.tou_period != 'anytime')
                             AND NOT EXISTS (SELECT 1 FROM tou_window w WHERE w.distributor_id = t.distributor_id
                               AND w.tariff_code = t.tariff_code AND w.effective_from = t.effective_from)) AS no_tou,
                         sum(NOT EXISTS (SELECT 1 FROM eligibility e WHERE e.distributor_id = t.distributor_id
                               AND e.tariff_code = t.tariff_code AND e.effective_from = t.effective_from)) AS no_elig
                         FROM tariff t JOIN source_document d USING (document_id)
                         GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""):
        out.append(f"{r['distributor_id']:13} {r['pricing_year']:7} {r['status']:11} {r['n']:4} tariffs, {r['no_tou']:3} "
                   f"pricing a TOU period without windows, {r['no_elig']:3} without eligibility")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sources", action="store_true", help="also re-read every value and quote from its source file")
    ap.add_argument("--committed-only", action="store_true",
                    help="with --sources: re-read only the files committed to the repository (as CI does)")
    ap.add_argument("--coverage", action="store_true", help="also list the gaps to fill (informational)")
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
