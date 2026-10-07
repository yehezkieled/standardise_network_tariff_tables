"""Build the tariff database under data/tariffdb/: one CSV per table (tables/*.csv), the SQLite DDL
(schema.sqlite.sql) and the machine-readable table spec (schema.json), all from scripts/tariffdb/spec.py.

Inputs:
  - the parser outputs out/aer_long.csv (latest held AER version per year), out/dnsp/*.csv and out/dnsp_metering/*.csv
    (run ./run.sh first; every row carries the cell or page it was read from);
  - data/tariffdb/curated/*.yaml: TOU windows, eligibility criteria and block bounds quoted from distributor documents;
  - the document registry, scripts/tariffdb/build_support.py over sources/inventory.csv.

Which rates a tariff code gets, per distributor and financial year:
  final        the distributor's own published price list prices the code (one such document per distributor-year;
               build_support.FINAL_DOCUMENT names it where a distributor publishes more than one);
  provisional  otherwise, the AER's latest held report for that year (v1 first, as it is published first), else a
               distributor document the AER hosts, else the distributor's own proposal.
Each price is the total network price (NUoS) excluding GST. A component printed both with and without metering keeps
the without-metering copy, and the distributor's separately priced metering charge becomes its own `metering` rate.

Output is deterministic (rows sorted by primary key, keys derived from content), so a rebuild from the same inputs is
byte-identical.

  .venv/bin/python scripts/tariffdb/build.py             rebuild data/tariffdb/
  .venv/bin/python scripts/tariffdb/build.py --out DIR   write the same files under DIR instead
  .venv/bin/python scripts/tariffdb/build.py --verbose   also list each curated fact that names no tariff with rates
"""
import argparse
import csv
import datetime
import glob
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import build_support as bs  # noqa: E402
import curated  # noqa: E402
import spec  # noqa: E402
from units import to_std  # noqa: E402

ROOT = bs.ROOT
OUT_DIR = os.path.join(ROOT, "data", "tariffdb")
# the parsers' marker row for a tariff the AER lists with every price zero (premium feed-in, trial placeholders)
NO_PRICES = "(no non-zero components)"
REPEATED_PRINTING = "repeated printing"
EXCL_METERING = re.compile(r"exclud\w*\s+metering|excl\.?\s+metering|without\s+metering", re.I)
BASIS_ORDER = ("NUoS", "unknown")  # total network price; 'unknown' = the document does not say (AusNet 2024-25, PWC)
CHARGE_TYPES = {"fixed": "daily", "energy": "usage", "demand": "demand", "capacity": "capacity", "export": "export",
                "other": "other"}
# parser time_band -> (rate.tou_period, rate.block)
BANDS = {"": (None, None), "anytime": ("anytime", None), "peak": ("peak", None), "offpeak": ("off_peak", None),
         "shoulder": ("shoulder", None), "super_offpeak": ("super_off_peak", None),
         "critical_peak": ("critical_peak", None), "solar_soak": ("solar_soak", None),
         "block1": (None, 1), "block2": (None, 2), "block3": (None, 3),
         "peak_block1": ("peak", 1), "peak_block2": ("peak", 2),
         "capacity_minimum": ("capacity_minimum", None), "capacity_remaining": ("capacity_remaining", None),
         "critical_minimum": ("critical_minimum", None), "dynamic_minimum": ("dynamic_minimum", None),
         "dynamic_maximum": ("dynamic_maximum", None)}
BLOCK_UNITS = {"day": "kWh/day", "billing_period_per_day": "kWh/billing_day", "quarter": "kWh/quarter",
               "unstated": "kWh"}
APPLIES = {"energy": "usage"}  # curated applies_to -> tou_window.applies_to (the rest are the same word)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def region_of(r):
    m = re.search(r"zone: ([^;]+)", r["note"] or "") or re.search(r"/\s*(T[1-4])\b", r["note"] or "")
    return m.group(1).strip() if m else None


