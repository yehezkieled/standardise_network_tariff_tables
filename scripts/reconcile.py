"""Reconcile AER-side rows against distributor-side rows.

Inputs : out/aer_long.csv (side=AER), out/dnsp/*.csv (side=DNSP | AER_HOSTED)
Outputs: out/recon_detail.csv      every compared pair / unmatched item with status + explanation
         out/discrepancies.csv     the non-equal subset (what the brief calls "every discrepancy")
         out/discrepancies.xlsx    Summary grid + Discrepancies + Detail + Sources
         out/match_grid.csv        distributor x year match-rate grid
         out/format_timeline.csv   what each side published per year (format change timeline)
         out/recon_summary.json    machine-readable summary for the report / board

Comparison basis
  * AER side is the AER-authored file (2024-25, 2025-26, 2026-27). For 2023-24 the AER published no
    price file, so the AER side is the DNSP-submitted document hosted on aer.gov.au (side=AER_HOSTED).
  * DNSP side is the distributor's own publication (side=DNSP). From 2024-25, where none could be obtained the
    AER-hosted copy of the distributor's document is used instead and the pair is flagged
    (dnsp_side = AER_HOSTED).
  * Values are compared in standard units (cents; fixed charges per day; demand per published period),
    basis NUoS unless only another basis is published (flagged).
"""
import csv, glob, json, os, re, sys, math
from collections import defaultdict, Counter
from difflib import SequenceMatcher
sys.path.insert(0, os.path.dirname(__file__))
from schema import COLUMNS, season_from_label, export_direction
from units import to_std
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YEARS = ["2023-24", "2024-25", "2025-26", "2026-27"]
DNSPS = ["Ausgrid", "Endeavour Energy", "Essential Energy", "Energex", "Ergon Energy", "SA Power Networks",
         "CitiPower", "Powercor", "United Energy", "Jemena", "AusNet Services", "Evoenergy", "TasNetworks",
         "Power and Water Corporation"]

def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def code_key(c):
    c = (c or "").strip().upper()
    c = re.sub(r"\s+", "", c)
    return c

def code_alts(c, note="", distributor=""):
    """alternative keys for a published code: split on '/' or ',' (AER joint codes "A100/F100", "010, 011*"), drop trailing '*', drop '-SA' suffix,
    add/strip Ergon transmission-region suffix (T1..T4; AER 2024-25 keeps it in 'other identifier')."""
    base = code_key(c)
    base = base.split("\u2016")[0]  # drop name disambiguator
    alts = [base]
    for part in re.split(r"[/,]", base):
        alts += [part, part.rstrip("*"), re.sub(r"-SA$", "", part)]
        if distributor != "Ergon Energy":
            continue
        alts.append(re.sub(r"T[1-4]$", "", part))
        m = re.search(r"/\s*T([1-4])\b", note or "") or re.search(r"\u2016(?:.*\u2016)?T([1-4])$", code_key(c))
        if m and not re.search(r"T[1-4]$", part):
            alts.append(part + "T" + m.group(1))
    return [a for a in dict.fromkeys(alts) if a]

def digits(s):
    return re.findall(r"\d+", s or "")

def unit_family(u):
    u = u or ""
    if u.startswith("c/kWh"): return "energy"
    if u.startswith("c/kVAh"): return "apparent_energy"
    if u.startswith("c/day"): return "fixed"
    if u.startswith("c/kW/"): return "demand_kW"
    if u.startswith("c/kVA/"): return "demand_kVA"
    if u.startswith("c/k?/"): return "demand_unknown"
    if u.startswith("c/lamp"): return "lamp"
    return u or "none"

def unit_period(u):
    m = re.match(r"c/k(?:W|VA|\?)/(.+)$", u or "")
    return m.group(1) if m else ""

def decimals_of(s):
    return max(0, -Decimal(str(s)).as_tuple().exponent)


def rounding_tolerance(row):
    factor, _ = to_std(1, row["unit"], row["component"])
    return 0.5 * 10 ** (-decimals_of(row["value"])) * abs(factor) + 1e-9

def sim(a, b):
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()

def load_rows():
    rows = []
    with open(os.path.join(ROOT, "out/aer_long.csv")) as f:
        rows += list(csv.DictReader(f))
    for p in sorted(glob.glob(os.path.join(ROOT, "out/dnsp/*.csv"))):
        with open(p) as f:
            rows += list(csv.DictReader(f))
    for r in rows:
        r["value_f"] = fnum(r.get("value"))
        r["value_std_f"] = fnum(r.get("value_std"))
    return rows

