"""Effective tariff rates: the AER's rates are provisional, the distributor's own published rates are final.

The flow, per distributor and financial year (tables rate_history and effective_rate, built with the database):
  1. The AER publishes first. Its consolidated stakeholder report v1 (proposed prices) comes 3-10 weeks before the
     distributors publish; later versions carry approved prices. Every AER rate is a provisional rate, and an approved
     or later version outranks an earlier one (rate_history.precedence). Where no AER-authored file carries the
     distributor-year (2023-24), the distributor document the AER hosts is the AER side, as in scripts/reconcile.py.
  2. The distributor publishes its price list. Every AER-side rate is validated against the distributor's rate for
     the same component, after published rounding and the documented adjustments (metering adder, ACT LFiT adder:
     price_adjustment_tariff), and the result is recorded on the AER row (validation_status, delta_std).
  3. The distributor's rate replaces the AER rate as final.
Wait rule (WAIT_FOR_APPROVED): for Jemena and Power and Water the AER's not-yet-approved prices are withheld, never
used as provisional rates; their components wait for an approved AER version or the distributor's list.

effective_rate holds the current answer per component; rate_history keeps every rate with what replaced it.

  .venv/bin/python scripts/tariffdb/rates.py rate --distributor jemena --tariff PRTOU [--date D]
  .venv/bin/python scripts/tariffdb/rates.py history COMPONENT_ID
  .venv/bin/python scripts/tariffdb/rates.py changes [--distributor ID] [--year FY] [--detail]
  .venv/bin/python scripts/tariffdb/rates.py report               rewrite docs/effective_rates.md

tests/test_rates.py fails when docs/effective_rates.md is not what `report` writes.
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)
import build_support as bs  # noqa: E402
from reconcile import match_components, rounding_tolerance  # noqa: E402
from schema import REPEATED_PRINTING  # noqa: E402
from spec import VALIDATIONS  # noqa: E402

ROOT = bs.ROOT
TABLES_DIR = os.path.join(ROOT, "data", "tariffdb", "tables")
REPORT_PATH = os.path.join(ROOT, "docs", "effective_rates.md")

# Distributors whose not-yet-approved AER prices are withheld (rate_history.role = withheld) until an AER version
# approves them or the distributor publishes. In 2025-26 the AER's v1 (proposed) prices of both changed in the approved
# version (exception aer_version_differs: Jemena's v1 carried its reopener application, and Power and Water resubmitted
# "including updated prices"); every other distributor's v1 is used as a provisional rate.
WAIT_FOR_APPROVED = {
    "jemena": "Jemena's AER v1 prices carried its reopener application; the approved 2025-26 prices were lower on 90 "
              "of 95 components (median -5.3%, up to -12.4%)",
    "powerwater": "Power and Water resubmitted 'including updated prices' after AER v1; all 22 approved 2025-26 "
                  "components differ from v1 (up to 34%)",
}
FINAL = 100
PRECEDENCE = {"approved": 60, "unverified": 40, "proposed": 20}
BASIS_ORDER = ("NUoS", "unknown", "DUoS")
EXCL_METERING = re.compile(r"exclud\w*\s+metering|excl\.?\s+metering|without\s+metering", re.I)


def blank(x):
    return x is None or x == ""


def num_text(x):
    """Canonical text for a computed number (15 significant digits, no binary-float noise)."""
    if x is None:
        return None
    d = Decimal(repr(float(x)))
    if not d:
        return "0"
    return format(d.quantize(Decimal(1).scaleb(d.adjusted() - 14)).normalize(), "f")


def dec_text(d):
    """A Decimal difference as plain text without trailing zeros ('-0.001', '2233')."""
    return format(d.normalize(), "f") if d else "0"


def slug(s):
    return bs.slug(s or "") or "-"


def view(c):
    """A charge row in the shape scripts/reconcile.py compares (component matching, rounding tolerance)."""
    return {"component": c["component_label"], "unit": c["unit_interpreted"] or c["unit_published"] or "",
            "value": c["value_published"], "value_std_f": float(c["value_std"]), "unit_std": c["unit_std"],
            "charge_type": c["charge_type"], "time_band": c["time_band"] or "", "season": c["season"] or "",
            "_charge": c}


def tolerance(c):
    try:
        return rounding_tolerance(view(c))
    except Exception:  # a unit to_std cannot read: fall back to the published-to-standard ratio
        v, s = float(c["value_num"]), float(c["value_std"])
        exp = -Decimal(str(c["value_published"]).strip()).as_tuple().exponent
        return 0.5 * 10 ** (-max(0, exp)) * (abs(s / v) if v else 1.0) + 1e-9


# ---------------------------------------------------------------------------------------------------------- build
class Inputs:
    """The tables the flow reads, from the builder's memory or the committed CSVs (NULL = None or '')."""

    def __init__(self, get):
        self.docs = {d["document_id"]: d for d in get("source_document")}
        self.coverage = {(c["document_id"], c["distributor_id"]): c["price_status"] for c in get("document_coverage")}
        self.tariffs = {t["tariff_id"]: t for t in get("tariff")}
        self.listings = {l["listing_id"]: l for l in get("tariff_listing")}
        self.charges = get("charge")
        self.flags = defaultdict(set)
        for f in get("listing_flag"):
            self.flags[f["listing_id"]].add(f["flag"])
        self.adjustments = {(a["kind"], a["distributor_id"], a["fin_year"]): a["adjustment_id"]
                            for a in get("price_adjustment")}
        self.expected = {(p["adjustment_id"], p["tariff_id"]): p["expected_delta_std"]
                         for p in get("price_adjustment_tariff")}


