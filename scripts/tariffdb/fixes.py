#!/usr/bin/env python3
"""Write data/tariffdb/transcription_fixes.csv: every change to a committed source-fact row since REF, each tagged
with the verification finding (data/verification/) that it fixes.

The append-only check (build.py --check-append-only) accepts exactly the changes listed there. This script lists a
change only when a rule below explains it, and exits 1 naming every change no rule explains; so a rebuild that alters
committed facts for any other reason cannot slip through. Entries for older refs already in the file are kept.

usage: fixes.py REF        (run after scripts/tariffdb/build.py)
"""
import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import build  # noqa: E402
import spec  # noqa: E402


def rows_of(text):
    header, rows = build.read_table_text(text)
    return [dict(zip(header, r)) for r in rows]


class Context:
    """The tables at REF and now, for the rules' lookups."""
    def __init__(self, ref):
        self.ref = ref
        self.old, self.new = {}, {}
        for name in ("source_document", "tariff", "tariff_listing", "charge", "tariff_demand_rule"):
            self.old[name] = {self.key(name, r): r for r in rows_of(self.git_table(name))}
            with open(os.path.join(build.ROOT, "data", "tariffdb", "tables", f"{name}.csv"), newline="",
                      encoding="utf-8") as f:
                self.new[name] = {self.key(name, r): r for r in rows_of(f.read())}
        self.doc = {**{k[0]: r for k, r in self.old["source_document"].items()},
                    **{k[0]: r for k, r in self.new["source_document"].items()}}
        self.listing = {**{k[0]: r for k, r in self.old["tariff_listing"].items()},
                        **{k[0]: r for k, r in self.new["tariff_listing"].items()}}

    def git_table(self, name):
        out = subprocess.run(["git", "show", f"{self.ref}:data/tariffdb/tables/{name}.csv"], cwd=build.ROOT,
                             capture_output=True, text=True)
        if out.returncode:
            raise SystemExit(f"cannot read {name}.csv at {self.ref}: {out.stderr.strip()}")
        return out.stdout

    @staticmethod
    def pk(name):
        return [c["name"] for c in spec.BY_NAME[name]["columns"] if c["primary_key"]]

    def key(self, name, row):
        return tuple(row[c] for c in self.pk(name))

    def added(self, name):
        return [r for k, r in self.new[name].items() if k not in self.old[name]]

    def tariff_of_charge(self, row):
        return self.listing[row["listing_id"]]["tariff_id"]

    def replacement(self, name, old):
        """The added row that takes the place of a removed one, or None."""
        if name == "tariff":
            moved = {self.new["tariff_listing"][k]["tariff_id"] for k, l in self.old["tariff_listing"].items()
                     if l["tariff_id"] == old["tariff_id"] and k in self.new["tariff_listing"]}
            if old["tariff_id"] == "sapn:-":
                moved = {"sapn:ZSN228"}
            return self.new["tariff"].get((moved.pop(),)) if len(moved) == 1 else None
        if name == "tariff_listing":
            tid = "sapn:ZSN228" if old["tariff_id"] == "sapn:-" else old["tariff_id"]
            hits = [r for r in self.added(name) if r["document_id"] == old["document_id"] and r["tariff_id"] == tid]
            return hits[0] if len(hits) == 1 else None
        if name == "charge":
            doc = self.listing[old["listing_id"]]["document_id"]
            hits = [r for r in self.added(name) if self.listing[r["listing_id"]]["document_id"] == doc
                    and all(r[c] == old[c] for c in ("locator", "price_basis", "gst", "value_published"))]
            tid = "sapn:ZSN228" if self.tariff_of_charge(old) == "sapn:-" else self.tariff_of_charge(old)
            hits = [r for r in hits if self.tariff_of_charge(r) == tid]
            same = [r for r in hits if r["component_label"] == old["component_label"]] or hits
            return same[0] if len(same) == 1 else None
        if name == "tariff_demand_rule":
            season = SEASON_VOCABULARY.get(old["season"])
            hits = [r for r in self.added(name) if r["tariff_id"] == old["tariff_id"]
                    and r["demand_rule_id"] == old["demand_rule_id"] and r["season"] == season]
            return hits[0] if len(hits) == 1 else None
        return None