def pick_side(rows, dnsp, fy, side, prefer_basis="NUoS"):
    """rows for one side; prefer NUoS basis, else DUoS/unknown; return (rows, basis_used, sources)."""
    cand = [r for r in rows if r["distributor"] == dnsp and r["fin_year"] == fy and r["side"] == side]
    if not cand:
        return [], "", []
    if any(r.get("gst") == "excl" for r in cand) and any(r.get("gst") == "incl" for r in cand):
        cand = [r for r in cand if r.get("gst") != "incl"]  # compare GST-exclusive prices only (AER files are ex GST)
    bases = Counter(r["basis"] for r in cand)
    for b in ("NUoS", "unknown", "DUoS"):
        if bases.get(b):
            sel = [r for r in cand if r["basis"] == b]
            return sel, b, sorted({r["source_file"] for r in sel})
    b = bases.most_common(1)[0][0]
    sel = [r for r in cand if r["basis"] == b]
    return sel, b, sorted({r["source_file"] for r in sel})

def dedupe(rows):
    """drop exact duplicate rows (same code/name/component/unit/value/basis/note) within one side."""
    seen = set(); out = []
    for r in rows:
        k = (code_key(r["tariff_code"]), (r["tariff_name"] or "").strip(), r["component"], r["unit"], r["value"], r["basis"], r.get("note", ""))
        if k in seen: continue
        seen.add(k); out.append(r)
    return out

def prefer_excl_metering(rows):
    """when one document prices the same code+component twice (e.g. Evoenergy Statement Table 2.6 NUOS incl metering and
    Table 2.7 NUOS excl metering) keep the rows whose note says metering is excluded, as the AER files exclude metering."""
    by = defaultdict(list)
    for r in rows:
        by[(code_key(r["tariff_code"]), (r["component"] or "").strip().lower(), r["basis"])].append(r)
    out = []
    for k, rs in by.items():
        if len(rs) > 1:
            ex = [r for r in rs if re.search(r"exclud\w*\s+metering|excl\.?\s+metering|without\s+metering", r.get("note", "") or "", re.I)]
            if ex and len(ex) < len(rs):
                rs = ex
        out += rs
    return out

def keyed(rows):
    rows = prefer_excl_metering(dedupe(rows))
    """group rows by tariff code; when one code carries several distinct tariff names (e.g. AER 'HSAC' historic
    bucket) disambiguate the key with the name so components of different tariffs are not merged."""
    by = defaultdict(list)
    for r in rows:
        by[code_key(r["tariff_code"])].append(r)
    out = {}
    def region(r):
        if r["distributor"] != "Ergon Energy":
            return ""
        m = re.search(r"/\s*(T[1-4])\b", r.get("note", "") or "")
        return m.group(1) if m else ""
    for k, rs in by.items():
        names = {(r["tariff_name"] or "").strip() for r in rs}
        regions = {region(r) for r in rs}
        if len(names) <= 1 and len(regions) <= 1:
            out[k] = rs
            continue
        for n in sorted(names):
            for rg in sorted(regions):
                sub = [r for r in rs if (r["tariff_name"] or "").strip() == n and region(r) == rg]
                if sub:
                    tag = "\u2016".join(x for x in (n if len(names) > 1 else "", rg if len(regions) > 1 else "") if x)
                    out[f"{k}\u2016{tag}" if tag else k] = sub
    # current tariffs before withdrawn/closed ones so they win contested matches
    return dict(sorted(out.items(), key=lambda kv: (bool(re.search(r"withdrawn|closed|obsolete|grandfather", kv[1][0]["tariff_name"] or "", re.I)), kv[0])))

def prefer_single_source(sel):
    """If a side has several source files for the same dnsp/year (e.g. xlsx + pdf), keep the one with most rows;
    the others are still used as evidence elsewhere."""
    by = defaultdict(list)
    for r in sel:
        by[r["source_file"]].append(r)
    if len(by) <= 1:
        return sel, ""
    # prefer non-LFiT-included, structured (xlsx) files, then larger
    def score(item):
        sf, rs = item
        s = len(rs)
        if sf.endswith(".xlsx"): s += 10000
        if any("LFiT included" in (r.get("note") or "") for r in rs): s -= 5000
        if any("AER-approved" in (r.get("note") or "") for r in rs): s += 2000
        return s
    best = max(by.items(), key=score)
    others = [k for k in by if k != best[0]]
    return best[1], "; ".join(others)

