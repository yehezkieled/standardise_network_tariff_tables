"""Parse the AER-authored price files into the normalised long table (out/aer_long.csv).

Sources handled:
  * AER consolidated stakeholder report 2025-26 (v5) and 2026-27 (26 Aug 2026) - sheet 'Tariff schedule'
    (total network prices = distribution + transmission + jurisdictional scheme; excl GST).
  * AER per-DNSP stakeholder reports 2024-25 - sheet 'Tariff schedule' (Tariff schedule 3 = all prices,
    Tariff schedule 4 = site specific; each with Total / Distribution / DPPC / JSA sections).
There is no AER-authored price file for 2023-24 (the AER only hosts the DNSP-submitted documents); those are
parsed by the DNSP parsers with side=AER_HOSTED.
"""
import csv, re, sys, os
import openpyxl
sys.path.insert(0, os.path.dirname(__file__))
from schema import COLUMNS, REPEATED_PRINTING, charge_type_from_label, time_band_from_label, season_from_label
from units import period_stated, to_std
from published import cell_value
from tariffdb import build_support, locators

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def consolidated_versions():
    """Every held AER consolidated report version, from the version registry (scripts/tariffdb/build_support.py):
    [(fin_year, version_seq, path, url)], URLs from sources/inventory.csv."""
    urls = {r["local_path"]: r["source_url"] for r in build_support.read_inventory()}
    return [(fy, seq, path, urls[path]) for (fy, seq), path in sorted(build_support.AER_CONSOLIDATED_FILES.items())]


# The latest held version of each year is the AER side of the reconciliation (out/aer_long.csv). Every earlier held
# version goes to out/aer_versions_long.csv: the tariff database keeps proposed and approved prices side by side, and
# `scripts/reconcile.py --aer-version` reconciles any of them.
LATEST = {fy: seq for fy, seq, _, _ in consolidated_versions()}
CONSOLIDATED = [(fy, path, url) for fy, seq, path, url in consolidated_versions() if seq == LATEST[fy]]
SUPERSEDED = [(fy, path, url, f"AER consolidated stakeholder report v{seq} ({build_support.version_date_text(fy, seq)}): "
               f"{build_support.version_status_text(fy, seq)} prices")
              for fy, seq, path, url in consolidated_versions() if seq != LATEST[fy]]

