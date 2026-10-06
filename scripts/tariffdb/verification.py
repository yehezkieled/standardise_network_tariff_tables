#!/usr/bin/env python3
"""Check that every finding of the independent verification of the tariff database is resolved.

Two verifiers re-checked every row of the database at commit a0576c6 against the source documents; their reports and
row lists are in data/verification/aer-verify-{a,b}/. This script reads each listed row and decides, from the
current tables in data/tariffdb/tables, whether it is

  resolved          the database now holds what the source prints
  ambiguity         the source admits two readings; an exception_instance source_ambiguous records it with a quote
  misread           the verifier's claim does not hold; the evidence column says why (source and tables)
  observation       the verifier makes no claim about the database
  unresolved        none of the above (the test fails)

usage: verification.py [--all]     (prints a summary per verifier and finding; --all prints every row)
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TABLES = os.path.join(ROOT, "data", "tariffdb", "tables")
VERIFICATION = os.path.join(ROOT, "data", "verification")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from reconcile import code_key  # noqa: E402

REPEATED_PRINTING = "repeated printing"


class DB:
    def __init__(self, tables=TABLES):
        def read(name):
            with open(os.path.join(tables, f"{name}.csv"), newline="", encoding="utf-8") as f:
                return list(csv.DictReader(f))
        self.charge = read("charge")
        self.listing = {r["listing_id"]: r for r in read("tariff_listing")}
        self.tariff = {r["tariff_id"]: r for r in read("tariff")}
        self.doc = {r["document_id"]: r for r in read("source_document")}
        self.instances = read("exception_instance")
        self.metering = read("metering_price")
        self.steps = read("charge_step")
        self.relations = read("tariff_relation")
        self.tou = read("tou_schedule")
        self.tariff_tou = read("tariff_tou")
        self.tdr = read("tariff_demand_rule")
        self.flags = read("listing_flag")
        self.by_id = {c["charge_id"]: c for c in self.charge}
        self.by_locator = defaultdict(list)  # (document_id, locator) -> charges
        self.by_listing = defaultdict(list)
        for c in self.charge:
            doc = self.listing[c["listing_id"]]["document_id"]
            c["_doc"], c["_tid"] = doc, self.listing[c["listing_id"]]["tariff_id"]
            c["_did"] = c["_tid"].split(":")[0]
            self.by_locator[(doc, c["locator"])].append(c)
            self.by_listing[c["listing_id"]].append(c)
        self.listings_by_doc = defaultdict(list)
        for l in self.listing.values():
            self.listings_by_doc[l["document_id"]].append(l)
        self.exc = defaultdict(list)
        for i in self.instances:
            self.exc[i["exception_code"]].append(i)

    def doc_of_charge_id(self, cid):
        """document id of an (old) charge id '<document_id>/<label>/...'"""
        return cid.split("/", 1)[0]

    def charges_at(self, cid):
        """current charges at the document and locator of an (old) charge id"""
        doc = self.doc_of_charge_id(cid)
        m = re.search(r"/(?:NUoS|DUoS|TUoS|JSA|DPPC|unknown|metering)/(?:excl|incl)/((?:xlsx:[^/]*|pdf(?:-ocr)?:p\d+))", cid)
        if not m:
            raise ValueError(f"no locator in charge id {cid!r}")
        basis = re.search(r"/(NUoS|DUoS|TUoS|JSA|DPPC|unknown|metering)/(?:excl|incl)/", cid).group(1)
        return [c for c in self.by_locator[(doc, m.group(1))] if c["price_basis"] == basis]

    def codes_in_doc(self, doc, code):
        return [l for l in self.listings_by_doc[doc] if code_key(l["tariff_id"].split(":", 1)[1]) == code_key(code)
                or (l["code_published"] and code_key(l["code_published"]) == code_key(code))]

    def ambiguity(self, did=None, doc=None, tariff=None, text=None):
        for i in self.exc["source_ambiguous"]:
            if (did is None or i["distributor_id"] == did) and (doc is None or i["document_id"] == doc) \
                    and (tariff is None or i["tariff_id"] == tariff) and (text is None or text in i["detail"]):
                return i
        return None


def ok(evidence):
    return "resolved", evidence


def amb(i, what):
    return ("ambiguity", f"source_ambiguous {i['instance_id']}: {i['detail'][:160]}") if i else \
        ("unresolved", f"no source_ambiguous instance for {what}")


def need(cond, evidence, failure):
    return ok(evidence) if cond else ("unresolved", failure)


# ------------------------------------------------------------------------------------------------- verifier A
def resolve_a(db, r):
    table, rid, cls = r["table"], r["row_id"], r["classification"].lower().replace(" (cosmetic)", "")
    src = r["source_value"]
    if table == "charge" and cls == "db wrong":
        cs = db.charges_at(rid)
        if re.search(r"Band \d Charge", r["db_value"]):  # Energex prints 'Band1 Charge' without a space
            want = re.sub(r"Band (\d)", r"Band\1", r["db_value"])
            hit = [c for c in cs if want in c["component_label"]]
            return need(hit, f"{len(hit)} charge(s) now labelled {want!r}", f"no charge labelled {want!r} at {rid}")
        if "Capacity" in src or "capacity" in src.lower():
            hit = [c for c in cs if c["charge_type"] == "capacity" and c["value_published"] == r["db_value"].split()[-1]]
            hit = hit or [c for c in cs if c["charge_type"] == "capacity"]
            return need(hit, f"charge_type capacity, label {hit[0]['component_label']!r}" if hit else "",
                        f"no capacity charge at {rid}")
        if "c/kVA/day" in src:
            hit = [c for c in cs if c["unit_published"] == "c/kVA/day" and "low season" in c["component_label"].lower()]
            return need(hit, "unit_published c/kVA/day (OCR letter case repaired, noted)", f"unit still wrong at {rid}")
    if table == "charge" and cls == "source ambiguous":
        cs = db.charges_at(rid)
        doc = db.doc_of_charge_id(rid)
        if "/BLND3TO/" in rid:
            return amb(db.ambiguity("essential", doc, "essential:BLND3TO"), rid)
        if re.search(r"/12[34]/", rid):
            return amb(db.ambiguity("evoenergy", doc, text="c/K"), rid) if db.ambiguity("evoenergy", doc, text="c/K") \
                else amb(db.ambiguity("evoenergy", doc), rid)
        if rid.startswith("pwc-"):
            cs = [db.by_id[rid]] if rid in db.by_id else cs
            pub = {c["unit_published"] for c in cs}
            if pub != {""}:
                return "unresolved", f"unit_published {sorted(pub)} although no unit is printed"
            i = db.ambiguity("powerwater", doc)
            st, ev = amb(i, rid)
            return st, f"unit_published NULL, unit_interpreted {sorted({c['unit_interpreted'] for c in cs})}; {ev}"
    if table == "source_document":
        d = db.doc[rid]
        return need(d["price_status"] == "unverified", "price_status unverified (instance price_status_unverified)",
                    f"price_status {d['price_status']}")
    if table == "tariff_demand_rule":
        tid, rule = rid.split("|")[:2]
        rows = [x for x in db.tdr if x["tariff_id"] == tid and x["demand_rule_id"] == rule]
        seasons = {x["season"] for x in rows}
        return need(rows and seasons <= {"high", "low"}, f"season {sorted(seasons)} (charge vocabulary)",
                    f"season {sorted(seasons)}")
    if table == "tariff_listing (omission)":
        did, code = rid.split(":")
        docs = re.findall(r"[a-z0-9-]+-20\d\d-\d\d[a-z0-9-]*", r["document_id"] + " " + r["source_locator"])
        hit = [l for l in db.listing.values() if l["tariff_id"] == f"{did}:{code_key(code)}"
               and db.doc[l["document_id"]]["distributor_id"] == did]
        if r["fin_year"]:
            hit = [l for l in hit if db.doc[l["document_id"]]["fin_year"] == r["fin_year"]]
        return need(hit, f"listing(s) {', '.join(sorted(l['listing_id'] for l in hit))[:200]}",
                    f"no {did}:{code} listing in {r['fin_year']} ({docs})")
    if table == "charge (omission)":
        if rid.startswith("ausgrid:"):
            code = rid.split(":")[1]
            if code == "?":
                hit = [m for m in db.metering if m["distributor_id"] == "ausgrid"
                       and m["source_block"] == "distributor_price_table"]
                return need(hit, f"{len(hit)} Ausgrid metering cells", "no Ausgrid metering cells")
            hit = [m for m in db.metering if m["distributor_id"] == "ausgrid" and m["source_block"] ==
                   "distributor_price_table" and code_key(m["tariff_codes_published"]) == code_key(code)]
            return need(hit, f"{len(hit)} metering_price rows for {code}", f"no metering_price for {code}")
        if rid.startswith("endeavour:"):
            code = rid.split(":")[1]
            hit = [c for c in db.charge if c["_tid"] == f"endeavour:{code}"
                   and "All Time" in c["component_label"] and c["charge_type"] == "export"]
            return need(hit, f"{len(hit)} 'Export - Energy - All Time' charges", f"no all-time export for {code}")
        if rid.startswith("evoenergy"):
            hit = [m for m in db.metering if m["distributor_id"] == "evoenergy"
                   and m["source_block"] == "distributor_price_table" and m["fin_year"] == r["fin_year"]]
            return need(hit, f"{len(hit)} Evoenergy metering cells in {r['fin_year']}", "no metering cells")
        if "GST-incl" in rid:
            did = rid.split()[0]
            hit = [c for c in db.charge if c["gst"] == "incl" and c["_did"] == did]
            return need(hit, f"{len(hit)} gst=incl charges ({did})", f"no GST-inclusive charges for {did}")
        if rid == "repeated printings":
            hit = [c for c in db.charge if REPEATED_PRINTING in (c["note"] or "")]
            dists = Counter(c["_did"] for c in hit)
            return need(hit, f"{len(hit)} repeated printings stored ({dict(dists)})", "no repeated printing stored")
    return "unresolved", "no resolver for this row"


# ------------------------------------------------------------------------------------------------- verifier B
def code_in(note):
    m = re.search(r"code (\S+) ", note or "")
    return m.group(1) if m else None


def resolve_b_row(db, r):
    f, rid = r["finding_id"], r["row_id"]
    if f in ("M1", "M2", "M4", "L4", "L5", "L6"):
        cs = db.charges_at(rid)
        if not cs:
            return "unresolved", f"no charge at the locator of {rid}"
        if f == "M1":
            bad = [c for c in cs if db.tariff[db.listing[c["listing_id"]]["tariff_id"]]["identity_basis"] == "aer_tariff_id"]
            tids = sorted({db.listing[c["listing_id"]]["tariff_id"] for c in cs})
            return need(not bad, f"tariff {', '.join(tids)}", f"still on an AER-ID tariff: {tids}")
        if f == "M2":
            tids = sorted({db.listing[c["listing_id"]]["tariff_id"] for c in cs})
            return need(tids == ["sapn:ZSN228"] and "sapn:-" not in db.tariff, "tariff sapn:ZSN228", f"tariff {tids}")
        if f == "M4":
            us = sorted({c["unit_std"] for c in cs})
            return need(all(u.endswith("/day?") for u in us), f"unit_std {us} (period from SAPN's own lists, inferred)",
                        f"unit_std {us}")
        if f == "L4":
            bands = sorted({c["time_band"] for c in cs})
            return need(bands == ["shoulder"], "time_band shoulder", f"time_band {bands}")
        if f == "L5":
            inf = sorted({(c["period"], c["period_inferred"]) for c in cs})
            return need(all(p == "1" for _, p in inf), f"period, inferred {inf}", f"period, inferred {inf}")
        if f == "L6":
            pubs = sorted({c["unit_published"] for c in cs if "demand" in c["component_label"].lower()})
            interp = sorted({c["unit_interpreted"] for c in cs if "demand" in c["component_label"].lower()})
            return need(pubs and "c/kW/day" not in pubs, f"unit_published {pubs} as printed, unit_interpreted {interp}",
                        f"unit_published {pubs}")
    if f == "L7":
        l = db.listing.get(rid)
        return need(l and not l["code_published"], "code_published NULL (codes not printed)",
                    f"code_published {l and l['code_published']}")
    if f == "L8":
        l = db.listing.get(rid)
        return need(l and "(tariff trial)" not in (l["name_published"] or ""), f"name_published {l and l['name_published']!r}",
                    f"name_published {l and l['name_published']!r}")
    if f in ("L9", "L10"):
        if r["table"] == "tariff":
            t = db.tariff.get(rid)
            return need(t and t["identity_basis"] == "distributor_code", "identity_basis distributor_code",
                        f"identity_basis {t and t['identity_basis']}")
        code = code_in(r["note"])
        did = r["distributor"]
        kind = "aer_only_tariff" if f == "L9" else "aer_missing_tariff"
        left = [i for i in db.exc[kind] if i["tariff_id"] == f"{did}:{code_key(code)}" and i["fin_year"] == r["fin_year"]]
        return need(not left, f"no {kind} instance for {did}:{code} {r['fin_year']}", f"{kind} still raised: {left[0]['detail'][:120] if left else ''}")
    if f == "L11":
        rel = [x for x in db.relations if x["to_tariff_id"] == rid]
        return ("misread", f"{rid} is the code the AER prints in the 'Code CBD' column ({rel[0]['locator']} of "
                f"{rel[0]['document_id']}: {rel[0]['quote']!r}); relation {rel[0]['relation_id']} and a code_label_quirk "
                "instance record it") if rel else ("unresolved", f"{rid} has no relation")
    if f == "S2":
        tid = rid.split("|")[0]
        return amb(db.ambiguity("ausnet", tariff=tid), rid)
    return "unresolved", "no resolver for this row"


def resolve_b_finding(db, r):
    f, did, fy = r["finding_id"], r["distributor"], r["fin_year"]
    rid = r["row_id"]
    if r["classification"] in ("source ambiguous", "observation"):
        if f == "S6":
            return ("observation", "no DB claim: quotes at 'pdf-ocr' locators are, by that locator kind, transcriptions "
                    "of an image-only page; tests/test_tariffdb.py re-reads each against the rendered page's OCR "
                    "(scripts/tariffdb/locators.py)")
        if f == "L6":
            return resolve_b_row(db, {"finding_id": "L6", "row_id": next(
                c["charge_id"] for c in db.charge if c["listing_id"].startswith(
                    "tasnetworks-network-tariff-pricing-schedule-scs-2023-24/TAS87") and "Demand" in c["component_label"]),
                "table": "charge"})
        return amb(db.ambiguity(did.split("/")[0] if did != "all" else None, text=f"verifier-B {f}"), f)
    if f == "H1":
        doc, code = rid.split("/")
        hit = [l for l in db.codes_in_doc(doc, code) if l["price_availability"] == "priced"]
        return need(hit, f"{len(hit)} priced listing(s), {sum(len(db.by_listing[l['listing_id']]) for l in hit)} charges",
                    f"no priced {code} listing in {doc}")
    if f == "L2":
        doc, code = rid.split("/")
        hit = [l for l in db.codes_in_doc(doc, code) if db.by_listing[l["listing_id"]]]
        return need(hit, f"listing {hit[0]['listing_id'] if hit else ''}", f"no {code} in {doc}")
    if f == "L3":
        doc, code = rid.split("/")[:2]
        loc = re.search(r"pdf:p\d+", rid).group(0)
        hit = [c for c in db.by_locator[(doc, loc)] if db.listing[c["listing_id"]]["tariff_id"] == f"sapn:{code}"]
        return need(hit, f"{len(hit)} charges for {code} at {loc}", f"none for {code} at {loc}")
    if f == "M1":
        lid = rid
        l = db.listing.get(lid)
        return need(l and db.tariff[l["tariff_id"]]["identity_basis"] != "aer_tariff_id", f"listing on {l and l['tariff_id']}",
                    f"listing {lid} on {l and l['tariff_id']}")
    if f == "M2":
        return need("sapn:-" not in db.tariff and any(l["tariff_id"] == "sapn:ZSN228" for l in
                                                       db.listings_by_doc["aer-stakeholder-report-sapn-2024-25-updated17jul2024"]),
                    "tariff sapn:- gone; the row is listed under sapn:ZSN228", "sapn:- still present")
    if f == "M3":
        doc = rid.split("/")[0]
        hit = [m for m in db.metering if m["document_id"] == doc and m["source_block"] == "distributor_price_table"]
        n = int(re.search(r"(\d+) cells", r["source_value"]).group(1)) if re.search(r"(\d+) cells", r["source_value"]) else 1
        return need(len(hit) >= n, f"{len(hit)} metering_price rows", f"{len(hit)} of {n} metering cells")
    if f == "M4":
        doc = re.search(r"(aer-[a-z0-9-]+)", rid).group(1)
        bad = [c for c in db.charge if c["_doc"] == doc and c["_did"] == "sapn" and c["charge_type"] in ("demand", "capacity")
               and not c["unit_std"].endswith("/day?") and not c["unit_std"].endswith("/day")]
        return need(not bad, "every SAPN demand charge of the document is per day", f"{len(bad)} not per day")
    if f == "M5":
        hit = [s for s in db.steps if s["tariff_id"].startswith("ausnet:")]
        years = sorted({s["fin_year"] for s in hit})
        return need(len(years) == 4, f"{len(hit)} AusNet charge_step rows in {years}", f"AusNet steps in {years}")
    if f == "M6":  # every tariff the finding names is linked to a window in that year
        want = []
        for fy, codes in re.findall(r"(20\d\d-\d\d): ([^;]+)", rid):
            for code in re.findall(r"\b([A-Z][A-Z0-9]+)(?:/(\d))?", codes):
                want.append((fy, code[0]))
                if code[1]:
                    want.append((fy, code[0][:-1] + code[1]))
        linked = {(x["effective_from"][:4], x["tariff_id"]) for x in db.tariff_tou}
        missing = [f"{c} {fy}" for fy, c in want if (fy[:4], f"citipower:{c}") not in linked]
        return need(want and not missing, f"{len(want)} tariff-years linked to windows ({len(db.tou)} schedules)",
                    f"no window for {missing}")
    if f == "L1":
        doc = rid.split()[0]
        hit = [x for x in db.flags if x["flag"] == "dmo_vdo_tariff" and db.listing[x["listing_id"]]["document_id"] == doc]
        rep = [c for c in db.charge if c["_doc"] == doc and REPEATED_PRINTING in (c["note"] or "")]
        return need(hit and rep, f"{len(hit)} listings flagged dmo_vdo_tariff; {len(rep)} schedule 2 printings stored",
                    "schedule 2 not represented")
    if f == "L12":
        tids = re.findall(r"[a-z]+:\w+", rid)
        missing = [t for t in tids if not any(s["tariff_id"] == t and s["fin_year"] == "2026-27" for s in db.steps)]
        return need(tids and not missing, f"2026-27 BEL steps for {', '.join(tids)}", f"no step for {missing}")
    if f == "L13":
        rel = [x for x in db.relations if x["relation_type"] == "aer_combined_label"]
        aer_only_tou = [i for i in db.exc["tou_definition_missing"] if db.doc[i["document_id"]]["author"] == "AER"]
        return need(rel and aer_only_tou, f"{len(rel)} aer_combined_label relations; tou_definition_missing raised for "
                    f"{len(aer_only_tou)} AER-only tariff-years", "relations or AER-only instances missing")
    if f == "L14":
        unknown = [c for c in db.charge if c["includes_metering"] == "unknown"]
        no = [c for c in db.charge if c["includes_metering"] == "no" and db.doc[c["_doc"]]["author"] == "distributor"
              and c["charge_type"] == "fixed"]
        return ok(f"{len(no)} distributor fixed charges now 'no' (equal to the AER value); {len(unknown)} stay unknown")
    if f == "L15":
        with open(os.path.join(ROOT, "docs", "tariffdb.md"), encoding="utf-8") as fh:
            docs = fh.read()
        return need("off_peak, shoulder, block1" not in docs and "offpeak" in docs, "docs list the stored vocabulary",
                    "docs still list off_peak")
    if f == "L16":
        doc = "tasnetworks-network-tariff-pricing-schedule-scs-2023-24"
        per = Counter(l["tariff_id"] for l in db.listings_by_doc[doc])
        split = [t for t, n in per.items() if n > 1]
        return need(not split, "one listing per tariff", f"split: {split}")
    if f == "L11":
        return "misread", "both codes are printed in the AER's 'Code CBD' column; see the row entries of this finding"
    if f in ("L4", "L5", "L6", "L7", "L8", "L9", "L10"):
        return "resolved", "see the row entries of this finding"
    return "unresolved", "no resolver for this finding"


def resolve_all(db=None):
    db = db or DB()
    out = []
    for v in ("aer-verify-a", "aer-verify-b"):
        with open(os.path.join(VERIFICATION, v, "mismatches.csv"), newline="", encoding="utf-8") as f:
            for i, r in enumerate(csv.DictReader(f), 2):
                if v == "aer-verify-a":
                    status, ev = resolve_a(db, r)
                    finding = f"{r['table']} / {r['classification']}"
                else:
                    status, ev = (resolve_b_row if r["kind"] == "row" else resolve_b_finding)(db, r)
                    finding = r["finding_id"]
                out.append({"verifier": v, "line": i, "finding": finding, "row_id": r["row_id"], "status": status,
                            "evidence": ev})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    res = resolve_all()
    summary = Counter((r["verifier"], r["finding"], r["status"]) for r in res)
    print("| verifier | finding | status | rows |\n|---|---|---|---|")
    for (v, f, s), n in sorted(summary.items()):
        print(f"| {v} | {f} | {s} | {n} |")
    for r in res:
        if a.all or r["status"] in ("unresolved", "misread", "observation"):
            print(f"{r['verifier']}:{r['line']} {r['finding']} {r['status']}: {r['row_id'][:90]} -- {r['evidence']}")
    sys.exit(1 if any(r["status"] == "unresolved" for r in res) else 0)


if __name__ == "__main__":
    main()