def candidate_charges(inp):
    """{(document_id, distributor_id): [charge]} of every document that prices a distributor-year: GST-exclusive,
    priced listings, one price basis per document (NUoS, else unknown, else DUoS), repeated printings left out, and of
    a component printed both with and without metering, the excluding-metering copy (as the reconciliation compares)."""
    by_doc = defaultdict(list)
    for c in inp.charges:
        l = inp.listings[c["listing_id"]]
        if l["price_availability"] != "priced" or c["gst"] != "excl" or blank(c["value_std"]) or blank(c["unit_std"]) \
                or REPEATED_PRINTING in (c["note"] or ""):
            continue
        did = inp.tariffs[l["tariff_id"]]["distributor_id"]
        by_doc[(l["document_id"], did)].append(c)
    out = {}
    for key, cs in by_doc.items():
        bases = Counter(c["price_basis"] for c in cs)
        basis = next((b for b in BASIS_ORDER if bases[b]), bases.most_common(1)[0][0])
        cs = [c for c in cs if c["price_basis"] == basis]
        groups = defaultdict(list)
        for c in cs:
            groups[(c["listing_id"], c["component_label"], c["time_band"], c["season"], c["unit_std"])].append(c)
        keep = []
        for g in groups.values():
            excl = [c for c in g if EXCL_METERING.search(c["note"] or "")]
            keep += excl if excl and len(excl) < len(g) else g
        out[key] = sorted(keep, key=lambda c: c["charge_id"])
    return out


def known_from(d):
    if not blank(d["publication_date"]):
        return d["publication_date"], d["publication_date_basis"]
    if not blank(d["retrieved_on"]):
        return d["retrieved_on"], f"retrieved_on:{d['retrieved_on_basis']}"
    return None, None


def component_ids(inp, charges, prefix=""):
    """{charge_id: component_id} for one document's charges: tariff, year, label, band, season, unit (and region),
    numbered when one document prints the same component more than once."""
    out, seen = {}, Counter()
    for c in charges:
        l = inp.listings[c["listing_id"]]
        fy = inp.docs[l["document_id"]]["fin_year"]
        base = "|".join([l["tariff_id"], fy, prefix + slug(c["component_label"]), c["time_band"] or "-",
                         c["season"] or "-", c["unit_std"]] + ([slug(l["region"])] if not blank(l["region"]) else []))
        seen[base] += 1
        out[c["charge_id"]] = base if seen[base] == 1 else f"{base}#{seen[base]}"
    return out