SEASON_VOCABULARY = {"non-summer": "non_summer", "On Season": "high", "Off Season": "low"}

# A rule: (finding, why, applies(ctx, table, column, old_row, new_row)); column None means the row was removed and
# new_row is its replacement. The first rule that applies tags the change; a change no rule explains is refused.
RULES = []


def rule(finding, why):
    def wrap(fn):
        RULES.append((finding, why, fn))
        return fn
    return wrap


def doc_of(ctx, table, row):
    if table == "charge":
        return ctx.listing[row["listing_id"]]["document_id"]
    return row.get("document_id")


def did_of(ctx, table, row):
    tid = {"charge": lambda: ctx.tariff_of_charge(row)}.get(table, lambda: row.get("tariff_id", ""))()
    return tid.split(":")[0]


@rule("verifier-a: AER 2024-25 status", "the AER 2024-25 stakeholder reports head their price tables 'Proposed "
      "prices' and state no approval, so the status is not asserted (exception price_status_unverified)")
def _(ctx, t, c, o, n):
    return t == "source_document" and c == "price_status" and o["document_type"] == "aer_stakeholder_report" \
        and (o[c], n[c]) == ("approved", "unverified")


@rule("verifier-b: M1", "AER 2025-26 v1 rows print no code: the code of the same AER tariff ID in the approved "
      "version identifies the tariff, so the AER-ID tariff has no listing left")
def _(ctx, t, c, o, n):
    if t == "tariff" and c is None:
        return o["identity_basis"] == "aer_tariff_id" and n is not None
    return t == "tariff_listing" and c == "tariff_id" and o["document_id"] == "aer-consolidated-2025-26-v1" \
        and o[c].split(":")[1].startswith("TD-") and not n[c].split(":")[1].startswith("TD-")


@rule("verifier-b: M2", "the AER row whose 'Code SA' is '-' is tariff ZSN228, which its 'Code CBD' cell names")
def _(ctx, t, c, o, n):
    if c is not None or n is None:
        return False
    if t == "tariff":
        return o["tariff_id"] == "sapn:-" and n["tariff_id"] == "sapn:ZSN228"
    if t == "tariff_listing":
        return o["tariff_id"] == "sapn:-" and n["tariff_id"] == "sapn:ZSN228"
    return t == "charge" and ctx.tariff_of_charge(o) == "sapn:-" and ctx.tariff_of_charge(n) == "sapn:ZSN228"


@rule("verifier-b: H1", "AusNet prints NASN2S/NASN2P with prices in every distributor document; the codes are "
      "distributor codes and their rules-only listings become the priced listings")
def _(ctx, t, c, o, n):
    if t == "tariff" and c == "identity_basis":
        return o["tariff_id"] in ("ausnet:NASN2S", "ausnet:NASN2P") and n[c] == "distributor_code"
    return t == "tariff_listing" and c is None and o["tariff_id"] in ("ausnet:NASN2S", "ausnet:NASN2P") \
        and o["price_availability"] == "rules_only" and n is not None and n["price_availability"] == "priced"


@rule("verifier-b: L9", "the 2026-27 Tariff Summary prints CFTUOS/PFTUOS/UFTUOS (rules-only listings), so they are "
      "distributor codes")
def _(ctx, t, c, o, n):
    return t == "tariff" and c == "identity_basis" and o["tariff_id"].split(":")[1] in ("CFTUOS", "PFTUOS", "UFTUOS") \
        and n[c] == "distributor_code"


@rule("verifier-b: L8", "' (tariff trial)' is not printed in the tariff name")
def _(ctx, t, c, o, n):
    return t == "tariff_listing" and c == "name_published" and o[c] == n[c] + " (tariff trial)"


