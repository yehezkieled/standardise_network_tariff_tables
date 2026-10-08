"""Build the tariff database under data/tariffdb/: one CSV per table (tables/*.csv), the SQLite DDL
(schema.sqlite.sql) and the machine-readable table spec (schema.json), all from scripts/tariffdb/spec.py.

Inputs:
  - the parser outputs out/aer_long.csv (latest held AER version per year), out/dnsp/*.csv, out/dnsp_metering/*.csv
    and out/history/*.csv (archived documents of pricing years before 2023-24: scripts/history/; only the years
    in effect on or after build_support.FIRST_STORED_DAY are stored);
    run ./run.sh first; every row carries the cell or page it was read from;
  - data/tariffdb/curated/*.yaml: TOU windows, eligibility criteria and block bounds quoted from distributor documents;
  - the document registry, scripts/tariffdb/build_support.py over sources/inventory.csv and
    sources/archive/inventory.csv.

Which rates a tariff code gets, per distributor and pricing year:
  final        the distributor's own published price list prices the code (lists that take effect the same day must
               price different codes; build_support.FINAL_DOCUMENT names the billed one where two price the same
               code), or before 2023-24 the tariff schedule a state regulator published or approved;
  provisional  otherwise, the AER's latest held report for that year (v1 first, as it is published first), else a
               distributor document the AER hosts, else the distributor's own proposal.
A code is stored as the distributor spells it: an AER code that differs only in case or spacing is the same tariff,
and an AER code data/tariffdb/code_alias.csv maps onto codes the final list prices gives way to them.
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
import aliases  # noqa: E402
import build_support as bs  # noqa: E402
import curated  # noqa: E402
import joins  # noqa: E402
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
# rate.register: a usage price is for the controlled-load register when its component names controlled load (SAPN 'CL
# Peak usage', AusNet 'Dedicated circuit', Endeavour 'Controlled Load Flat'; not 'Uncontrolled'), or when the whole tariff
# is a controlled-load tariff (Ausgrid 'Controlled Load 1', Energex 'Economy', Ergon 'Volume Night Controlled'): its name
# says so and does not combine it with general supply ('General Supply Block + Controlled Load 1', '... & Dedicated
# Circuit', 'Two Rate 5d - Controlled Load')
CL_COMPONENT = re.compile(r"\bCL\b|(?<!un)controlled|\bcontrol load\b|dedicated|\beconomy\b", re.I)
CL_TARIFF = re.compile(r"(?<!un)controlled|dedicated|economy|hot water|off.?peak heating|\bOPCL\b", re.I)
COMBINED = re.compile(r"[&+,]|\bwith\b|\band\b|general supply|single rate|two rate|residential flat", re.I)
REGISTER_OF = {"usage": "general", "demand": "general", "capacity": "general", "export": "export"}


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
    return when <= bs.YEAR_DATES[r["fin_year"]][0]


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


def is_final(d):
    """A document whose prices customers were billed on: the distributor's own published price list, or (before the
    AER) the schedule a state regulator published or approved."""
    return (d["recon_side"] == "DNSP" and d["price_status"] == "published") or (
        d["recon_side"] == "REGULATOR_HOSTED" and d["price_status"] in ("published", "approved"))


def codes_of(r):
    """Codes a parsed row prices. The AER prints some codes jointly ('010, 011*', 'A100/F100'); a distributor code is
    taken as printed ('M/QOPCL' is one SA Power Networks code)."""
    if r["side"] != "AER":
        return [r["tariff_code"]]
    return [c.strip().rstrip("*").strip() for c in re.split(r",|/", r["tariff_code"]) if c.strip()]


class Builder:
    def __init__(self, parsed=None, metering=None, curated_files=None, docs=None, starts=None, code_aliases=None,
                 first_day=bs.FIRST_STORED_DAY):
        """first_day: store only the pricing years in effect on or after it (None: every year)."""
        self.docs = [d for d in (docs if docs is not None else bs.documents()) if bs.stored(d["fin_year"], first_day)]
        self.starts = starts if starts is not None else bs.EFFECTIVE_FROM | bs.archive_effective_from()
        self.doc_by_path = {d["local_path"]: d for d in self.docs if d["local_path"]}
        self.parsed = parsed if parsed is not None else self.parser_rows("out/aer_long.csv", "out/dnsp/*.csv",
                                                                                   "out/history/*.csv")
        self.parsed = [r for r in self.parsed if bs.stored(r["fin_year"], first_day)]
        self.metering = [r for r in (metering if metering is not None else self.parser_rows("out/dnsp_metering/*.csv"))
                         if bs.stored(r["fin_year"], first_day)]
        self.curated_files = {name: curated.per_year(data) for name, data in
                              (curated_files if curated_files is not None else curated.load_all()).items()}
        self.aliases = code_aliases if code_aliases is not None else aliases.load()
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
        if table == "rate":  # keyed by insertion until rate_ids() names them
            self.tables[table][("#", len(self.tables[table]))] = row
            return
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
                "document_id": d["document_id"], "distributor_id": d["distributor_id"], "pricing_year": d["fin_year"],
                "publisher": d["author"], "document_type": d["document_type"],
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
        start, end = bs.YEAR_DATES[fy]
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
            if is_final(info[k]):
                by_start[self.start_of(k, fy)].append(k)
        final = []
        for start, ks in sorted(by_start.items()):
            codes = [aliases.norm(c) for k in ks for c in docs[k]]
            if len(ks) > 1 and len(codes) != len(set(codes)):
                # lists that price the same code the same day are alternatives; lists that price different codes
                # (Country Energy's current and obsolete tariffs, 2007-08) together make the year's price list
                path = bs.FINAL_DOCUMENT.get((did, fy))
                ks = [k for k in ks if info[k]["local_path"] == path]
                if len(ks) != 1:
                    raise SystemExit(f"{did} {fy}: several published distributor price lists take effect {start}; "
                                     f"name the one customers are billed on in build_support.FINAL_DOCUMENT")
            final += [(start, k) for k in sorted(ks)]
        finals = {k for _, k in final} | {k for ks in by_start.values() for k in ks}
        provisional = None
        for side in ("AER", "AER_HOSTED", "DNSP", "REGULATOR_HOSTED"):
            ks = [k for k in docs if info[k]["recon_side"] == side and k not in finals]
            if len(ks) > 1:
                raise SystemExit(f"{did} {fy}: several {side} documents price this year: {sorted(ks)}")
            if ks:
                provisional = ks[0]
                break
        return final, provisional

    def tariffs_and_rates(self):
        years = [(did, fy, docs, *self.choose(did, fy, docs)) for (did, fy), docs in sorted(self.candidates().items())]
        spelling = defaultdict(dict)  # distributor -> normalised code -> its own spelling (latest year that prints it)
        for did, _, docs, final, _ in years:
            for _, doc_id in final:
                spelling[did].update({aliases.norm(c): c for c in docs[doc_id]})
        for did, fy, docs, final, provisional in years:
            fy_start, fy_end = bs.YEAR_DATES[fy]
            # an AER code is stored under the distributor's spelling ('LVDed' is United Energy's 'LVDED'), so its final
            # rates replace the provisional ones and its history stays under one code
            this_year = {aliases.norm(c): c for _, doc_id in final for c in docs[doc_id]}
            printed = {}  # stored code -> code as the provisional document prints it
            for code in docs.get(provisional) or {}:
                stored = this_year.get(aliases.norm(code)) or spelling[did].get(aliases.norm(code), code)
                if stored in printed:
                    raise SystemExit(f"{did} {fy}: {provisional} prints both {printed[stored]!r} and {code!r}")
                printed[stored] = code
            for code in sorted(set(printed) | {c for _, doc_id in final for c in docs[doc_id]}):
                # the document in force from each start date: a final list replaces the provisional one from the day
                # it takes effect, a mid-year re-issue replaces the codes it prices from its own start, and an AER code
                # the distributor prices under its own codes (data/tariffdb/code_alias.csv) ends that day (None)
                segments = {}
                if code in printed:
                    segments[fy_start] = (provisional, "provisional", docs[provisional][printed[code]])
                    for start, doc_id in final:
                        if aliases.targets(self.aliases, did, printed[code], start, docs[doc_id]):
                            segments[start] = None
                for start, doc_id in final:
                    if code in docs[doc_id]:
                        segments[start] = (doc_id, "final", docs[doc_id][code])
                starts = sorted(segments)
                for i, start in enumerate(starts):
                    if segments[start] is None:
                        continue
                    end = day_before(starts[i + 1]) if i + 1 < len(starts) else fy_end
                    doc_id, status, rows = segments[start]
                    self.tariff_period(did, fy, code, start, end, doc_id, status, rows)

    def tariff_period(self, did, fy, code, start, end, doc_id, status, rows):
        self.periods[(did, fy, code)].append((start, end, doc_id, status))
        name = Counter(r["tariff_name"] for r in rows if r["tariff_name"]).most_common(1)
        cls = Counter(r["customer_class"] for r in rows if r["customer_class"]).most_common(1)
        self.add("tariff", {
            "distributor_id": did, "tariff_code": code, "effective_from": start, "effective_to": end,
            "tariff_name": name[0][0] if name else None, "customer_class": cls[0][0] if cls else None,
            "status": status, "document_id": doc_id})
        cl_tariff = bool(name and CL_TARIFF.search(name[0][0]) and not COMBINED.search(name[0][0]))
        for r in sorted(rows, key=lambda r: (r["locator"], r["component"], r["time_band"], r["season"])):
            if r["component"] == NO_PRICES:
                continue  # the AER lists the code with every price zero: a tariff with no rate rows
            tou, block = BANDS[r["time_band"]]
            charge_type = CHARGE_TYPES[r["charge_type"]]
            register = REGISTER_OF.get(charge_type)
            if charge_type == "usage" and (cl_tariff or CL_COMPONENT.search(r["component"])):
                register = "controlled_load"
            self.rate(did, code, start, end, status, doc_id, {
                "charge_type": charge_type, "tou_period": tou, "register": register,
                "season": r["season"] or None, "block": block, "region": region_of(r),
                "value": r["value_std"], "unit": r["unit_std"], "value_published": r["value"],
                "unit_published": r["unit"] or None, "component": r["component"], "locator": r["locator"],
                "note": r["note"] or None})

    @staticmethod
    def rate_key(r):
        return ":".join([r["distributor_id"], r["tariff_code"], r["effective_from"], r["charge_type"],
                         slug(r["component"])]
                        + [str(x) for x in (r["tou_period"], r["season"], r["block"], r["region"]) if x])

    def rate(self, did, code, start, end, status, doc_id, r):
        r["value"] = std_number(r["value"])
        self.add("rate", {"rate_id": None, "distributor_id": did, "tariff_code": code, "effective_from": start,
                          "effective_to": end, "block_from": None, "block_to": None, "block_unit": None,
                          "condition": None, "status": status, "document_id": doc_id, **r})

    def rate_ids(self):
        """rate_id from each rate's final period and season (curated facts may set them after the price list):
        <did>:<code>:<from>:<charge_type>:<component>[:<period>][:<season>][:<block>][:<region>], #n on a repeat."""
        rows, self.tables["rate"] = list(self.tables["rate"].values()), {}
        for r in rows:
            base = self.rate_key(r)
            rid, n = base, 1
            while (rid,) in self.tables["rate"]:
                n += 1
                rid = f"{base}#{n}"
            r["rate_id"] = rid
            self.tables["rate"][(rid,)] = r

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
                        "charge_type": "metering", "tou_period": None, "register": None, "season": None,
                        "block": None,
                        "region": None, "value": value, "unit": unit, "value_published": r["value"],
                        "unit_published": r["unit"], "component": r["component"], "locator": r["locator"],
                        "note": r["note"] or None})

    # ------------------------------------------------------------------ curated facts
    def tariff_codes(self, did, code, fy, where):
        """The stored tariff codes a curated code names in that year: the same code; else the one code that differs from
        it only in spacing, case or a trailing '*' (curated files quote 'LVKVATOU1' where the price list prints
        'LVKVATOU 1'); else the codes the distributor prices it as by data/tariffdb/code_alias.csv (a fact Ergon states
        for EBDEM holds for EBDEMT1-T3). Empty, recorded as a problem, when no tariff of that year has rates."""
        if (did, fy, code) in self.periods:
            return [code]
        if self.by_norm is None:
            self.by_norm = defaultdict(set)
            for d, f, c in self.periods:
                self.by_norm[(d, aliases.norm(c), f)].add(c)
        same = self.by_norm.get((did, aliases.norm(code), fy), set())
        if len(same) > 1:
            self.problems.append(f"{where}: {code!r} ({fy}) matches several tariffs {sorted(same)}")
            return []
        if same:
            return list(same)
        year = [c for d, f, c in self.periods if d == did and f == fy]
        found = aliases.targets(self.aliases, did, code, bs.YEAR_DATES[fy][0], year)
        if not found:
            self.problems.append(f"{where}: no {fy} tariff {code!r} of {did} has rates")
        return found

    def curated_facts(self):
        for name, data in sorted(self.curated_files.items()):
            did = data["distributor"]
            self.tou_windows(did, data, f"curated/{name}.yaml")
            self.eligibility(did, data, f"curated/{name}.yaml")
            self.blocks(did, data, f"curated/{name}.yaml")
            self.metering_schedules(did, data, f"curated/{name}.yaml")
            self.conditions(did, data, f"curated/{name}.yaml")
            self.rate_periods(did, data, f"curated/{name}.yaml")
            self.charge_rules(did, data, f"curated/{name}.yaml")

    def fy_periods(self, did, fy):
        """[(code, (start, end, document_id, status))] of every tariff period of a distributor's pricing year."""
        return [(c, p) for (d, f, c), ps in self.periods.items() if d == did and f == fy for p in ps]

    def code_periods(self, did, code, fy, where):
        """[(stored code, start, end)] of the tariff periods a curated fact for that year applies to."""
        return [(c, start, end) for c in self.tariff_codes(did, code, fy, where)
                for start, end, _, _ in self.periods[(did, fy, c)]]

    def tou_windows(self, did, data, where):
        for s in data.get("tou_schedules") or []:
            doc = self.doc(s["doc"], where)["document_id"]
            for t in s.get("tariffs") or []:
                applies = APPLIES.get(t["applies_to"], t["applies_to"])
                for named in t["codes"]:
                    periods = self.code_periods(did, named, s["fin_year"], f"{where} {s['id']}")
                    for (code, start, end), w in ((p, w) for p in periods for w in s["windows"]):
                        ms = curated.months_of(w.get("months"))
                        months = None if ms is None else ",".join(str(m) for m in ms)
                        wid = ":".join([did, code, start, applies, w["period"], w["days"],
                                        f"{w['start']}-{w['end']}", months.replace(",", ".") if months else
                                        f"season-{w['season']}" if w.get("season") else "months-not-stated"])
                        if (wid,) in self.tables["tou_window"]:
                            continue  # the same window stated by two schedules (e.g. a summary and a schedule)
                        self.add("tou_window", {
                            "window_id": wid, "distributor_id": did, "tariff_code": code, "effective_from": start,
                            "effective_to": end, "applies_to": applies, "tou_period": w["period"],
                            "period_label": str(w["label"]), "day_type": w["days"], "start_time": str(w["start"]),
                            "end_time": str(w["end"]), "months": months, "season": w.get("season"),
                            "season_label": None if w.get("season_label") is None else str(w["season_label"]),
                            "time_basis": s["time_basis"], "public_holidays": s["public_holidays"],
                            "document_id": doc, "locator": w.get("locator") or s["locator"]})

    def eligibility(self, did, data, where):
        seen = defaultdict(set)
        counter = Counter()
        for r in data.get("eligibility") or []:
            doc = self.doc(r["doc"], where)["document_id"]
            fact = (r["rule_type"], r.get("operator"), r.get("value_num"), r.get("value_unit"), r.get("value_text"),
                    r.get("target_code"))
            for named in r["codes"]:
                for code, start, end in self.code_periods(did, named, r["fin_year"], f"{where} eligibility"):
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
            for named in r["codes"]:
                for code, start, _ in self.code_periods(did, named, r["fin_year"], f"{where} steps"):
                    rows = by_block.get((code, start, charge_type, r["step_index"]))
                    if not rows:
                        self.problems.append(f"{where} steps: {code} {r['fin_year']} has no {charge_type} rate for "
                                             f"block {r['step_index']} ({r['component_label']})")
                        continue
                    for row in rows:
                        row.update(block_from=r.get("lower_bound"), block_to=r.get("upper_bound"),
                                   block_unit=BLOCK_UNITS[r["reset_period"]])

    def conditions(self, did, data, where):
        """rate.condition from the curated conditions: every rate of the codes and year with that component."""
        by_component = defaultdict(list)
        for row in self.tables["rate"].values():
            if row["distributor_id"] == did:
                by_component[(row["tariff_code"], row["effective_from"], row["component"])].append(row)
        for r in data.get("conditions") or []:
            for named in r["codes"]:
                for code, start, _ in self.code_periods(did, named, r["fin_year"], f"{where} conditions"):
                    rows = by_component.get((code, start, r["component"]))
                    if not rows:
                        self.problems.append(f"{where} conditions: {code} {r['fin_year']} has no rate "
                                             f"{r['component']!r}")
                    for row in rows or []:
                        row["condition"] = r["condition"]

    def metering_schedules(self, did, data, where):
        """Metering rates from a network-wide metering schedule (a metering side-output row with no tariff code), for
        the codes and sites the curated `metering` fact quotes. Only tariff periods whose rates come from the
        schedule's own document get one, so a tariff's prices never mix documents."""
        schedule = defaultdict(list)
        for r in self.metering:
            if not r["tariff_code"] and r["gst"] == "excl":
                schedule[(bs.ID_BY_NAME[r["distributor"]], r["fin_year"], r["component"])].append(r)
        for m in data.get("metering") or []:
            rows = schedule.get((did, m["fin_year"], m["schedule"]))
            if not rows:
                self.problems.append(f"{where} metering: no {m['fin_year']} metering schedule row {m['schedule']!r}")
                continue
            codes = m["codes"] if m["codes"] != "all" else sorted({c for d, f, c in self.periods
                                                                    if d == did and f == m["fin_year"]})
            for named in codes:
                for code, start, end in self.code_periods(did, named, m["fin_year"], f"{where} metering"):
                    for r in rows:
                        doc = self.doc(r["source_file"], "metering schedule")["document_id"]
                        period = next((p for p in self.periods[(did, m["fin_year"], code)] if p[0] == start), None)
                        if period is None or period[2] != doc:
                            continue
                        value, unit = to_std(r["value"], r["unit"], r["component"])
                        label = r["component"] if r["meter_class"] in ("", r["component"]) else \
                            f"{r['meter_class']} - {r['component']}"
                        self.rate(did, code, start, end, period[3], doc, {
                            "charge_type": "metering", "tou_period": None, "register": None, "season": None,
                            "block": None, "region": None, "value": value, "unit": unit,
                            "value_published": r["value"], "unit_published": r["unit"], "component": label,
                            "locator": r["locator"], "condition": m.get("condition"),
                            "note": "; ".join(x for x in (r["note"], f"applies per {where} ({m['locator']})") if x)})

    def rate_periods(self, did, data, where):
        """rate.tou_period and season from the curated rate_periods: every rate of the codes and year with that
        component."""
        by_component = defaultdict(list)
        for row in self.tables["rate"].values():
            if row["distributor_id"] == did:
                by_component[(row["tariff_code"], row["effective_from"], row["component"])].append(row)
        for r in data.get("rate_periods") or []:
            for named in r["codes"]:
                for code, start, _ in self.code_periods(did, named, r["fin_year"], f"{where} rate_periods"):
                    rows = by_component.get((code, start, r["component"]))
                    if not rows:
                        self.problems.append(f"{where} rate_periods: {code} {r['fin_year']} has no rate "
                                             f"{r['component']!r}")
                    for row in rows or []:
                        said = []
                        for key in ("tou_period", "season"):
                            if r.get(key) and row[key] != r[key]:
                                said.append(f"{key} (the price list says {row[key]})" if row[key] else key)
                                row[key] = r[key]
                        if said:
                            row["note"] = "; ".join(x for x in (row["note"], f"{' and '.join(said)} from {where} "
                                                                             f"({r['locator']})") if x)

    def charge_rules(self, did, data, where):
        """Rules for named codes first; then `codes: all` (a rule the document states for every tariff) fills each
        tariff-period of that year with a rate of the charge type priced in the rule's measure that a named rule did
        not cover."""
        entries = data.get("charge_rules") or []
        for r in [r for r in entries if r["codes"] != "all"] + [r for r in entries if r["codes"] == "all"]:
            doc = self.doc(r["doc"], where)["document_id"]
            if r["codes"] == "all":
                year = {(c, p[0]) for c, p in self.fy_periods(did, r["fin_year"])}
                periods = sorted({(x["tariff_code"], x["effective_from"], x["effective_to"])
                                  for x in self.tables["rate"].values()
                                  if x["distributor_id"] == did and x["charge_type"] == r["charge_type"]
                                  and re.search(rf"/{r['measure']}(/|$)", x["unit"])
                                  and (x["tariff_code"], x["effective_from"]) in year})
            else:
                periods = [p for named in r["codes"]
                           for p in self.code_periods(did, named, r["fin_year"], f"{where} charge_rules")]
            for code, start, end in periods:
                rid = ":".join([did, code, start, r["charge_type"], r.get("tou_period") or "all",
                                r.get("season") or "all", r["measure"]])
                row = {"rule_id": rid, "distributor_id": did, "tariff_code": code, "effective_from": start,
                       "effective_to": end, "charge_type": r["charge_type"], "tou_period": r.get("tou_period"),
                       "season": r.get("season"), "measure": r["measure"], "interval_min": r.get("interval_min"),
                       "method": r["method"], "n": r.get("n"), "reset": r["reset"],
                       "minimum_value": r.get("minimum_value"), "threshold_value": r.get("threshold_value"),
                       "allowance_per_day": r.get("allowance_per_day"),
                       "allowance_rollover": None if r.get("allowance_rollover") is None
                       else int(r["allowance_rollover"]),
                       "document_id": doc, "locator": r["locator"], "quote": str(r["quote"]),
                       "note": r.get("note")}
                old = self.tables["charge_rule"].get((rid,))
                if old is None:
                    self.add("charge_rule", row)
                elif r["codes"] == "all":
                    continue
                elif {k: v for k, v in old.items() if k not in ("document_id", "locator", "quote", "note")} != \
                        {k: v for k, v in row.items() if k not in ("document_id", "locator", "quote", "note")}:
                    raise SystemExit(f"{where} charge_rules: two different rules for {rid}")

    def periods_from_windows(self):
        """A demand or capacity rate the price list prints without a period ('Demand charge') is measured in the
        tariff's demand window: it takes the period of the tariff's windows for its charge group and season (the
        windows stated for that season when there are any) when they name exactly one (Ausgrid 'Demand charge - high
        season' and its 'High season demand window')."""
        windows = defaultdict(list)
        for w in self.tables["tou_window"].values():
            windows[(w["distributor_id"], w["tariff_code"], w["effective_from"])].append(w)
        for r in self.tables["rate"].values():
            ws = windows.get((r["distributor_id"], r["tariff_code"], r["effective_from"]))
            if r["tou_period"] is not None or r["charge_type"] not in ("demand", "capacity") or not ws:
                continue
            cands = [w for w in joins.group_windows(joins.group_of(r), ws)
                     if joins.same_season(r["season"], w["season"])]
            named = {w["tou_period"] for w in ([w for w in cands if w["season"] == r["season"]] or cands)}
            if len(named) == 1 and named != {"anytime"}:
                r["tou_period"] = named.pop()
                r["note"] = "; ".join(x for x in (r["note"], "tou_period from the tariff's demand window") if x)

    def build(self):
        self.reference()
        self.tariffs_and_rates()
        self.metering_rates()
        self.curated_facts()
        self.periods_from_windows()
        self.rate_ids()
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