def pick_final(inp, docs, cands):
    """The distributor's own published list among several priced ones: an LFiT-inclusive schedule over the one
    excluding LFiT (the rate customers are billed), then the latest publication date, then the most prices."""
    def excludes_lfit(key):
        return any("excludes_lfit" in inp.flags[c["listing_id"]] for c in cands[key])
    return max(docs, key=lambda key: (not excludes_lfit(key), inp.docs[key[0]]["publication_date"] or "",
                                      len(cands[key]), key[0]))


def validate(inp, r, f):
    """(validation_status, delta, expected delta, adjustment_id, note) of AER-side charge r against final charge f."""
    lr, lf = inp.listings[r["listing_id"]], inp.listings[f["listing_id"]]
    did = inp.tariffs[lf["tariff_id"]]["distributor_id"]
    fy = inp.docs[lf["document_id"]]["fin_year"]
    exact = Decimal(str(f["value_std"])) - Decimal(str(r["value_std"]))
    delta = float(exact)
    tr, tf = tolerance(r), tolerance(f)
    adj = expected = None
    note = []
    if f["unit_std"] != r["unit_std"]:
        note.append(f"units differ ({r['unit_std']} vs {f['unit_std']}); compared as published")
    if f["charge_type"] == "fixed" and f["includes_metering"] == "yes" and r["includes_metering"] != "yes":
        adj = inp.adjustments.get(("metering_adder", did, fy))
        if adj and not blank(inp.expected.get((adj, lf["tariff_id"]))):
            expected = float(inp.expected[(adj, lf["tariff_id"])])
    if f["charge_type"] == "energy" and f["includes_lfit"] == "yes" and r["includes_lfit"] == "no":
        adj = inp.adjustments.get(("lfit_adder", did, fy)) or inp.adjustments.get(("lfit_rebate", did, fy))
        if adj and not blank(inp.expected.get((adj, lf["tariff_id"]))):
            expected = float(inp.expected[(adj, lf["tariff_id"])])
    if abs(delta) < 1e-12:
        status, adj, expected = "match", None, None
    elif abs(delta) <= max(tr, tf):
        status, adj, expected = "match_within_rounding", None, None
        note.append(f"difference {dec_text(exact)} within half a unit of the published digits")
    elif expected is not None and abs(delta - expected) <= tr + tf + 1e-9:
        status = "match_after_adjustment"
        note.append(f"difference {dec_text(exact)} = {adj} {num_text(expected)} within published rounding")
    else:
        status = "mismatch"
        if adj and expected is None:
            note.append(f"the final rate includes {'metering' if adj.startswith('metering') else 'the ACT LFiT'}, but "
                        f"{adj} gives no amount for this tariff")
        elif adj:
            note.append(f"{adj} predicts {num_text(expected)}, the difference is {dec_text(exact)}")
            adj = None
        if lr["tariff_id"] != lf["tariff_id"]:
            note.append(f"compared across tariffs {lr['tariff_id']} / {lf['tariff_id']}")
    return status, dec_text(exact), num_text(expected), adj, "; ".join(note) or None


def compute(get):
    """Build rate_history and effective_rate from the database tables (`get(table)` -> rows)."""
    inp = Inputs(get)
    cands = candidate_charges(inp)
    years = defaultdict(lambda: {"aer": [], "hosted": [], "final": []})
    for (doc, did), cs in cands.items():
        d = inp.docs[doc]
        side = "aer" if d["author"] == "AER" else "hosted" if d["recon_side"] == "AER_HOSTED" else \
            "final" if d["price_status"] == "published" else None
        if side:
            years[(did, d["fin_year"])][side].append((doc, did))
    history = []
    for (did, fy), g in sorted(years.items()):
        history += distributor_year(inp, cands, did, fy, g)
    for r in history:
        r.pop("_charge")
    return {"rate_history": history, "effective_rate": effective(inp, history)}