@rule("verifier-b: L7", "the Jemena 2025-26 schedule never prints the F-codes of these rules-only listings")
def _(ctx, t, c, o, n):
    return t == "tariff_listing" and c == "code_published" and o["document_id"] == "jemena-network-tariff-schedule-2025-26" \
        and o["price_availability"] == "rules_only" and n[c] == ""


@rule("verifier-b: L3", "the last row of the SAPN page is now read, so the tariff's first printing is an earlier page")
def _(ctx, t, c, o, n):
    return t == "tariff_listing" and c == "locator" and o["tariff_id"].startswith("sapn:") \
        and o["document_id"].startswith("sapn-") and int(n[c].split("p")[-1]) < int(o[c].split("p")[-1])


@rule("verifier-b: L16", "TasNetworks 2023-24 prints these tariffs in two tables of one document: one listing, "
      "keeping the first table's class (the other table's class is in the charge note)")
def _(ctx, t, c, o, n):
    doc = "tasnetworks-network-tariff-pricing-schedule-scs-2023-24"
    if c is None:
        return n is not None and doc_of(ctx, t, o) == doc and t in ("tariff_listing", "charge")
    return t == "charge" and c == "note" and doc_of(ctx, t, o) == doc and n[c].startswith(o[c]) \
        and "; this table prints the tariff under" in n[c][len(o[c]):]


@rule("verifier-a: Essential unpriced codes", "Essential prints these codes with '-' in every price cell: they "
      "are placeholder listings of the price list (were rules-only)")
def _(ctx, t, c, o, n):
    return t == "tariff_listing" and c is None and o["tariff_id"].startswith("essential:") \
        and o["price_availability"] == "rules_only" and n is not None and n["price_availability"] == "placeholder"


@rule("verifier-a: Ausgrid capacity", "the column sits under the 'Capacity charge' / 'Network Capacity Prices' "
      "header (the AER labels it 'Real Capacity'): a capacity charge, values unchanged")
def _(ctx, t, c, o, n):
    if t != "charge" or did_of(ctx, t, o) != "ausgrid":
        return False
    if c is None:
        return n is not None and o["charge_type"] == "demand" and n["charge_type"] == "capacity" \
            and o["value_published"] == n["value_published"]
    return c == "charge_type" and (o[c], n[c]) == ("demand", "capacity")


@rule("verifier-a: Energex Band labels", "the header prints 'Band1 Charge' ... 'Band5 Charge' without a space")
def _(ctx, t, c, o, n):
    return t == "charge" and c == "component_label" and did_of(ctx, t, o) == "energex" \
        and re.sub(r"Band (\d)", r"Band\1", o[c]) == n[c]


@rule("verifier-a: Evoenergy OCR unit case", "the page prints 'c/kVA/day'; the OCR read 'c/KVA/day'")
def _(ctx, t, c, o, n):
    return t == "charge" and did_of(ctx, t, o) == "evoenergy" and (
        (c in ("unit_published", "unit_interpreted") and (o[c], n[c]) == ("c/KVA/day", "c/kVA/day"))
        or (c == "note" and n[c].replace("; unit letter case as printed (OCR text: 'c/KVA/day')", "") == o[c]))


@rule("verifier-a: Evoenergy 123/124 units", "'c/KkA/day' prints the period 'day'; only the quantity is repaired, "
      "so the period is not inferred (the unit itself is a source_ambiguous instance)")
def _(ctx, t, c, o, n):
    return t == "charge" and c == "period_inferred" and did_of(ctx, t, o) == "evoenergy" \
        and o["unit_published"] == "c/KkA/day" and (o[c], n[c]) == ("1", "0")


def same(o, n, *cols):
    return all(o[c] == n[c] for c in cols)


def interprets_note(n):
    return f"Source parser interprets {n['unit_published'] or ''!r} as {n['unit_interpreted']!r}; see parser note " \
           f"and original unit."