def lfit_included(d):
    """distributor row comes from an ACT LFiT-inclusive schedule (note or file name), not from an LFiT-excluded one."""
    txt = (d.get("note") or "") + " " + os.path.basename(d.get("source_file") or "")
    if not re.search(r"LFiT", txt):
        return False
    return not re.search(r"(excl\w*|without|net of|ex)[\s_-]*(of\s+)?LFiT|LFiT[\s_-]*(excl|removed|not included)|AER[_ -]approved", txt, re.I)

def metering_in_fixed(d):
    txt = (d.get("note") or "")
    return bool(re.search(r"includ\w*\s+(legacy\s+)?metering|metering\s+(charge\s+)?included", txt, re.I)) and not re.search(r"exclud\w*\s+metering", txt, re.I)

def classify_diff(a, d, aer_val, dnsp_val, ctx):
    """return (explanation_class, detail) for a value difference."""
    diff = dnsp_val - aer_val
    if aer_val == 0 and dnsp_val == 0:
        return "equal", ""
    # rounding: DNSP value published with N decimals in its own unit; convert tolerance to std units
    dec = decimals_of(d.get("value"))
    tol = rounding_tolerance(d)
    if abs(diff) <= tol:
        return "rounding", f"within half-unit of DNSP's {dec}dp publication"
    # AER value may itself be rounded coarser than the DNSP's
    deca = decimals_of(a.get("value"))
    if abs(diff) <= rounding_tolerance(a):
        return "rounding", f"within half-unit of AER's {deca}dp publication"
    hypotheses = []
    if metering_in_fixed(d) and unit_family(d["unit_std"]) == "fixed":
        hypotheses.append("[UNSURE] embedded metering may contribute; component adjustment not substantiated")
    if lfit_included(d):
        hypotheses.append("[UNSURE] LFiT may contribute; component adjustment not substantiated")
    if ctx.get("basis_dnsp") != ctx.get("basis_aer"):
        hypotheses.append("[UNSURE] differing price bases; component adjustment not substantiated")
    return "unexplained", "; ".join(hypotheses)


def unit_unverified(a, d):
    return "demand_unknown" in (unit_family(a["unit_std"]), unit_family(d["unit_std"]))


def compatible(a, d):
    families = {unit_family(a["unit_std"]), unit_family(d["unit_std"])}
    if len(families) > 1 and not (unit_unverified(a, d) and families <= {"demand_unknown", "demand_kW", "demand_kVA"}):
        return False
    if a["charge_type"] != d["charge_type"]:
        return False
    if a["charge_type"] == "export":
        if export_direction(a["component"], a["value"]) != export_direction(d["component"], d["value"]):
            return False
    for field in ("time_band", "season"):
        av = a.get(field) or (season_from_label(a["component"] + " " + a["unit"]) if field == "season" else "")
        dv = d.get(field) or (season_from_label(d["component"] + " " + d["unit"]) if field == "season" else "")
        if field == "time_band" and not (av and dv):
            continue
        if av != dv:
            return False
    pa, pd = unit_period(a["unit_std"]), unit_period(d["unit_std"])
    hard = lambda p: bool(p) and "?" not in p
    if hard(pa) and hard(pd) and pa != pd:
        return False
    return True