def day_before(day):
    return (datetime.date.fromisoformat(day) - datetime.timedelta(days=1)).isoformat()


def std_number(x):
    """A standard-unit value as text, to 12 significant digits: the unit conversion (dollars to cents, per year to per
    day) leaves binary-float noise such as 222.29000000000002 that no source prints."""
    return f"{float(x):.12g}"


def withdrawn_before(r):
    """True for a row whose name says the tariff is withdrawn from the first day of its year, or earlier: the AER keeps
    such rows (e.g. 'East Demand Small (withdrawn from 1 Jul 25)') beside the code's current row."""
    m = re.search(r"withdrawn from (\d{1,2}) (\w{3})\w* (\d{2,4})", r["tariff_name"] or "", re.I)
    if not m:
        return False
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    year = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
    when = f"{year:04d}-{months.index(m.group(2).lower()) + 1:02d}-{int(m.group(1)):02d}"
    return when <= bs.FIN_YEAR_DATES[r["fin_year"]][0]


def split_shared_codes(codes):
    """{code: rows} with each code that one document prints for several differently named tariffs (beyond one name
    per pricing zone) resolved: a block
    whose prices repeat another's is a repeated printing and dropped (Evoenergy 2026-27 codes 108, 109); blocks with
    their own prices become one tariff each, coded '<code> (<name>)' (the Energex and Ergon trial tariffs published
    with the code 'TBA')."""
    out = {}
    for code, rows in codes.items():
        by_name = defaultdict(list)
        for r in rows:
            by_name[r["tariff_name"]].append(r)
        regions = [{region_of(r) for r in rs} for rs in by_name.values()]
        if len(by_name) == 1 or (None not in set().union(*regions)
                                 and sum(map(len, regions)) == len(set().union(*regions))):
            out[code] = rows  # one name, or one name per pricing zone ('... T1', '... T2' priced by zone)
            continue
        prices = {}
        for name, rs in by_name.items():
            key = sorted((r["component"], r["time_band"], r["season"], r["unit_std"], region_of(r) or "", r["value"])
                         for r in rs)
            prices.setdefault(str(key), []).append(name)
        if len(prices) == 1:
            out[code] = by_name[min(by_name, key=lambda n: min(r["locator"] for r in by_name[n]))]
        else:
            for name, rs in by_name.items():
                out[f"{code} ({name})"] = rs
    return out


def norm_code(code):
    return re.sub(r"\s+", "", code).upper().rstrip("*")


def codes_of(r):
    """Codes a parsed row prices. The AER prints some codes jointly ('010, 011*', 'A100/F100'); a distributor code is
    taken as printed ('M/QOPCL' is one SA Power Networks code)."""
    if r["side"] != "AER":
        return [r["tariff_code"]]
    return [c.strip().rstrip("*").strip() for c in re.split(r",|/", r["tariff_code"]) if c.strip()]