STAKEHOLDER_2024_25 = {
    "Ausgrid": ("AER_Stakeholder_report_Ausgrid_2024-25.xlsx", "https://www.aer.gov.au/system/files/2024-05/AER%20-%20Stakeholder%20report%20-%20Ausgrid%20-%202024%E2%80%9325%20Annual%20Pricing%20Proposal.xlsx"),
    "AusNet Services": ("AER_Stakeholder_report_AusNet_2024-25.xlsx", "https://www.aer.gov.au/system/files/2024-05/AER%20-%20Stakeholder%20report%20-%20AusNet%20Services%20-%202024%E2%80%9325%20Annual%20Pricing%20Proposal.xlsx"),
    "CitiPower": ("AER_Stakeholder_report_CitiPower_2024-25.xlsx", "https://www.aer.gov.au/system/files/2024-05/AER%20-%20Stakeholder%20report%20-%20CitiPower-%202024%E2%80%9325%20Annual%20Pricing%20Proposal.xlsx"),
    "Endeavour Energy": ("AER_Stakeholder_report_Endeavour_2024-25.xlsx", "https://www.aer.gov.au/system/files/2024-05/AER%20-%20Stakeholder%20report%20-%20Endeavour%20Energy%20-%202024%E2%80%9325%20Annual%20Pricing%20Proposal.xlsx"),
    "Energex": ("AER_Stakeholder_report_Energex_2024-25.xlsx", "https://www.aer.gov.au/system/files/2024-05/AER%20-%20Stakeholder%20report%20-%20Energex%20-%202024%E2%80%9325%20Annual%20Pricing%20Proposal.xlsx"),
    "Ergon Energy": ("AER_Stakeholder_report_Ergon_2024-25.xlsx", "https://www.aer.gov.au/system/files/2024-05/AER%20-%20Stakeholder%20report%20-%20Ergon%20Energy%20-%202024%E2%80%9325%20Annual%20Pricing%20Proposal.xlsx"),
    "Essential Energy": ("AER_Stakeholder_report_Essential_2024-25.xlsx", "https://www.aer.gov.au/system/files/2024-05/AER%20-%20Stakeholder%20report%20-%20Essential%20Energy%20-%202024%E2%80%9325%20Annual%20Pricing%20Proposal.xlsx"),
    "Evoenergy": ("AER_Stakeholder_report_Evoenergy_2024-25.xlsx", "https://www.aer.gov.au/system/files/2024-05/AER%20-%20Stakeholder%20report%20-%20Evoenergy%20-%202024%E2%80%9325%20Annual%20Pricing%20Proposal.xlsx"),
    "Jemena": ("AER_Stakeholder_report_Jemena_2024-25.xlsx", "https://www.aer.gov.au/system/files/2024-05/AER%20-%20Stakeholder%20report%20-%20Jemena%20-%202024%E2%80%9325%20Annual%20Pricing%20Proposal.xlsx"),
    "Power and Water Corporation": ("AER_Stakeholder_report_PWC_2024-25.xlsx", "https://www.aer.gov.au/system/files/2024-05/AER%20-%20Stakeholder%20report%20-%20Power%20and%20Water%20Corporation%20-%202024%E2%80%9325%20Annual%20Pricing%20Proposal.xlsx"),
    "Powercor": ("AER_Stakeholder_report_Powercor_2024-25.xlsx", "https://www.aer.gov.au/system/files/2024-05/AER%20-%20Stakeholder%20report%20-%20Powercor%20-%202024%E2%80%9325%20Annual%20Pricing%20Proposal.xlsx"),
    "SA Power Networks": ("AER_Stakeholder_report_SAPN_2024-25_updated17Jul2024.xlsx", "https://www.aer.gov.au/system/files/2024-07/AER%20-%20Stakeholder%20report%20-%20SA%20Power%20Networks%20-%20%202024%E2%80%9325%20Annual%20Pricing%20Proposal%20%28updated%2017%20July%202024%29.xlsx"),
    "TasNetworks": ("AER_Stakeholder_report_TasNetworks_2024-25.xlsx", "https://www.aer.gov.au/system/files/2024-05/AER%20-%20Stakeholder%20report%20-%20TasNetworks%20-%202024%E2%80%9325%20Annual%20Pricing%20Proposal.xlsx"),
    "United Energy": ("AER_Stakeholder_report_UnitedEnergy_2024-25.xlsx", "https://www.aer.gov.au/system/files/2024-05/AER%20-%20Stakeholder%20report%20-%20United%20Energy%20-%202024%E2%80%9325%20Annual%20Pricing%20Proposal.xlsx"),
}

SECTION_BASIS = {
    "Total network prices": "NUoS",
    "Distribution": "DUoS",
    "Designated Pricing Proposal Costs": "DPPC",
    "Jurisdictional Scheme Amounts": "JSA",
}


def nz(v):
    return v is not None and v != "" and not (isinstance(v, (int, float)) and v == 0)


# Billing period of a demand price whose AER unit states none ('$/kVA', '$dollars/kVA'), from the distributor's own
# price lists. SA Power Networks prints every demand price in '$/kVA/day' (and '$kW/day'), and the AER rows carry the
# same values (HVAD265 'Ann Dmnd Pk' 0.3348 in the AER 2024-25 report = '0.3348 $/kVA/day' in SAPN's 2024-25 pricing
# proposal p67): 'Ann'/'Mth' in the AER label name the demand measurement window, not the billing period.
DEMAND_PERIOD_HINT = {"SA Power Networks": ("day", "billing period per SA Power Networks' own price lists ('$/kVA/day'); "
                                                    "'Ann'/'Mth' in the AER label is the demand measurement window")}


def row(dnsp, fy, code, name, cls, comp, unit, val, basis, src, url, note="", side="AER", locator=""):
    hint, hint_note = DEMAND_PERIOD_HINT.get(dnsp, ("", ""))
    if hint and not period_stated(unit) and re.search(r"/\s*k(?:VA|W)\b", unit or "", re.I):
        note = "; ".join(x for x in (note, hint_note) if x)
    else:
        hint = ""
    vs, us = to_std(val, unit, comp, period_hint=hint)
    ct = charge_type_from_label(comp, unit)
    if dnsp == "Ausgrid" and str(code).strip().rstrip("*") == "EA029" and ct == "energy":
        ct = "export"
    return {
        "side": side, "distributor": dnsp, "fin_year": fy, "tariff_code": str(code).strip(), "tariff_name": (name or "").strip() if isinstance(name, str) else str(name or ""),
        "customer_class": cls or "", "component": comp.strip(), "charge_type": ct,
        "time_band": time_band_from_label(comp), "season": season_from_label(comp) or season_from_label(unit), "unit": unit, "value": val,
        "value_std": vs, "unit_std": us, "gst": "excl", "basis": basis, "source_file": src, "source_url": url, "note": note,
        "locator": locator,
    }