def distributor_year(inp, cands, did, fy, g):
    # the AER side: AER-authored files, else the AER-hosted distributor document with the most structured prices
    aer_side = g["aer"] or ([max(g["hosted"], key=lambda k: (inp.docs[k[0]]["local_path"].endswith(".xlsx"),
                                                              len(cands[k]), k[0]))] if g["hosted"] else [])
    final = pick_final(inp, g["final"], cands) if g["final"] else None
    rows, cid = [], {}
    for key in aer_side:
        d = inp.docs[key[0]]
        status = inp.coverage.get(key, d["price_status"])
        withheld = did in WAIT_FOR_APPROVED and status != "approved"
        cid.update(component_ids(inp, cands[key]))
        for c in cands[key]:
            rows.append(rate_row(inp, c, cid[c["charge_id"]], d, "aer" if d["author"] == "AER" else "aer_hosted",
                                 status, "withheld" if withheld else "provisional",
                                 PRECEDENCE.get(status, 0) + int(d["version_seq"])))
    if final:
        # match the distributor's components to the AER side's, best AER version first; the rest are distributor-only
        assigned = {}
        free = defaultdict(list)
        for c in cands[final]:
            free[inp.listings[c["listing_id"]]["tariff_id"]].append(c)
        linked = set()
        for key in sorted(aer_side, key=lambda k: -max(r["precedence"] for r in rows if r["document_id"] == k[0])):
            by_tariff = defaultdict(list)
            for c in cands[key]:
                if cid[c["charge_id"]] not in linked:
                    by_tariff[inp.listings[c["listing_id"]]["tariff_id"]].append(c)
            for tid, cs in sorted(by_tariff.items()):
                if not free.get(tid):
                    continue
                pairs, _, rest = match_components([view(c) for c in cs], [view(c) for c in free[tid]])
                for a, f in pairs:
                    assigned[f["_charge"]["charge_id"]] = cid[a["_charge"]["charge_id"]]
                    linked.add(cid[a["_charge"]["charge_id"]])
                free[tid] = [v["_charge"] for v in rest]
        own = component_ids(inp, [c for c in cands[final] if c["charge_id"] not in assigned], prefix="dnsp:")
        d = inp.docs[final[0]]
        for c in cands[final]:
            rows.append(rate_row(inp, c, assigned.get(c["charge_id"]) or own[c["charge_id"]], d, "distributor",
                                 "published", "final", FINAL))
    link(inp, rows, final is not None)
    return rows


def rate_row(inp, c, component_id, d, side, status, role, precedence):
    l = inp.listings[c["listing_id"]]
    kf, basis = known_from(d)
    return {"rate_id": c["charge_id"], "component_id": component_id, "charge_id": c["charge_id"],
            "document_id": d["document_id"], "distributor_id": inp.tariffs[l["tariff_id"]]["distributor_id"],
            "fin_year": d["fin_year"], "tariff_id": l["tariff_id"], "source_side": side, "price_status": status,
            "role": role, "precedence": precedence, "known_from": kf, "known_from_basis": basis,
            "value_std": c["value_std"], "unit_std": c["unit_std"], "is_current": 0, "superseded_by": None,
            "validated_against": None, "validation_status": None, "delta_std": None, "expected_delta_std": None,
            "adjustment_id": None, "validation_note": None, "_charge": c}


def link(inp, rows, published):
    """Validation of every AER-side rate against the final rate of its component, and the supersession chain."""
    by_comp = defaultdict(list)
    for r in rows:
        by_comp[r["component_id"]].append(r)
    for comp in by_comp.values():
        comp.sort(key=lambda r: (r["precedence"], r["document_id"], r["rate_id"]))
        final = next((r for r in comp if r["role"] == "final"), None)
        aer = [r for r in comp if r["role"] != "final"]
        for r in aer:
            if final is None:
                r["validation_status"] = "aer_only" if published else "pending"
                continue
            st, delta, exp, adj, note = validate(inp, r["_charge"], final["_charge"])
            r.update(validated_against=final["rate_id"], validation_status=st, delta_std=delta, expected_delta_std=exp,
                     adjustment_id=adj, validation_note=note)
        if final:
            # the provisional rate it replaces, else the best withheld one (what the wait rule kept out)
            replaced = next((r for r in reversed(aer) if r["role"] == "provisional"), aer[-1] if aer else None)
            if replaced:
                for k in ("validation_status", "delta_std", "expected_delta_std", "adjustment_id", "validation_note"):
                    final[k] = replaced[k]
                final["validated_against"] = replaced["rate_id"]
            else:
                final["validation_status"] = "distributor_only"
        usable = [r for r in comp if r["role"] != "withheld"]
        for r in comp:
            nxt = next((u for u in usable if u["precedence"] > r["precedence"]), None)
            r["superseded_by"] = nxt["rate_id"] if nxt else None