PWC_ASSUMED_UNITS = ("$/kWh", "$/kVA/month", "$/kVA", "$/NMI/day")  # sapn_pwc.pwc_default_unit and its period note


@rule("verifier-a: Power and Water unprinted units", "these columns print no unit: unit_published is NULL and the "
      "unit used is the parser's reading (period inferred; a source_ambiguous instance quotes the header)")
def _(ctx, t, c, o, n):
    if t != "charge" or n is None or did_of(ctx, t, o) != "powerwater" or n["unit_published"] != "" \
            or not same(o, n, "unit_interpreted", "unit_std", "value_std"):
        return False
    return (c == "unit_published" and o[c] in PWC_ASSUMED_UNITS) \
        or (c == "period_inferred" and (o[c], n[c]) == ("0", "1")) \
        or (c == "normalisation_note" and not o[c] and n[c] == interprets_note(n))


@rule("verifier-b: L4", "'Mth Dmnd Shld' is the monthly shoulder demand ('BD Shoulder' in SAPN's documents)")
def _(ctx, t, c, o, n):
    return t == "charge" and c == "time_band" and did_of(ctx, t, o) == "sapn" and "Shld" in o["component_label"] \
        and (o[c], n[c]) == ("", "shoulder")


M4_NOTE = ("billing period per SA Power Networks' own price lists ('$/kVA/day'); 'Ann'/'Mth' in the AER label is the "
           "demand measurement window")


@rule("verifier-b: M4", "SAPN prices demand per day ('$/kVA/day' in its own price lists); 'Ann'/'Mth' in the AER "
      "label is the demand measurement window, not the billing period (period inferred)")
def _(ctx, t, c, o, n):
    if t != "charge" or n is None or did_of(ctx, t, o) != "sapn" or not doc_of(ctx, t, o).startswith("aer-") \
            or not same(o, n, "unit_published", "value_published", "value_std"):
        return False
    return (c == "unit_std" and n[c] == re.sub(r"/(year|month)\?$", "/day?", o[c]) != o[c]) \
        or (c == "unit_interpreted" and n[c] == re.sub(r"^\$(?:dollars)?/(kVA|kW)$", r"$/\1/day?", o[c]) != o[c]) \
        or (c == "period" and (o[c], n[c]) in (("year", "day"), ("month", "day"))) \
        or (c == "period_inferred" and (o[c], n[c]) == ("0", "1")) \
        or (c == "normalisation_note" and not o[c] and n[c] == interprets_note(n)) \
        or (c == "note" and n[c] == "; ".join(x for x in (o[c], M4_NOTE) if x))


@rule("verifier-b: L5", "the AER unit prints a season ('cents/kVA/Summer', '.../highsn'), not a period: the period "
      "'day' is inferred")
def _(ctx, t, c, o, n):
    return t == "charge" and doc_of(ctx, t, o).startswith("aer-") and re.search(r"/(Summer|highsn|lowsn)$",
                                                                               o["unit_published"]) and (
        (c == "unit_std" and n[c] == o[c] + "?") or (c == "period_inferred" and (o[c], n[c]) == ("0", "1")))


# (as stored before, as the TasNetworks header prints it)
L6_UNITS = {("c/kVA or kW/day", "(c/kVA/day) (c/kW/day)"), ("c/kW/day", "c/kVA/day"),
            ("c/kW/day", "c/kVA, kW, lamp watt/day"), ("c/kVA/day", "c/kVA, kW, lamp watt/day")}


@rule("verifier-b: L6", "the TasNetworks header prints the unit stored in unit_published; the reading used for "
      "conversion is unit_interpreted, with a note")
def _(ctx, t, c, o, n):
    # only the printed unit and its notes change: the unit and value used for conversion stay as they were
    if t != "charge" or n is None or did_of(ctx, t, o) != "tasnetworks" or not same(o, n, "unit_std", "value_std"):
        return False
    return (c == "unit_published" and (o[c], n[c]) in L6_UNITS) \
        or (c == "unit_interpreted" and o[c] == o["unit_published"] and n[c] == n["unit_published"]
            and (o[c], n[c]) in L6_UNITS) \
        or (c == "normalisation_note" and not o[c] and n[c] == interprets_note(n)) \
        or (c == "note" and n[c].startswith(o[c]) and "unit printed as" in n[c][len(o[c]):])