def consolidated_columns(ws, hr):
    """Columns of one distributor block, found by header label: the layout moves between versions (2025-26 v1 has
    'Tariff class' and 'Code' in D/E and prices from G; v5 and 2026-27 have 'Tariff code' (SA Power Networks: 'Code SA')
    in D and prices from H)."""
    labels = {c: ws.cell(hr, c).value.strip() for c in range(3, ws.max_column + 1) if isinstance(ws.cell(hr, c).value, str)}
    code_col = next(c for c, v in labels.items() if v in ("Tariff code", "Code", "Code SA"))
    top_col = next(c for c, v in labels.items() if v == "Top")
    return code_col, top_col + 1


def parse_consolidated(fy, path, url, note_prefix=""):
    wb = openpyxl.load_workbook(os.path.join(ROOT, path), data_only=True)
    ws = wb["Tariff schedule"]
    out = []
    maxr = ws.max_row
    blocks = []
    for rr in range(1, maxr + 1):
        b = ws.cell(rr, 2).value
        if isinstance(b, str) and re.search(r"\d{4}.\d{2} network prices$", b.strip()):
            blocks.append(rr)
    for bi, hr in enumerate(blocks):
        dnsp = re.sub(r"\s+\d{4}.\d{2} network prices$", "", ws.cell(hr, 2).value.strip())
        code_col, first_price_col = consolidated_columns(ws, hr)
        comps = {}
        for c in range(first_price_col, ws.max_column + 1):
            lab = ws.cell(hr, c).value
            if isinstance(lab, str) and lab.strip():
                comps[c] = (lab.strip(), (ws.cell(hr + 1, c).value or "").strip() if isinstance(ws.cell(hr + 1, c).value, str) else str(ws.cell(hr + 1, c).value or ""))
        end = blocks[bi + 1] if bi + 1 < len(blocks) else maxr + 1
        for rr in range(hr + 2, end):
            code = ws.cell(rr, code_col).value
            name = ws.cell(rr, 3).value
            if ws.cell(rr, 2).value == "End":
                break
            if code == "#REF!":
                code = ""  # 2025-26 v1: the code column is a broken formula; the row is identified by its AER tariff ID
            if not nz(code) and not (isinstance(name, str) and name.strip()):
                continue
            if code in (None, "", 0) and isinstance(name, str):
                code = ""
            aer_id = ws.cell(rr, 2).value if isinstance(ws.cell(rr, 2).value, str) else ""
            any_val = False
            for c, (lab, unit) in comps.items():
                v = ws.cell(rr, c).value
                if isinstance(v, (int, float)) and v != 0:
                    any_val = True
                    note = "; ".join(x for x in (note_prefix, f"aer_id={aer_id}" if aer_id else "") if x)
                    out.append(row(dnsp, fy, code, name, "", lab, unit, cell_value(ws.cell(rr, c)), "NUoS", path, url, note,
                                   locator=locators.xlsx(ws, ws.cell(rr, c))))
            # a superseded version without codes (v1) keeps its zero-priced rows by AER tariff ID
            if not any_val and (nz(code) or (note_prefix and aer_id)):
                note = "; ".join(x for x in (note_prefix, "zero-priced row", f"aer_id={aer_id}" if aer_id else "") if x)
                out.append(row(dnsp, fy, code, name, "", "(no non-zero components)", "", 0, "NUoS", path, url, note,
                               locator=locators.xlsx(ws, ws.cell(rr, code_col if nz(code) else 2))))
    return out