def resolve(history, as_of=None):
    """{component_id: answer} from rate_history rows, using only the documents known by `as_of` (all when None).
    answer: status, current (the rate in effect, or None), latest (highest-precedence rate), replaced (the provisional
    rate a final one replaced), only_in, validation (of the current answer as of that date)."""
    rows = [r for r in history if as_of is None or (not blank(r["known_from"]) and r["known_from"] <= as_of)]
    by_year = defaultdict(list)
    for r in rows:
        by_year[(r["distributor_id"], r["fin_year"])].append(r)
    out = {}
    for _, yr in sorted(by_year.items()):
        published = any(r["role"] == "final" for r in yr)
        usable_docs = {}
        for r in yr:
            if r["role"] == "provisional":
                usable_docs[r["document_id"]] = max(int(r["precedence"]), usable_docs.get(r["document_id"], 0))
        best = max(usable_docs, key=lambda d: (usable_docs[d], d)) if usable_docs else None
        aer_tariffs = {r["tariff_id"] for r in yr if r["role"] != "final"}
        final_tariffs = {r["tariff_id"] for r in yr if r["role"] == "final"}
        comps = defaultdict(list)
        for r in yr:
            comps[r["component_id"]].append(r)
        for comp, cs in comps.items():
            cs.sort(key=lambda r: (int(r["precedence"]), r["document_id"], r["rate_id"]))
            final = next((r for r in cs if r["role"] == "final"), None)
            prov = [r for r in cs if r["role"] == "provisional"]
            current, only, replaced = None, None, None
            if final:
                current, status, validation = final, "final", final["validation_status"]
                replaced = prov[-1] if prov else None
                if len(cs) == 1:
                    only = "distributor_component" if final["tariff_id"] in aer_tariffs else "distributor_tariff"
            else:
                validation = "aer_only" if published else "pending"
                if best and any(r["document_id"] == best for r in prov):
                    current, status = next(r for r in prov if r["document_id"] == best), "provisional"
                    if published:
                        only = "aer_component" if current["tariff_id"] in final_tariffs else "aer_tariff"
                else:
                    status = "dropped" if best or published else "awaiting_approval"
            out[comp] = {"status": status, "current": current, "latest": cs[-1], "replaced": replaced,
                         "only_in": only, "validation": validation}
    return out


def effective(inp, history):
    out = []
    for comp, a in sorted(resolve(history).items()):
        cur = a["current"]
        row = cur or a["latest"]
        if cur:
            cur["is_current"] = 1
        c = inp_charge(inp, row)
        d = inp.docs[row["document_id"]]
        final = a["status"] == "final"
        out.append({
            "component_id": comp, "distributor_id": row["distributor_id"], "fin_year": row["fin_year"],
            "tariff_id": row["tariff_id"], "effective_from": c["effective_from"], "effective_to": c["effective_to"],
            "charge_type": c["charge_type"], "time_band": c["time_band"], "season": c["season"],
            "component_label": c["component_label"], "unit_std": c["unit_std"], "status": a["status"],
            "value_std": c["value_std"] if cur else None, "value_published": c["value_published"] if cur else None,
            "unit_published": c["unit_published"] if cur else None, "rate_id": cur["rate_id"] if cur else None,
            "document_id": d["document_id"], "version_label": d["version_label"], "source_side": row["source_side"],
            "price_status": row["price_status"],
            "replaced_rate_id": a["replaced"]["rate_id"] if a["replaced"] else None,
            "validation_status": a["validation"], "delta_std": row["delta_std"] if final else None,
            "adjustment_id": row["adjustment_id"] if final else None, "only_in": a["only_in"],
        })
    return out