def reconcile():
    rows = load_rows()
    inv = {}
    with open(os.path.join(ROOT, "sources/inventory.csv")) as f:
        for r in csv.DictReader(f):
            inv[r["local_path"]] = r
    detail = []
    grid = []
    timeline = []
    for dnsp in DNSPS:
        for fy in YEARS:
            # AER side
            if fy == "2023-24":
                aer_rows, aer_basis, aer_src = pick_side(rows, dnsp, fy, "AER_HOSTED")
                aer_kind = "AER_HOSTED" if aer_rows else "none"
            else:
                aer_rows, aer_basis, aer_src = pick_side(rows, dnsp, fy, "AER")
                aer_kind = "AER" if aer_rows else "none"
            aer_rows, aer_dropped = prefer_single_source(aer_rows)
            # DNSP side
            d_rows, d_basis, d_src = pick_side(rows, dnsp, fy, "DNSP")
            d_kind = "DNSP"
            if not d_rows and fy != "2023-24":
                d_rows, d_basis, d_src = pick_side(rows, dnsp, fy, "AER_HOSTED")
                d_kind = "AER_HOSTED" if d_rows else "none"
            if d_rows and d_basis == "unknown" and fy != "2023-24":
                h_rows, h_basis, h_src = pick_side(rows, dnsp, fy, "AER_HOSTED")
                if h_rows and h_basis == "NUoS" and len({r["tariff_code"] for r in h_rows}) > 2 * len({r["tariff_code"] for r in d_rows}):
                    d_rows, d_basis, d_src = h_rows, h_basis, h_src
                    d_kind = "AER_HOSTED"
            if not d_rows:
                d_kind = "none"
            d_rows, d_dropped = prefer_single_source(d_rows)
            # all-basis stats for timeline
            all_aer = [r for r in rows if r["distributor"] == dnsp and r["fin_year"] == fy and r["side"] == ("AER_HOSTED" if fy == "2023-24" else "AER")]
            all_d = [r for r in rows if r["distributor"] == dnsp and r["fin_year"] == fy and r["side"] == "DNSP"]
            timeline.append({
                "distributor": dnsp, "fin_year": fy,
                "aer_side_kind": aer_kind, "aer_side_files": "; ".join(aer_src), "aer_side_bases": "/".join(sorted({r["basis"] for r in all_aer})),
                "aer_side_units": "; ".join(sorted({r["unit"] for r in all_aer if r["unit"]})),
                "aer_side_codes": len({r["tariff_code"] for r in aer_rows}),
                "dnsp_side_kind": d_kind, "dnsp_side_files": "; ".join(d_src), "dnsp_side_bases": "/".join(sorted({r["basis"] for r in all_d})),
                "dnsp_side_units": "; ".join(sorted({r["unit"] for r in all_d if r["unit"]})),
                "dnsp_side_codes": len({r["tariff_code"] for r in d_rows}),
                "dnsp_side_gst": "/".join(sorted({r["gst"] for r in all_d})),
                "dnsp_access_note": "; ".join(sorted({(inv.get(r["source_file"], {}).get("access_note") or "") for r in d_rows} - {""})),
            })
            g = {"distributor": dnsp, "fin_year": fy, "aer_side": aer_kind, "dnsp_side": d_kind, "aer_basis": aer_basis, "dnsp_basis": d_basis,
                 "aer_codes": 0, "dnsp_codes": 0, "codes_matched": 0, "codes_aer_only": 0, "codes_aer_placeholder": 0, "codes_dnsp_only": 0, "codes_mapped_by_name": 0, "codes_joint_variant": 0,
                 "components_compared": 0, "equal": 0, "rounding": 0, "explainable": 0, "unexplained": 0,
                 "components_aer_only": 0, "components_dnsp_only": 0, "match_rate": "", "reconciled_rate": "", "max_abs_pct_diff": "", "note": ""}
            if not aer_rows or not d_rows:
                if not aer_rows:
                    g["note"] = "no AER-side data"
                elif fy == "2023-24":
                    g["note"] = "no AER-authored file for 2023-24 and the distributor's own copy was not retrievable: the AER-hosted distributor document is the only source, nothing to compare"
                else:
                    g["note"] = "no DNSP-side data (distributor document not retrievable or carries no price table)"
                g["aer_codes"] = len({r["tariff_code"] for r in aer_rows}); g["dnsp_codes"] = len({r["tariff_code"] for r in d_rows})
                grid.append(g)
                continue
            ctx = {"basis_dnsp": d_basis, "basis_aer": aer_basis}
            A = keyed(aer_rows); D = keyed(d_rows)
            g["aer_codes"], g["dnsp_codes"] = len(A), len(D)
            # pass 1: exact key; pass 2: unique alias; pass 3: name (same digits, sim>=0.9)
            d_alias = defaultdict(set)
            for k in D:
                for alt in code_alts(k, D[k][0].get("note", ""), dnsp):
                    d_alias[alt].add(k)
            used_d = set()
            pairs = []
            unmatched_a = []
            for ak in A:
                target = None
                if ak in D and ak not in used_d:
                    target = ak
                else:
                    for alt in code_alts(ak, A[ak][0].get("note", ""), dnsp):
                        cands = [t for t in d_alias.get(alt, ()) if t not in used_d]
                        if len(cands) == 1:
                            target = cands[0]; break
                if target is None:
                    unmatched_a.append(ak)
                else:
                    used_d.add(target)
                    pairs.append((ak, target, "" if target == ak else f"code differs ({A[ak][0]['tariff_code']} vs {D[target][0]['tariff_code']}); matched by code alias"))
            # name-based pass over leftovers (greedy best-first)
            cand_pairs = []
            for ak in unmatched_a:
                aname = A[ak][0]["tariff_name"]
                if not aname: continue
                for dk in D:
                    if dk in used_d: continue
                    dname = D[dk][0]["tariff_name"]
                    sc = sim(re.sub(r"\(.*?\)|\*", "", aname), re.sub(r"\(.*?\)|\*", "", dname))
                    # digit sequences in names and in codes must agree (site-specific codes share a name and differ by number)
                    if sc >= 0.9 and digits(aname) == digits(dname) and digits(A[ak][0]["tariff_code"]) == digits(D[dk][0]["tariff_code"]):
                        cand_pairs.append((sc, ak, dk))
            for sc, ak, dk in sorted(cand_pairs, reverse=True):
                if ak in unmatched_a and dk not in used_d:
                    unmatched_a.remove(ak); used_d.add(dk)
                    g["codes_mapped_by_name"] += 1
                    pairs.append((ak, dk, f"code differs ({A[ak][0]['tariff_code']} vs {D[dk][0]['tariff_code']}); matched by tariff name (similarity {sc:.2f})"))
            # pass 4: AER lists several codes jointly on one row ("A100/F100", "010, 011*"); compare each extra
            # distributor code against that joint row instead of reporting it as DNSP-only
            joint = {}
            for ak, dk, _ in list(pairs):
                if re.search(r"[/,]", A[ak][0]["tariff_code"] or ""):
                    for alt in code_alts(ak, A[ak][0].get("note", ""), dnsp):
                        joint.setdefault(alt, ak)
            for dk in D:
                if dk in used_d: continue
                hit = next((joint[alt] for alt in code_alts(dk, D[dk][0].get("note", ""), dnsp) if alt in joint), None)
                if hit:
                    used_d.add(dk); g["codes_joint_variant"] += 1
                    pairs.append((hit, dk, f"AER lists codes jointly ({A[hit][0]['tariff_code']}); distributor publishes {D[dk][0]['tariff_code']} separately - compared against the joint AER row"))
            matched_a_keys = {p[0] for p in pairs}
            for ak in unmatched_a:
                r0 = A[ak][0]
                priced = [r for r in A[ak] if not r["component"].startswith("(no non-zero") and r["value_std_f"]]
                expl, det = "", "tariff code present in AER-side file but not in distributor publication"
                if not priced:
                    expl, det = "aer_zero_placeholder", "AER-side file lists this tariff with no prices (placeholder / TBA code)"
                    g["codes_aer_placeholder"] += 1
                elif re.search(r"withdrawn|closed|obsolete|grandfather", r0["tariff_name"], re.I):
                    expl, det = "withdrawn_tariff", "AER-side file prices a tariff its own name marks as withdrawn/closed; distributor no longer publishes it"
                    g["codes_aer_only"] += 1
                else:
                    g["codes_aer_only"] += 1
                detail.append(dict(distributor=dnsp, fin_year=fy, status="aer_only_code", explanation=expl, tariff_code=r0["tariff_code"], tariff_name=r0["tariff_name"],
                                   component=f"{len(priced)} priced component(s)", aer_value="", aer_unit="", dnsp_value="", dnsp_unit="", aer_value_std="", dnsp_value_std="", unit_std="",
                                   abs_diff="", pct_diff="", aer_basis=aer_basis, dnsp_basis=d_basis, aer_source=r0["source_file"], aer_url=r0["source_url"],
                                   dnsp_source="; ".join(d_src), dnsp_url="", aer_side=aer_kind, dnsp_side=d_kind, detail=det,
                                   dnsp_tariff_name="", dnsp_component="", note=r0.get("note", "")))
            matched_base = {re.sub(r"T[1-4]$", "", k.split("\u2016")[0]) if dnsp == "Ergon Energy" else k.split("\u2016")[0] for k in used_d}
            for dk in D:
                if dk not in used_d:
                    r0 = D[dk][0]
                    expl, det = "", "tariff code present in distributor publication but not in AER-side file"
                    base = re.sub(r"T[1-4]$", "", dk.split("\u2016")[0]) if dnsp == "Ergon Energy" else dk.split("\u2016")[0]
                    msite = re.match(r"^([A-Z]{2,6})(\d{3})$", dk.split("\u2016")[0])
                    if dnsp == "Ergon Energy" and base in matched_base and re.search(r"T[1-4]$", dk):
                        expl, det = "region_variant", "transmission-region variant (T1-T4 suffix) of a tariff the AER-side file lists once"
                    elif (msite and msite.group(1) in matched_base) or re.search(r"site[- ]specific", (r0.get("tariff_name") or "") + " " + (r0.get("note") or ""), re.I):
                        expl, det = "site_specific_variant", "site-specific (locational) variant of a standard tariff; the AER-side file lists only the standard code"
                    elif re.search(r"obsolete|withdrawn|closed|grandfather|not available (on application|for new)", (r0.get("note", "") or "") + " " + (r0.get("tariff_name", "") or ""), re.I):
                        expl, det = "withdrawn_tariff", "distributor still prices a tariff it marks obsolete/closed; AER-side file does not list it"
                    elif "trial" in ((r0.get("note", "") or "") + " " + (r0.get("tariff_name", "") or "")).lower():
                        expl, det = "trial_tariff", "trial tariff published by distributor; AER-side file has no priced counterpart"
                    g["codes_dnsp_only"] += 1
                    detail.append(dict(distributor=dnsp, fin_year=fy, status="dnsp_only_code", explanation=expl, tariff_code=r0["tariff_code"], tariff_name=r0["tariff_name"],
                                       component=f"{len(D[dk])} component(s)", aer_value="", aer_unit="", dnsp_value="", dnsp_unit="", aer_value_std="", dnsp_value_std="", unit_std="",
                                       abs_diff="", pct_diff="", aer_basis=aer_basis, dnsp_basis=d_basis, aer_source="; ".join(aer_src), aer_url="", dnsp_source=r0["source_file"], dnsp_url=r0["source_url"],
                                       aer_side=aer_kind, dnsp_side=d_kind, detail=det,
                                       dnsp_tariff_name=r0["tariff_name"], dnsp_component="", note=r0.get("note", "")))
            g["codes_matched"] = len(pairs)
            maxpct = 0.0
            for ak, dk, mapnote in pairs:
                arows = [r for r in A[ak] if r["value_std_f"] is not None]
                drows = [r for r in D[dk] if r["value_std_f"] is not None]
                # drop AER zero-placeholder rows
                arows = [r for r in arows if not r["component"].startswith("(no non-zero")]
                drows_avail = list(drows)
                matched = []
                for a in arows:
                    best = None; bd = None
                    for d in drows_avail:
                        if not compatible(a, d): continue
                        dd = abs(d["value_std_f"] - a["value_std_f"])
                        tol = rounding_tolerance(d)
                        if dd <= tol and (bd is None or dd < bd):
                            best, bd = d, dd
                    if best is not None:
                        matched.append((a, best)); drows_avail.remove(best)
                # pass 2: label match among remaining
                rem_a = [a for a in arows if all(a is not m[0] for m in matched)]
                for a in rem_a:
                    cands = [d for d in drows_avail if compatible(a, d)]
                    if not cands:
                        continue
                    sa = season_from_label(a["component"] + " " + a["unit"])
                    def lscore(d):
                        s = sim(a["component"], d["component"]) + 0.2
                        if a["time_band"]: s += 0.5
                        if a.get("season") or sa: s += 0.3
                        return s
                    best = max(cands, key=lscore)
                    unique = len(cands) == 1 and sum(
                        compatible(other, best) for other in rem_a
                        if all(other is not pair[0] for pair in matched)
                    ) == 1
                    if unique or lscore(best) >= 0.55:
                        matched.append((a, best)); drows_avail.remove(best)
                for a, d in matched:
                    av, dv = a["value_std_f"], d["value_std_f"]
                    cls, det = classify_diff(a, d, av, dv, ctx)
                    if unit_unverified(a, d):
                        det = "; ".join(x for x in (det, f"[UNSURE] unit unverified: demand quantity (kW or kVA) not established ({a['unit']} vs {d['unit']})") if x)
                    if cls == "equal" or abs(dv - av) < 1e-12:
                        status, cls = "equal", "equal"
                    elif cls == "rounding":
                        status = "equal_after_rounding"
                    else:
                        status = "value_differs"
                    g["components_compared"] += 1
                    if status == "equal": g["equal"] += 1
                    elif status == "equal_after_rounding": g["rounding"] += 1
                    elif cls == "unexplained": g["unexplained"] += 1
                    else: g["explainable"] += 1
                    pct = (dv - av) / av * 100 if av else ""
                    if pct != "" and status == "value_differs": maxpct = max(maxpct, abs(pct))
                    detail.append(dict(distributor=dnsp, fin_year=fy, status=status, explanation=cls, tariff_code=a["tariff_code"], tariff_name=a["tariff_name"], component=a["component"],
                                       aer_value=a["value"], aer_unit=a["unit"], dnsp_value=d["value"], dnsp_unit=d["unit"], aer_value_std=av, dnsp_value_std=dv, unit_std=a["unit_std"] if a["unit_std"] == d["unit_std"] else f'{a["unit_std"]} vs {d["unit_std"]}',
                                       abs_diff=dv - av, pct_diff=pct, aer_basis=a["basis"], dnsp_basis=d["basis"], aer_source=a["source_file"], aer_url=a["source_url"],
                                       dnsp_source=d["source_file"], dnsp_url=d["source_url"], aer_side=aer_kind, dnsp_side=d_kind, detail="; ".join(x for x in (mapnote, det) if x),
                                       dnsp_tariff_name=d["tariff_name"], dnsp_component=d["component"], note="; ".join(x for x in (a.get("note", ""), d.get("note", "")) if x)))
                for a in arows:
                    if all(a is not m[0] for m in matched):
                        g["components_aer_only"] += 1
                        detail.append(dict(distributor=dnsp, fin_year=fy, status="aer_only_component", explanation="", tariff_code=a["tariff_code"], tariff_name=a["tariff_name"], component=a["component"],
                                           aer_value=a["value"], aer_unit=a["unit"], dnsp_value="", dnsp_unit="", aer_value_std=a["value_std_f"], dnsp_value_std="", unit_std=a["unit_std"], abs_diff="", pct_diff="",
                                           aer_basis=a["basis"], dnsp_basis=d_basis, aer_source=a["source_file"], aer_url=a["source_url"], dnsp_source="; ".join(d_src), dnsp_url="", aer_side=aer_kind, dnsp_side=d_kind,
                                           detail=mapnote or "component priced in AER-side file with no counterpart in distributor publication", dnsp_tariff_name=D[dk][0]["tariff_name"], dnsp_component="", note=a.get("note", "")))
                aer_placeholder = not arows
                for d in drows_avail:
                    if d["value_std_f"] == 0:
                        continue
                    g["components_dnsp_only"] += 1
                    detail.append(dict(distributor=dnsp, fin_year=fy, status="dnsp_only_component", explanation=("aer_zero_placeholder" if aer_placeholder else ""), tariff_code=d["tariff_code"], tariff_name=A[ak][0]["tariff_name"], component=d["component"],
                                       aer_value="", aer_unit="", dnsp_value=d["value"], dnsp_unit=d["unit"], aer_value_std="", dnsp_value_std=d["value_std_f"], unit_std=d["unit_std"], abs_diff="", pct_diff="",
                                       aer_basis=aer_basis, dnsp_basis=d["basis"], aer_source="; ".join(aer_src), aer_url="", dnsp_source=d["source_file"], dnsp_url=d["source_url"], aer_side=aer_kind, dnsp_side=d_kind,
                                       detail=mapnote or "component priced in distributor publication with no counterpart in AER-side file", dnsp_tariff_name=d["tariff_name"], dnsp_component=d["component"], note=d.get("note", "")))
            comp = g["components_compared"]
            g["match_rate"] = round((g["equal"] + g["rounding"]) / comp * 100, 1) if comp else ""
            g["reconciled_rate"] = round((comp - g["unexplained"]) / comp * 100, 1) if comp else ""
            g["max_abs_pct_diff"] = round(maxpct, 2) if comp else ""
            notes = []
            if d_kind == "AER_HOSTED": notes.append("distributor side = AER-hosted copy of distributor document (own-site copy unavailable or carries no full price schedule)")
            if aer_dropped: notes.append(f"AER-side alt source not used: {aer_dropped}")
            if d_dropped: notes.append(f"DNSP-side alt source not used: {d_dropped}")
            g["note"] = "; ".join(notes)
            grid.append(g)
    return detail, grid, timeline