class Builder:
    def __init__(self, parsed=None, metering=None, curated_files=None, docs=None, starts=None):
        self.docs = docs if docs is not None else bs.documents()
        self.starts = starts if starts is not None else bs.EFFECTIVE_FROM
        self.doc_by_path = {d["local_path"]: d for d in self.docs if d["local_path"]}
        self.parsed = parsed if parsed is not None else self.parser_rows("out/aer_long.csv", "out/dnsp/*.csv")
        self.metering = metering if metering is not None else self.parser_rows("out/dnsp_metering/*.csv")
        self.curated_files = curated_files if curated_files is not None else curated.load_all()
        self.tables = {t: {} for t in spec.TABLE_ORDER}
        self.problems = []
        self.by_norm = None
        self.periods = defaultdict(list)  # (distributor_id, fin_year, code) -> [(start, end, document_id, status)]

    @staticmethod
    def parser_rows(*patterns):
        rows = []
        for pattern in patterns:
            for p in sorted(glob.glob(os.path.join(ROOT, pattern))):
                rows += read_csv(p)
        if not rows:
            raise SystemExit(f"no parser output at {patterns}: run ./run.sh first")
        return rows

    def add(self, table, row):
        pk = tuple(row[c["name"]] for c in spec.BY_NAME[table]["columns"] if c["primary_key"])
        if pk in self.tables[table]:
            raise SystemExit(f"{table}: duplicate key {pk}")
        self.tables[table][pk] = row

    def doc(self, path, where):
        if path not in self.doc_by_path:
            raise SystemExit(f"{where}: {path!r} is not a retrieved document in sources/inventory.csv")
        return self.doc_by_path[path]

    # ------------------------------------------------------------------ reference tables
    def reference(self):
        for d in bs.DISTRIBUTORS:
            self.add("distributor", {k: d[k] for k in ("distributor_id", "name", "state", "iana_timezone")}
                     | {"observes_dst": int(d["observes_dst"])})
        for d in self.docs:
            self.add("source_document", {
                "document_id": d["document_id"], "distributor_id": d["distributor_id"], "fin_year": d["fin_year"],
                "publisher": "AER" if d["author"] == "AER" else "distributor", "document_type": d["document_type"],
                "hosted_by_aer": int(d["recon_side"] == "AER_HOSTED"), "version_label": d["version_label"],
                "version_seq": d["version_seq"], "price_status": d["price_status"],
                "published_on": d["publication_date"] or None, "source_url": d["source_url"] or None,
                "local_path": d["local_path"] or None, "sha256": d["sha256"] or None})

    # ------------------------------------------------------------------ tariffs and rates
    def candidates(self):
        """{(distributor_id, fin_year): {document_id: {code: [row]}}}: GST-exclusive total network prices, one price
        basis per document, repeated printings left out, and the without-metering copy of a component printed both
        ways."""
        by_doc = defaultdict(list)
        for r in self.parsed:
            if r["gst"] != "excl" or r["basis"] not in BASIS_ORDER or not r["value_std"] \
                    or REPEATED_PRINTING in (r["note"] or "") or withdrawn_before(r):
                continue
            by_doc[(self.doc(r["source_file"], "parser row")["document_id"], bs.ID_BY_NAME[r["distributor"]])].append(r)
        out = defaultdict(dict)
        for (doc_id, did), rows in by_doc.items():
            basis = next(b for b in BASIS_ORDER if any(r["basis"] == b for r in rows))
            groups = defaultdict(list)
            for r in rows:
                if r["basis"] == basis:
                    for code in codes_of(r):
                        key = (code, r["component"], r["time_band"], r["season"], r["unit_std"], region_of(r))
                        groups[key].append(r)
            codes = defaultdict(list)
            for (code, *_), g in groups.items():
                excl = [r for r in g if EXCL_METERING.search(r["note"] or "")]
                codes[code] += excl if excl and len(excl) < len(g) else g
            out[(did, rows[0]["fin_year"])][doc_id] = split_shared_codes(codes)
        return out

    def start_of(self, doc_id, fy):
        """First day a document's prices apply: 1 July, or the date build_support.EFFECTIVE_FROM records for a
        distributor's mid-year re-issue."""
        d = next(x for x in self.docs if x["document_id"] == doc_id)
        start, end = bs.FIN_YEAR_DATES[fy]
        day = self.starts.get(d["local_path"], start)
        if not start <= day <= end:
            raise SystemExit(f"{d['local_path']}: EFFECTIVE_FROM {day} is outside {fy}")
        return day

    def choose(self, did, fy, docs):
        """([(start, final document)], provisional document or None) for one distributor-year: the distributor's own
        published lists by the day they take effect, and the document that prices the year until then."""
        info = {x["document_id"]: x for x in self.docs}
        by_start = defaultdict(list)
        for k in docs:
            if info[k]["recon_side"] == "DNSP" and info[k]["price_status"] == "published":
                by_start[self.start_of(k, fy)].append(k)
        final = []
        for start, ks in sorted(by_start.items()):
            if len(ks) > 1:
                path = bs.FINAL_DOCUMENT.get((did, fy))
                ks = [k for k in ks if info[k]["local_path"] == path]
                if len(ks) != 1:
                    raise SystemExit(f"{did} {fy}: several published distributor price lists take effect {start}; "
                                     f"name the one customers are billed on in build_support.FINAL_DOCUMENT")
            final.append((start, ks[0]))
        finals = {k for _, k in final} | {k for ks in by_start.values() for k in ks}
        provisional = None
        for side in ("AER", "AER_HOSTED", "DNSP"):
            ks = [k for k in docs if info[k]["recon_side"] == side and k not in finals]
            if len(ks) > 1:
                raise SystemExit(f"{did} {fy}: several {side} documents price this year: {sorted(ks)}")
            if ks:
                provisional = ks[0]
                break
        return final, provisional

    def tariffs_and_rates(self):
        for (did, fy), docs in sorted(self.candidates().items()):
            fy_start, fy_end = bs.FIN_YEAR_DATES[fy]
            final, provisional = self.choose(did, fy, docs)
            sources = ([(fy_start, provisional, "provisional")] if provisional else []) + [
                (start, doc_id, "final") for start, doc_id in final]
            for code in sorted({c for _, doc_id, _ in sources for c in docs[doc_id]}):
                # the document in force from each start date; a final list replaces the provisional one from the day
                # it takes effect, and a mid-year re-issue replaces the codes it prices from its own start
                segments = {}
                for start, doc_id, status in sources:
                    if code in docs[doc_id]:
                        segments[start] = (doc_id, status)
                starts = sorted(segments)
                for i, start in enumerate(starts):
                    end = day_before(starts[i + 1]) if i + 1 < len(starts) else fy_end
                    doc_id, status = segments[start]
                    self.tariff_period(did, fy, code, start, end, doc_id, status, docs[doc_id][code])

    def tariff_period(self, did, fy, code, start, end, doc_id, status, rows):
        self.periods[(did, fy, code)].append((start, end, doc_id, status))
        name = Counter(r["tariff_name"] for r in rows if r["tariff_name"]).most_common(1)
        cls = Counter(r["customer_class"] for r in rows if r["customer_class"]).most_common(1)
        self.add("tariff", {
            "distributor_id": did, "tariff_code": code, "effective_from": start, "effective_to": end,
            "tariff_name": name[0][0] if name else None, "customer_class": cls[0][0] if cls else None,
            "status": status, "document_id": doc_id})
        for r in sorted(rows, key=lambda r: (r["locator"], r["component"], r["time_band"], r["season"])):
            if r["component"] == NO_PRICES:
                continue  # the AER lists the code with every price zero: a tariff with no rate rows
            tou, block = BANDS[r["time_band"]]
            self.rate(did, code, start, end, status, doc_id, {
                "charge_type": CHARGE_TYPES[r["charge_type"]], "tou_period": tou,
                "season": r["season"] or None, "block": block, "region": region_of(r),
                "value": r["value_std"], "unit": r["unit_std"], "value_published": r["value"],
                "unit_published": r["unit"] or None, "component": r["component"], "locator": r["locator"],
                "note": r["note"] or None})

    def rate(self, did, code, start, end, status, doc_id, r):
        r["value"] = std_number(r["value"])
        base = ":".join([did, code, start, r["charge_type"], slug(r["component"])]
                        + [str(x) for x in (r["tou_period"], r["season"], r["block"], r["region"]) if x])
        rid, n = base, 1
        while (rid,) in self.tables["rate"]:
            n += 1
            rid = f"{base}#{n}"
        self.add("rate", {"rate_id": rid, "distributor_id": did, "tariff_code": code, "effective_from": start,
                          "effective_to": end, "block_from": None, "block_to": None, "block_unit": None,
                          "status": status, "document_id": doc_id, **r})

    def metering_rates(self):
        """The distributor's separately priced metering charge, for the tariffs whose rates come from that document."""
        for r in self.metering:
            if r["gst"] != "excl":
                continue
            did, doc = bs.ID_BY_NAME[r["distributor"]], self.doc(r["source_file"], "metering row")
            value, unit = to_std(r["value"], r["unit"], r["component"])
            for start, end, doc_id, status in self.periods.get((did, r["fin_year"], r["tariff_code"]), []):
                if doc_id == doc["document_id"]:
                    self.rate(did, r["tariff_code"], start, end, status, doc_id, {
                        "charge_type": "metering", "tou_period": None, "season": None, "block": None,
                        "region": None, "value": value, "unit": unit, "value_published": r["value"],
                        "unit_published": r["unit"], "component": r["component"], "locator": r["locator"],
                        "note": r["note"] or None})

    # ------------------------------------------------------------------ curated facts
    def tariff_code(self, did, code, fy, where):
        """The stored tariff code a curated code names in that year: the same code, or the one code that differs from
        it only in spacing, case or a trailing '*' (curated files quote 'LVKVATOU1' where the price list prints
        'LVKVATOU 1'). None, recorded as a problem, when no tariff of that year has rates."""
        if (did, fy, code) in self.periods:
            return code
        if self.by_norm is None:
            self.by_norm = defaultdict(set)
            for d, f, c in self.periods:
                self.by_norm[(d, norm_code(c), f)].add(c)
        same = self.by_norm.get((did, norm_code(code), fy), set())
        if len(same) == 1:
            return next(iter(same))
        self.problems.append(f"{where}: no {fy} tariff {code!r} of {did} has rates" if not same else
                             f"{where}: {code!r} ({fy}) matches several tariffs {sorted(same)}")
        return None

    def curated_facts(self):
        for name, data in sorted(self.curated_files.items()):
            did = data["distributor"]
            self.tou_windows(did, data, f"curated/{name}.yaml")
            self.eligibility(did, data, f"curated/{name}.yaml")
            self.blocks(did, data, f"curated/{name}.yaml")

    def code_periods(self, did, code, fy, where):
        """(stored code, [(start, end)]) of the tariff periods a curated fact for that year applies to."""
        code = self.tariff_code(did, code, fy, where)
        return code, [(start, end) for start, end, _, _ in self.periods.get((did, fy, code), [])]

    def tou_windows(self, did, data, where):
        for s in data.get("tou_schedules") or []:
            doc = self.doc(s["doc"], where)["document_id"]
            for t in s.get("tariffs") or []:
                applies = APPLIES.get(t["applies_to"], t["applies_to"])
                for code in t["codes"]:
                    code, periods = self.code_periods(did, code, s["fin_year"], f"{where} {s['id']}")
                    for (start, end), w in ((p, w) for p in periods for w in s["windows"]):
                        ms = curated.months_of(w.get("months"))
                        months = None if ms is None else ",".join(str(m) for m in ms)
                        wid = ":".join([did, code, start, applies, w["period"], w["days"],
                                        f"{w['start']}-{w['end']}", "months-not-stated" if months is None else
                                        months.replace(",", ".")])
                        if (wid,) in self.tables["tou_window"]:
                            continue  # the same window stated by two schedules (e.g. a summary and a schedule)
                        self.add("tou_window", {
                            "window_id": wid, "distributor_id": did, "tariff_code": code, "effective_from": start,
                            "effective_to": end, "applies_to": applies, "tou_period": w["period"],
                            "period_label": str(w["label"]), "day_type": w["days"], "start_time": str(w["start"]),
                            "end_time": str(w["end"]), "months": months, "season": w.get("season"),
                            "time_basis": s["time_basis"], "public_holidays": s["public_holidays"],
                            "document_id": doc, "locator": w.get("locator") or s["locator"]})

    def eligibility(self, did, data, where):
        seen = defaultdict(set)
        counter = Counter()
        for r in data.get("eligibility") or []:
            doc = self.doc(r["doc"], where)["document_id"]
            fact = (r["rule_type"], r.get("operator"), r.get("value_num"), r.get("value_unit"), r.get("value_text"),
                    r.get("target_code"))
            for code in r["codes"]:
                code, periods = self.code_periods(did, code, r["fin_year"], f"{where} eligibility")
                for start, end in periods:
                    if fact in seen[(code, start)]:
                        continue  # the same criterion stated twice (another page or document of the same year)
                    seen[(code, start)].add(fact)
                    counter[(code, start, r["rule_type"])] += 1
                    self.add("eligibility", {
                        "criterion_id": f"{did}:{code}:{start}:{r['rule_type']}:"
                                        f"{counter[(code, start, r['rule_type'])]}",
                        "distributor_id": did, "tariff_code": code, "effective_from": start, "effective_to": end,
                        "criterion": r["rule_type"], "operator": r.get("operator"), "value_num": r.get("value_num"),
                        "value_unit": r.get("value_unit"), "value_text": r.get("value_text"),
                        "target_tariff_code": r.get("target_code"), "document_id": doc, "locator": r["locator"],
                        "quote": str(r["quote"])})

    def blocks(self, did, data, where):
        """Block bounds from the curated block ladders, onto the usage (or export) rates with that block number."""
        by_block = defaultdict(list)
        for row in self.tables["rate"].values():
            if row["block"] is not None and row["distributor_id"] == did:
                by_block[(row["tariff_code"], row["effective_from"], row["charge_type"], row["block"])].append(row)
        for r in data.get("steps") or []:
            charge_type = "export" if "export" in r["step_group"].lower() else "usage"
            for code in r["codes"]:
                code, periods = self.code_periods(did, code, r["fin_year"], f"{where} steps")
                for start, _ in periods:
                    rows = by_block.get((code, start, charge_type, r["step_index"]))
                    if not rows:
                        self.problems.append(f"{where} steps: {code} {r['fin_year']} has no {charge_type} rate for "
                                             f"block {r['step_index']} ({r['component_label']})")
                        continue
                    for row in rows:
                        row.update(block_from=r.get("lower_bound"), block_to=r.get("upper_bound"),
                                   block_unit=BLOCK_UNITS[r["reset_period"]])

    def build(self):
        self.reference()
        self.tariffs_and_rates()
        self.metering_rates()
        self.curated_facts()
        return self