def inp_charge(inp, row):
    if not hasattr(inp, "_charge_by_id"):
        inp._charge_by_id = {c["charge_id"]: c for c in inp.charges}
    return inp._charge_by_id[row["charge_id"]]


# ---------------------------------------------------------------------------------------------------------- reading
def read(table):
    with open(os.path.join(TABLES_DIR, f"{table}.csv"), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class Db:
    """The committed tables the commands read."""

    def __init__(self):
        self.history = read("rate_history")
        self.effective = read("effective_rate")
        self.docs = {d["document_id"]: d for d in read("source_document")}
        self.tariffs = {t["tariff_id"]: t for t in read("tariff")}
        self.by_rate = {r["rate_id"]: r for r in self.history}

    def source(self, doc_id):
        d = self.docs[doc_id]
        if d["author"] == "AER" and d["document_type"] == "aer_consolidated_stakeholder_report":
            return f"AER {d['version_label']}"
        return doc_id


def md_table(header, rows):
    esc = lambda x: str("" if x is None else x).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    return "\n".join(["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
                     + ["| " + " | ".join(esc(x) for x in r) + " |" for r in rows]) + "\n"


def code(db, tid):
    return db.tariffs[tid]["tariff_code"]


def year_rows(db, distributor=None, year=None):
    keep = lambda r: (distributor in (None, r["distributor_id"]) and year in (None, r["fin_year"]))  # noqa: E731
    return [r for r in db.effective if keep(r)], [r for r in db.history if keep(r)]


def status_table(db, eff, hist):
    by = defaultdict(Counter)
    final_doc, prov_doc = defaultdict(set), defaultdict(set)
    for r in eff:
        k = (r["distributor_id"], r["fin_year"])
        by[k][r["status"]] += 1
        if r["status"] == "final":
            final_doc[k].add(r["document_id"])
        elif r["status"] == "provisional":
            prov_doc[k].add(db.source(r["document_id"]))
    rows = [[d, fy, n["final"], n["provisional"], n["awaiting_approval"], n["dropped"],
             ", ".join(sorted(final_doc[(d, fy)])) or "not published / not held",
             ", ".join(sorted(prov_doc[(d, fy)])) or "-"] for (d, fy), n in sorted(by.items())]
    return md_table(["Distributor", "Year", "Final", "Provisional", "Awaiting approval", "Dropped",
                     "Final rates from", "Provisional rates from"], rows)


def changes_table(db, eff, hist):
    by = defaultdict(Counter)
    replaced = defaultdict(set)
    for r in eff:
        k = (r["distributor_id"], r["fin_year"])
        if r["status"] == "final":
            by[k][r["validation_status"]] += 1
            h = db.by_rate[r["rate_id"]]
            if h["validated_against"]:
                v = db.by_rate[h["validated_against"]]
                replaced[k].add(f"{db.source(v['document_id'])} ({v['price_status']}"
                                + (", withheld)" if v["role"] == "withheld" else ")"))
        if r["only_in"]:
            by[k][r["only_in"]] += 1
    rows = []
    for (d, fy), n in sorted(by.items()):
        if not any(n[s] for s in VALIDATIONS):
            continue
        final = sum(n[s] for s in VALIDATIONS)
        rows.append([d, fy, ", ".join(sorted(replaced[(d, fy)])) or "no AER-side rate", final, n["match"],
                     n["match_within_rounding"], n["match_after_adjustment"], n["mismatch"],
                     f"{n['distributor_tariff']} / {n['distributor_component']}",
                     f"{n['aer_tariff']} / {n['aer_component']}"])
    return md_table(["Distributor", "Year", "Provisional rates replaced (AER version)", "Final rates", "Same",
                     "Within rounding", "After metering/LFiT adjustment", "Changed", "Distributor only (tariffs / "
                     "components)", "AER only, kept provisional (tariffs / components)"], rows)


def versions_table(db, eff, hist):
    by = defaultdict(Counter)
    for r in hist:
        if r["role"] != "final":
            by[(r["distributor_id"], r["fin_year"], r["document_id"], r["price_status"], r["role"])][
                r["validation_status"]] += 1
    rows = []
    for (d, fy, doc, st, role), n in sorted(by.items()):
        rows.append([d, fy, db.source(doc), st, role, sum(n.values()), n["match"], n["match_within_rounding"],
                     n["match_after_adjustment"], n["mismatch"], n["aer_only"], n["pending"]])
    return md_table(["Distributor", "Year", "AER-side document", "Price status", "Role", "Rates", "Same",
                     "Within rounding", "After adjustment", "Changed", "Not in distributor list",
                     "Awaiting distributor"], rows)


def mismatch_table(db, eff, hist):
    rows = []
    for r in eff:
        if r["status"] != "final" or r["validation_status"] != "mismatch":
            continue
        h = db.by_rate[r["rate_id"]]
        v = db.by_rate[h["validated_against"]]
        pct = f"{float(h['delta_std']) / float(v['value_std']) * 100:+.1f}%" if float(v["value_std"]) else ""
        rows.append([r["distributor_id"], r["fin_year"], code(db, r["tariff_id"]), r["component_label"],
                     db.source(v["document_id"]), v["value_std"], r["value_std"], h["delta_std"], pct, r["unit_std"],
                     h["validation_note"] or ""])
    return md_table(["Distributor", "Year", "Tariff", "Component", "AER-side source", "AER-side value", "Final value",
                     "Difference", "%", "Unit", "Note"], rows)


def one_source_table(db, eff, hist):
    rows = [[r["distributor_id"], r["fin_year"], code(db, r["tariff_id"]), r["component_label"], r["only_in"],
             r["status"], r["value_std"], r["unit_std"], db.source(r["document_id"])]
            for r in eff if r["only_in"]]
    return md_table(["Distributor", "Year", "Tariff", "Component", "Only in", "Status", "Value", "Unit", "Source"],
                    rows)


def report(db):
    eff, hist = year_rows(db)
    n = Counter(r["status"] for r in eff)
    v = Counter(r["validation_status"] for r in eff if r["status"] == "final")
    wait = "\n".join(f"  - {d}: {why}" for d, why in sorted(WAIT_FOR_APPROVED.items()))
    totals = md_table(["Measure", "Count"], [
        ["Components", len(eff)], ["Final (distributor's published rate)", n["final"]],
        ["Provisional (AER-side rate)", n["provisional"]], ["Awaiting approval (wait rule)", n["awaiting_approval"]],
        ["Dropped (only an earlier AER version prints it)", n["dropped"]],
        ["Final, same as the AER-side rate it replaced", v["match"]],
        ["Final, within rounding", v["match_within_rounding"]],
        ["Final, after the metering/LFiT adjustment", v["match_after_adjustment"]],
        ["Final, changed", v["mismatch"]], ["Final, no AER-side rate", v["distributor_only"]]])
    return f"""# Effective rates: AER provisional, distributor final

Generated by `scripts/tariffdb/rates.py report` from `data/tariffdb` (tables `rate_history` and `effective_rate`); do
not edit by hand. Query one tariff with `scripts/tariffdb/rates.py rate`, or one component's history with
`scripts/tariffdb/rates.py history`.

## The rule

- **Provisional:** every AER rate, as soon as the AER publishes it. An approved version outranks an unverified one,
  which outranks a proposed one; a later version outranks an earlier one with the same status.
- **AER side in 2023-24:** the AER published no price file that year. The distributor document the AER hosts stands
  in for it, as in the reconciliation.
- **Validation:** when the distributor publishes, every AER-side rate is compared with the distributor's rate for the
  same component (same matching as `scripts/reconcile.py`). It counts as the same when it is equal, within half a
  unit of either published digit, or apart by exactly the documented adjustment (metering adder, ACT LFiT adder;
  `price_adjustment_tariff`). Otherwise it is recorded as changed, with the difference.
- **Final:** the distributor's own published price list replaces the AER rate. Only a list from the distributor's
  own site with status `published` counts; a proposal, or an AER-hosted copy, does not.
- **Wait rule:** the AER's not-yet-approved prices are withheld, never used, for:
{wait}
- **One source only:** a component only one side prints is kept and flagged (`only_in`). An AER-only rate stays
  provisional.
- **No point-in-time (as-of) lookup:** most distributor publication dates are not held; `known_from` is often only
  the date a document was retrieved, so an answer as of an earlier date would be wrong.

## Totals

{totals}
## Status by distributor and year

{status_table(db, eff, hist)}
## What changed when the distributor published

Counts of final rates by how they compare with the AER-side rate they replaced.

{changes_table(db, eff, hist)}
## Every AER-side version against the final list

One row per AER-side document. A `withheld` row is validated too, which shows what the wait rule avoided.

{versions_table(db, eff, hist)}
## Changed rates

Final rates that differ from the AER-side rate they replaced by more than rounding and the documented adjustments.

{mismatch_table(db, eff, hist)}"""


def cmd_rate(db, a):
    tid = a.tariff if ":" in (a.tariff or "") else f"{a.distributor}:{(a.tariff or '').upper()}" if a.tariff else None
    rows = [r for r in db.effective if r["distributor_id"] == a.distributor
            and (tid is None or r["tariff_id"] == tid)
            and (a.date is None or r["effective_from"] <= a.date <= r["effective_to"])]
    if not rows:
        raise SystemExit("no rates match")
    print(md_table(["Component", "Status", "Value", "Unit", "Source", "Version", "Price status", "Validation",
                    "Only in"],
                   [[r["component_id"], r["status"], r["value_std"], r["unit_std"], r["document_id"],
                     r["version_label"], r["price_status"], r["validation_status"], r["only_in"]] for r in rows]))


def cmd_history(db, a):
    rows = sorted((r for r in db.history if r["component_id"] == a.component),
                  key=lambda r: (int(r["precedence"]), r["rate_id"]))
    if not rows:
        raise SystemExit(f"no component {a.component!r}")
    print(md_table(["Rate", "Document", "Role", "Price status", "Known from", "Value", "Unit", "Current",
                    "Superseded by", "Validation", "Difference", "Adjustment"],
                   [[r["rate_id"], r["document_id"], r["role"], r["price_status"],
                     f"{r['known_from']} ({r['known_from_basis']})", r["value_std"], r["unit_std"], r["is_current"],
                     r["superseded_by"], r["validation_status"], r["delta_std"], r["adjustment_id"]] for r in rows]))


def cmd_changes(db, a):
    eff, hist = year_rows(db, a.distributor, a.year)
    if not eff:
        raise SystemExit("no rates match")
    print(changes_table(db, eff, hist))
    print(versions_table(db, eff, hist))
    if a.detail:
        print(mismatch_table(db, eff, hist))
        print(one_source_table(db, eff, hist))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("rate", help="effective rates of a distributor's tariffs")
    p.add_argument("--distributor", required=True, help="distributor_id, e.g. jemena")
    p.add_argument("--tariff", help="tariff code (or tariff_id)")
    p.add_argument("--date", help="only rates in effect on this date (YYYY-MM-DD)")
    p = sub.add_parser("history", help="every rate of one component, in order")
    p.add_argument("component")
    p = sub.add_parser("changes", help="what changed when the distributor rates arrived")
    p.add_argument("--distributor")
    p.add_argument("--year")
    p.add_argument("--detail", action="store_true", help="also list changed rates and one-source components")
    sub.add_parser("report", help=f"rewrite {os.path.relpath(REPORT_PATH, ROOT)}")
    a = ap.parse_args(argv)
    if getattr(a, "date", None):
        try:
            date.fromisoformat(a.date)
        except ValueError:
            ap.error(f"--date {a.date!r} is not a YYYY-MM-DD date")
    db = Db()
    if a.cmd == "report":
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write(report(db))
        print(f"wrote {os.path.relpath(REPORT_PATH, ROOT)}")
    else:
        {"rate": cmd_rate, "history": cmd_history, "changes": cmd_changes}[a.cmd](db, a)


if __name__ == "__main__":
    main()