def parse_stakeholder_2024_25(dnsp, fname, url):
    path = f"sources/aer/2024-25_stakeholder_reports/{fname}"
    wb = openpyxl.load_workbook(os.path.join(ROOT, path), data_only=True)
    ws = wb["Tariff schedule"]
    out = []
    printed = {}  # (code, component, unit, basis) -> first row printing it (Tariff schedule 3)
    schedules = []
    for rr in range(1, ws.max_row + 1):
        b = ws.cell(rr, 2).value
        if isinstance(b, str) and re.match(r"Tariff schedule \d", b):
            schedules.append((rr, b))
    # metering (schedule 1) is read by the tariff database build; the DMO/VDO schedule (2) reprints a subset of the
    # schedule 3 prices, so it is read after schedule 3 and each identical value is marked as a repeated printing
    order = sorted(range(len(schedules)), key=lambda i: ("DMO" in schedules[i][1] or "VDO" in schedules[i][1], i))
    for si in order:
        hr, title = schedules[si]
        if "Metering" in title:
            continue
        default_offer = "DMO" in title or "VDO" in title
        note_sched = "site-specific" if "Site specific" in title else title.strip() if default_offer else ""
        comps = {}
        for c in range(9, ws.max_column + 1):
            lab = ws.cell(hr, c).value
            if isinstance(lab, str) and lab.strip():
                u = ws.cell(hr + 1, c).value
                comps[c] = (lab.strip(), u.strip() if isinstance(u, str) else "")
        end = schedules[si + 1][0] if si + 1 < len(schedules) else ws.max_row + 1
        basis = "NUoS"
        for rr in range(hr + 2, end):
            cval = ws.cell(rr, 3).value
            code = ws.cell(rr, 5).value
            if isinstance(cval, str) and cval.strip() in SECTION_BASIS and not nz(code):
                basis = SECTION_BASIS[cval.strip()]
                continue
            if not nz(code):
                continue
            name = cval if isinstance(cval, str) else ""
            cls = ws.cell(rr, 4).value or ""
            others = [str(ws.cell(rr, c).value).strip() for c in (6, 7) if nz(ws.cell(rr, c).value)]
            other = " / ".join(others)
            code_note = ""
            if str(code).strip() in ("-", "–") and others:
                # SA Power Networks 'Zone Substation kVA Locational': '-' in 'Code SA', the code in 'Other identifier'
                code_note = f"'Code SA' printed as {str(code).strip()!r}; code taken from 'Other identifier' {others[0]!r}"
                code, others, other = others[0], others[1:], " / ".join(others[1:])
            note = "; ".join(x for x in (note_sched, code_note, f"other_id={other}" if other else "") if x)
            any_val = False
            for c, (lab, unit) in comps.items():
                v = ws.cell(rr, c).value
                if isinstance(v, (int, float)) and v != 0:
                    any_val = True
                    r = row(dnsp, "2024-25", code, name, cls, lab, unit, cell_value(ws.cell(rr, c)), basis, path, url, note,
                            locator=locators.xlsx(ws, ws.cell(rr, c)))
                    if default_offer:
                        first = printed.get((r["tariff_code"], lab, unit, basis))
                        if first is not None and first["value"] == r["value"]:
                            r["note"] = "; ".join(x for x in (r["note"], f"{REPEATED_PRINTING} of {first['locator']} "
                                                                         "(Tariff schedule 3, identical)") if x)
                    else:
                        printed.setdefault((r["tariff_code"], lab, unit, basis), r)
                    out.append(r)
            if not any_val and basis == "NUoS" and not default_offer:
                out.append(row(dnsp, "2024-25", code, name, cls, "(no non-zero components)", "", 0, basis, path, url, ("zero-priced row; " + note).strip("; "),
                               locator=locators.xlsx(ws, ws.cell(rr, 5))))
    return out


def main():
    rows = []
    for fy, path, url in CONSOLIDATED:
        rows += parse_consolidated(fy, path, url)
    for dnsp, (fname, url) in STAKEHOLDER_2024_25.items():
        rows += parse_stakeholder_2024_25(dnsp, fname, url)
    os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
    with open(os.path.join(ROOT, "out/aer_long.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    versions = []
    for fy, path, url, note in SUPERSEDED:
        versions += parse_consolidated(fy, path, url, note_prefix=note)
    with open(os.path.join(ROOT, "out/aer_versions_long.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(versions)
    print("superseded-version rows", len(versions))
    # summary
    from collections import Counter
    c = Counter((r["fin_year"], r["distributor"], r["basis"]) for r in rows)
    uniq = {}
    for r in rows:
        uniq.setdefault((r["fin_year"], r["distributor"], r["basis"]), set()).add(r["tariff_code"])
    for k in sorted(uniq):
        print(f"{k[0]} {k[1]:30s} {k[2]:5s} tariffs={len(uniq[k]):4d} rows={c[k]}")
    print("total rows", len(rows))


if __name__ == "__main__":
    main()