@rule("verifier-a: demand-rule season vocabulary", "tariff_demand_rule.season uses the charge season vocabulary "
      "(non_summer; Power and Water On/Off Season = high/low), so the season join works")
def _(ctx, t, c, o, n):
    return t == "tariff_demand_rule" and c is None and n is not None and SEASON_VOCABULARY.get(o["season"]) == n["season"]


def explain(ctx, table, column, old, new):
    for finding, why, fn in RULES:
        if fn(ctx, table, column, old, new):
            return finding, why
    return None


def diff(ref):
    ctx = Context(ref)
    out, unexplained = [], []
    for name in spec.TABLE_ORDER:
        t = spec.BY_NAME[name]
        if t.get("derived"):
            continue
        rp = f"data/tariffdb/tables/{name}.csv"
        old_text = subprocess.run(["git", "show", f"{ref}:{rp}"], cwd=build.ROOT, capture_output=True, text=True)
        if old_text.returncode:
            continue
        with open(os.path.join(build.ROOT, rp), newline="", encoding="utf-8") as f:
            new_rows = rows_of(f.read())
        pk = ctx.pk(name)
        derived = {c["name"] for c in t["columns"] if c.get("derived")}
        new = {tuple(r[c] for c in pk): r for r in new_rows}
        for o in rows_of(old_text.stdout):
            k = tuple(o[c] for c in pk)
            n = new.get(k)
            if n is None:
                rep = ctx.replacement(name, o)
                why = explain(ctx, name, None, o, rep)
                entry = (name, k, None, "", json.dumps([rep[c] for c in pk]) if rep else "")
                (out.append(entry + why) if why else unexplained.append(entry))
                continue
            for c, v in o.items():
                if c in derived or n.get(c, "") == v:
                    continue
                why = explain(ctx, name, c, o, n)
                entry = (name, k, c, v, n.get(c, ""))
                (out.append(entry + why) if why else unexplained.append(entry))
    return out, unexplained


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ref")
    a = ap.parse_args()
    out, unexplained = diff(a.ref)
    for name, k, c, v, nv in unexplained:
        print(f"UNEXPLAINED {name} {k} {c or 'removed'}: {v!r} -> {nv!r}")
    if unexplained:
        print(f"{len(unexplained)} changes match no rule; nothing written")
        sys.exit(1)
    keep = []
    if os.path.exists(build.FIXES_PATH):
        listed = {(name, k, c) for name, k, c, *_ in out}
        ctx, at_ref = Context(a.ref), {}
        with open(build.FIXES_PATH, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                name, k, c = r["table"], tuple(json.loads(r["key"])), r["column"] or None
                if (name, k, c) in listed:
                    continue
                # an older correction stays listed only while REF already has it; one made since REF and then undone
                # (the row is back to its REF value) is no longer a change
                if name not in at_ref:
                    at_ref[name] = {ctx.key(name, x): x for x in rows_of(ctx.git_table(name))}
                row = at_ref[name].get(k)
                if (row is None) if c is None else (row is not None and row.get(c, "") == r["new"]):
                    keep.append([r[col] for col in build.FIXES_COLUMNS])
    rows = keep + [[name, json.dumps(list(k)), c or "", v, nv, finding, why] for name, k, c, v, nv, finding, why in out]
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(build.FIXES_COLUMNS)
    w.writerows(rows)
    with open(build.FIXES_PATH, "w", newline="", encoding="utf-8") as f:
        f.write(buf.getvalue())
    print(f"wrote {len(rows)} fixes -> {os.path.relpath(build.FIXES_PATH, build.ROOT)}")


if __name__ == "__main__":
    main()