def write_csv(path, cols, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow(["" if r[c] is None else r[c] for c in cols])
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(buf.getvalue())


def write_all(b, out_dir=OUT_DIR):
    os.makedirs(os.path.join(out_dir, "tables"), exist_ok=True)
    for t in spec.TABLES:
        cols = [c["name"] for c in t["columns"]]
        write_csv(os.path.join(out_dir, "tables", f"{t['name']}.csv"), cols,
                  [b.tables[t["name"]][k] for k in sorted(b.tables[t["name"]])])
    with open(os.path.join(out_dir, "schema.sqlite.sql"), "w", encoding="utf-8") as f:
        f.write(spec.ddl())
    with open(os.path.join(out_dir, "schema.json"), "w", encoding="utf-8") as f:
        json.dump(spec.json_spec(), f, indent=2, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=OUT_DIR, help="directory to write (default data/tariffdb)")
    ap.add_argument("--verbose", action="store_true", help="list each curated fact that was not loaded")
    a = ap.parse_args()
    b = Builder().build()
    write_all(b, a.out)
    for t in spec.TABLE_ORDER:
        print(f"{t:16} {len(b.tables[t]):6}")
    problems = sorted(set(b.problems))
    if problems:
        print(f"{len(problems)} curated facts not loaded: their code has no rates that year, or no block-numbered "
              f"rate (--verbose lists them)")
    if a.verbose:
        for p in problems:
            print("  " + p)