def write_outputs(detail, grid, timeline):
    out = os.path.join(ROOT, "out")
    dcols = ["distributor", "fin_year", "status", "explanation", "tariff_code", "tariff_name", "component", "aer_value", "aer_unit", "dnsp_value", "dnsp_unit", "aer_value_std", "dnsp_value_std", "unit_std",
             "abs_diff", "pct_diff", "aer_basis", "dnsp_basis", "aer_side", "dnsp_side", "aer_source", "aer_url", "dnsp_source", "dnsp_url", "dnsp_tariff_name", "dnsp_component", "detail", "note"]
    order = {"value_differs": 0, "aer_only_code": 1, "dnsp_only_code": 2, "aer_only_component": 3, "dnsp_only_component": 4, "equal_after_rounding": 5, "equal": 6}
    detail.sort(key=lambda r: (r["distributor"], r["fin_year"], order.get(r["status"], 9), -(abs(float(r["pct_diff"])) if r["pct_diff"] not in ("", None) else 0), r["tariff_code"]))
    with open(os.path.join(out, "recon_detail.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=dcols); w.writeheader(); w.writerows(detail)
    disc = [r for r in detail if r["status"] not in ("equal", "equal_after_rounding")]
    with open(os.path.join(out, "discrepancies.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=dcols); w.writeheader(); w.writerows(disc)
    gcols = list(grid[0].keys())
    with open(os.path.join(out, "match_grid.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=gcols); w.writeheader(); w.writerows(grid)
    tcols = list(timeline[0].keys())
    with open(os.path.join(out, "format_timeline.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=tcols); w.writeheader(); w.writerows(timeline)
    # xlsx
    import openpyxl
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    ws = wb.active; ws.title = "Summary"
    ws.append(gcols)
    for g in grid: ws.append([g[c] for c in gcols])
    ws2 = wb.create_sheet("Discrepancies"); ws2.append(dcols)
    for r in disc: ws2.append([r[c] for c in dcols])
    ws3 = wb.create_sheet("All compared"); ws3.append(dcols)
    for r in detail: ws3.append([r[c] for c in dcols])
    ws4 = wb.create_sheet("Format timeline"); ws4.append(tcols)
    for t in timeline: ws4.append([t[c] for c in tcols])
    ws5 = wb.create_sheet("Sources")
    with open(os.path.join(ROOT, "sources/inventory.csv")) as f:
        for row in csv.reader(f): ws5.append(row)
    for w_ in wb.worksheets:
        for i, col in enumerate(w_.columns, 1):
            w_.column_dimensions[get_column_letter(i)].width = min(60, max(10, max(len(str(c.value)) if c.value is not None else 0 for c in list(col)[:200]) + 2))
        w_.freeze_panes = "A2"
    wb.save(os.path.join(out, "discrepancies.xlsx"))
    summary = {"grid": grid, "timeline": timeline,
               "status_counts": dict(Counter(r["status"] for r in detail)),
               "explanation_counts": dict(Counter(r["explanation"] for r in detail if r["status"] == "value_differs")),
               "top_differences": [r for r in detail if r["status"] == "value_differs" and r["pct_diff"] != ""][:0]}
    tops = sorted([r for r in detail if r["status"] == "value_differs" and r["pct_diff"] != ""], key=lambda r: -abs(float(r["pct_diff"])))[:60]
    summary["top_differences"] = tops
    with open(os.path.join(out, "recon_summary.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    return disc

def main():
    detail, grid, timeline = reconcile()
    disc = write_outputs(detail, grid, timeline)
    print(f"{'distributor':28s} {'FY':8s} {'A':>5s} {'D':>5s} {'aerC':>5s} {'dC':>5s} {'match':>6s} {'eq':>4s} {'rnd':>4s} {'expl':>4s} {'unex':>4s} {'A-only':>6s} {'D-only':>6s} {'joint':>5s} {'cmpA':>5s} {'cmpD':>5s} rate")
    for g in grid:
        print(f"{g['distributor']:28s} {g['fin_year']:8s} {g['aer_side'][:5]:>5s} {g['dnsp_side'][:5]:>5s} {g['aer_codes']:5d} {g['dnsp_codes']:5d} {g['codes_matched']:6d} {g['equal']:4d} {g['rounding']:4d} {g['explainable']:4d} {g['unexplained']:4d} {g['codes_aer_only']:6d} {g['codes_dnsp_only']:6d} {g['codes_joint_variant']:5d} {g['components_aer_only']:5d} {g['components_dnsp_only']:5d} {g['match_rate']} {g['note'][:40]}")
    print("discrepancy rows:", len(disc), "detail rows:", len(detail))

if __name__ == "__main__":
    main()
