#!/usr/bin/env python
"""Parse Ausgrid and Endeavour Energy (NSW) network price documents into out/dnsp/ausgrid_endeavour.csv.

Files parsed (see sources/inventory.csv for URLs):
  Ausgrid    2023-24  AER_HOSTED  annual SCS pricing proposal, Tables 4.1-4.4 (NUOS / DUOS / TUOS / jurisdictional)
  Ausgrid    2024-25..2026-27  DNSP  Network Price List PDFs (main, storage and trial tariff tables)
  Endeavour  2023-24  AER_HOSTED  Pricing Proposal, "Proposed Network / Distribution / DPPC / JSA Prices - FY24"
  Endeavour  2024-25..2026-27  DNSP  NUOS Price List PDFs (Table 1a standard, Table 3a obsolete where published)

Run from the repo root:  .venv/bin/python scripts/dnsp/ausgrid_endeavour.py [--debug]
"""
from decimal import Decimal
import csv
import os
import re
import sys

import pdfplumber

sys.path.insert(0, "scripts")
import schema  # noqa: E402
import units  # noqa: E402
sys.path.insert(0, "scripts/tariffdb")
import locators  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "out", "dnsp", "ausgrid_endeavour.csv")
DEBUG = "--debug" in sys.argv

FILES = [
    # (distributor, fin_year, side, repo path)
    ("Ausgrid", "2023-24", "AER_HOSTED", "sources/aer/2023-24_price_lists/Ausgrid_2023-24_annual_SCS_pricing_proposal_31Mar2023.pdf"),
    ("Ausgrid", "2024-25", "DNSP", "sources/dnsp/ausgrid/Ausgrid_Network_Price_List_2024-25_wayback20250806.pdf"),
    ("Ausgrid", "2025-26", "DNSP", "sources/dnsp/ausgrid/Ausgrid_Network_Price_List_2025-26.pdf"),
    ("Ausgrid", "2026-27", "DNSP", "sources/dnsp/ausgrid/Ausgrid_Network_Price_List_2026-27.pdf"),
    ("Endeavour Energy", "2023-24", "AER_HOSTED", "sources/aer/2023-24_price_lists/Endeavour_2023-24_Pricing_Proposal_31Mar2023.pdf"),
    ("Endeavour Energy", "2024-25", "DNSP", "sources/dnsp/endeavour/Endeavour_NUOS_Price_List_2024-25_v10.pdf"),
    ("Endeavour Energy", "2025-26", "DNSP", "sources/dnsp/endeavour/Endeavour_NUOS_Price_List_2025-26_v11.pdf"),
    ("Endeavour Energy", "2026-27", "DNSP", "sources/dnsp/endeavour/Endeavour_NUOS_Price_List_2026-27.pdf"),
]

NUM_RE = re.compile(r"^-?[\d,]*\d\.\d+\*?$|^-?\d[\d,]*\*?$")
UNIT_RE = re.compile(r"^[¢c]/")
WARNINGS = []
SKIPPED = []  # published codes deliberately not emitted (reported in the run summary)
METERING = []  # per-tariff metering cells (schema.METERING_COLUMNS), written to the metering side output


def warn(msg):
    WARNINGS.append(msg)
    print("WARN:", msg, file=sys.stderr)


def load_inventory():
    inv = {}
    with open(os.path.join(ROOT, "sources", "inventory.csv"), newline="") as f:
        for r in csv.DictReader(f):
            if r["local_path"]:
                inv[r["local_path"]] = r["source_url"]
    return inv


def num(s):
    """'2,749.00' / '-3.8551' / '3.3973*' -> float; '' / None / text -> None."""
    if s is None:
        return None
    t = str(s).strip().replace("\n", "").replace(",", "").rstrip("*").strip()
    if not t or not re.match(r"^-?\d+(\.\d+)?$", t):
        return None
    return Decimal(t)


def norm_unit(u):
    """Published unit text -> keep as published, only normalising the cent sign spelling."""
    return u.replace("¢", "c").strip()


def make_row(dist, fy, side, path, url, code, name, cls, component, unit, value, basis, gst, note,
             locator, time_band=None, season=None, charge_type=None):
    vstd, ustd = units.to_std(value, unit, component)
    return {
        "side": side, "distributor": dist, "fin_year": fy, "tariff_code": code.strip(), "tariff_name": name.strip(),
        "customer_class": cls.strip(), "component": component,
        "charge_type": charge_type or schema.charge_type_from_label(component, unit),
        "time_band": time_band if time_band is not None else schema.time_band_from_label(component),
        "season": season if season is not None else schema.season_from_label(component),
        "unit": unit, "value": repr(float(value)) if isinstance(value, float) else str(value),
        "value_std": "" if vstd is None else repr(float(vstd)), "unit_std": ustd, "gst": gst, "basis": basis,
        "source_file": path, "source_url": url, "note": note,
        "locator": locator,
    }


# ----------------------------------------------------------------------------------------------------------------------
# Generic helpers for pdfplumber grid tables (Ausgrid documents)
# ----------------------------------------------------------------------------------------------------------------------
def page_cells(page):
    """All table cells on a page as (bbox, text) using pdfplumber's rect-based table finder."""
    out = []
    for t in page.find_tables():
        grid = t.extract()
        for ri, row in enumerate(t.rows):
            for ci, bbox in enumerate(row.cells):
                if bbox is None:
                    continue
                txt = grid[ri][ci] if ri < len(grid) and ci < len(grid[ri]) else None
                out.append((bbox, (txt or "").strip()))
    return out


def clean_label(t):
    t = re.sub(r"\s*\n\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = t.replace("Non- TOU", "Non-TOU").replace("Off- peak", "Off-peak").replace("Sub- transmission", "Sub-transmission")
    return t


def header_text(raw_header):
    """Printed header of a column from its ' | '-joined hierarchy: 'Metering | Service | Charge' and
    'Metering Service Charge | Metering Service | Charge' (the merged cell read twice) are both 'Metering Service Charge'."""
    parts = raw_header.split(" | ")
    for i in range(1, len(parts)):
        if " ".join(parts[i:]) == " ".join(parts[:i]):
            return clean_label(" ".join(parts[:i]))
    return clean_label(" ".join(parts))


def x_contains(bbox, x):
    return bbox[0] - 0.5 <= x <= bbox[2] + 0.5


def y_contains(bbox, y):
    return bbox[1] - 0.5 <= y <= bbox[3] + 0.5


def grid_tables(page, code_re, class_hdr_re=r"Tariff Class", name_hdr_re=r"Tariff Name"):
    """Yield price tables on a page as dicts: columns (unit cells with header label), rows (code, name, class, values).

    A table is a group of unit cells sharing the same top. Header label for each unit column = text of all header
    cells (above the unit row, within the table's x-extent) whose x-range contains the unit cell's centre.
    """
    cells = page_cells(page)
    unit_cells = [(b, t) for b, t in cells if UNIT_RE.match(t.replace("\n", ""))]
    if not unit_cells:
        return
    # group unit cells by top (one group per table)
    groups = {}
    for b, t in unit_cells:
        key = round(b[1] / 4)
        groups.setdefault(key, []).append((b, t))
    for key in sorted(groups):
        ucells = sorted(groups[key], key=lambda c: c[0][0])
        if len(ucells) < 3:
            continue
        unit_top = min(b[1] for b, _ in ucells)
        unit_bottom = max(b[3] for b, _ in ucells)
        # table vertical extent: data rows below the unit row until the next unit row (or page end)
        later_tops = [min(b[1] for b, _ in groups[k]) for k in groups if min(b[1] for b, _ in groups[k]) > unit_bottom + 2]
        table_end = min(later_tops) - 40 if later_tops else page.height
        # header band: cells above the unit row down to 80pt above (titles sit further up and outside tables)
        # (row-label headers such as 'Tariff Class' / 'Tariff Name' are merged down through the unit row, so only
        #  require them to START above it; column headers must also END above the data rows)
        hdr_cells = [(b, clean_label(t)) for b, t in cells if b[1] < unit_top - 1 and b[1] >= unit_top - 80 and t]
        col_hdr = [(b, t) for b, t in hdr_cells if b[3] <= unit_bottom + 1]
        columns = []
        for b, t in ucells:
            cx = (b[0] + b[2]) / 2
            labs = [ht for hb, ht in sorted(col_hdr, key=lambda c: c[0][1]) if x_contains(hb, cx)]
            dedup = []
            for l in labs:
                if l not in dedup:
                    dedup.append(l)
            columns.append({"bbox": b, "cx": cx, "unit": norm_unit(clean_label(t)), "raw_header": " | ".join(dedup)})
        # locate class / name columns from header cells
        def find_hdr(pattern):
            for hb, ht in hdr_cells:
                if re.search(pattern, ht, re.I):
                    return hb
            return None
        class_b = find_hdr(class_hdr_re)
        name_b = find_hdr(name_hdr_re)
        # data cells
        data = [(b, t) for b, t in cells if b[1] >= unit_bottom - 1 and b[1] < table_end]
        rows = []
        code_cells = [(b, t) for b, t in data if code_re.match(t.replace("\n", "").strip())]
        for cb, ct in sorted(code_cells, key=lambda c: c[0][1]):
            cy = (cb[1] + cb[3]) / 2
            name = ""
            if name_b is not None:
                for b, t in data:
                    if x_contains(b, (name_b[0] + name_b[2]) / 2) and y_contains(b, cy) and b is not cb:
                        name = clean_label(t)
                        break
            cls = ""
            if class_b is not None:
                for b, t in data:
                    if x_contains(b, (class_b[0] + class_b[2]) / 2) and y_contains(b, cy) and t:
                        cls = clean_label(t)
                        break
            vals = []
            for col in columns:
                v = None
                raw = ""
                for b, t in data:
                    if x_contains(b, col["cx"]) and y_contains(b, cy):
                        raw = t.strip()
                        v = num(t)
                        break
                vals.append((v, raw))
            rows.append({"code": ct.replace("\n", "").strip(), "name": name, "class": cls, "values": vals, "top": cb[1]})
        yield {"columns": columns, "rows": rows, "unit_top": unit_top}


# ----------------------------------------------------------------------------------------------------------------------
# Ausgrid: column vocabulary (label-based keyword mapping of the published header hierarchy)
# ----------------------------------------------------------------------------------------------------------------------
def ausgrid_component(raw_header, unit):
    """Map the published header text of a column to its component label. Returns None for excluded columns.

    raw_header is the header hierarchy top-to-bottom joined with ' | '. The leaf (bottom-most) header is tried
    first because pdfplumber sometimes merges a group header and all its sub-headers into one cell.
    """
    h = raw_header.lower()
    u = unit.lower()
    if "metering" in h:
        return None  # alternative control service (metering) - excluded by contract
    if "/day" in u and "kw" not in u and "kva" not in u:
        if "access" in h:
            return "Network Access Charge"
        raise ValueError(f"unmapped c/day column: {raw_header!r}")
    if "kw/day" in u and "capacity" in h:
        # 'Network Capacity Prices | Peak' in c/kW/day (EA302): the leaf alone ('Peak') would read as a demand price
        return "Network Capacity Prices - Peak"
    leaf = raw_header.split(" | ")[-1]
    if leaf.strip().lower() != h.strip():
        try:
            return _ausgrid_component_kw(leaf, unit)
        except ValueError:
            pass
    return _ausgrid_component_kw(raw_header, unit)


def _ausgrid_component_kw(raw_header, unit):
    h = raw_header.lower()
    u = unit.lower()
    if "kwh" in u:
        if "critical" in h and "minimum" in h:
            return "Network Energy Prices - Critical minimum energy"
        if "critical" in h and "peak" in h:
            return "Network Energy Prices - Critical peak energy"
        if "export" in h and "charge" in h:
            return "Network Energy Prices - " + ("Opt in export charge" if "opt in" in h else "Export charge")
        if "export" in h and "reward" in h:
            return "Network Energy Prices - " + ("Opt in export reward" if "opt in" in h else "Export reward")
        if "dynamic" in h and "minimum" in h:
            return "Network Energy Prices - Dynamic (minimum)"
        if "dynamic" in h and "maximum" in h:
            return "Network Energy Prices - Dynamic (maximum)"
        if "anytime" in h:
            return "Network Energy Prices - Anytime"
        if "off-peak" in h or "off peak" in h:
            return "Network Energy Prices - Off-peak"
        if "shoulder" in h:
            return "Network Energy Prices - Shoulder"
        if "peak" in h:
            return "Network Energy Prices - Peak"
        raise ValueError(f"unmapped c/kWh column: {raw_header!r}")
    if "kw/day" in u:
        if "tuos" in h:
            return "TUOS demand"
        if "high" in h and "season" in h:
            return "Network Demand Prices - High Season"
        if "low" in h and "season" in h:
            return "Network Demand Prices - Low Season"
        if "peak" in h:
            return "Network Demand Prices - Peak"
        raise ValueError(f"unmapped c/kW/day column: {raw_header!r}")
    if "kva/day" in u:
        if "block 1" in h:
            return "Capacity charges - Block 1 Firm capacity"
        if "block 2" in h:
            return "Capacity charges - Block 2 Flexible capacity"
        if "peak" in h:
            return "Network Capacity Prices - Peak"
        raise ValueError(f"unmapped c/kVA/day column: {raw_header!r}")
    raise ValueError(f"unmapped unit {unit!r} ({raw_header!r})")


def ausgrid_2324_component(raw_header, unit):
    h = raw_header.lower()
    u = unit.lower()
    if "/day" in u and "kw" not in u and "kva" not in u:
        if "access" in h:
            return "Network Access Charge"
        raise ValueError(f"unmapped c/day column: {raw_header!r}")
    if "kwh" in u:
        if "non" in h and "tou" in h:
            return "Energy consumption charge - Non-TOU"
        if "shoulder" in h:
            return "Energy consumption charge - Shoulder"
        if "off" in h:
            return "Energy consumption charge - Off-peak"
        if "peak" in h:
            return "Energy consumption charge - Peak"
        raise ValueError(f"unmapped c/kWh column: {raw_header!r}")
    if "kw/day" in u:
        if "capacity" in h:
            return "Capacity charge - Peak"  # 'Capacity charge | Peak' printed in c/kW/day (EA302, EA316)
        if "high" in h:
            return "Demand charge - High season"
        if "low" in h:
            return "Demand charge - Low season"
        if "peak" in h:
            return "Demand charge - Peak"
        raise ValueError(f"unmapped c/kW/day column: {raw_header!r}")
    if "kva/day" in u:
        return "Capacity charge - Peak"
    raise ValueError(f"unmapped unit {unit!r} ({raw_header!r})")


# ----------------------------------------------------------------------------------------------------------------------
# Ausgrid Network Price List (DNSP, 2024-25 .. 2026-27)
# ----------------------------------------------------------------------------------------------------------------------
AUSGRID_CODE_RE = re.compile(r"^EA\d{3}\*?$")


def parse_ausgrid_price_list(dist, fy, side, path, url):
    rows = []
    incl = {}  # (code, component) -> value from GST-inclusive pages, cross-checked against the exclusive pages
    with pdfplumber.open(os.path.join(ROOT, path)) as pdf:
        meta = pdf.metadata or {}
        created = (meta.get("CreationDate") or meta.get("ModDate") or "")
        m = re.search(r"D:(\d{4})(\d{2})(\d{2})", created)
        pdf_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if "Prices exclude GST" in text:
                gst = "excl"
            elif "Prices include GST" in text:
                gst = "incl"
            else:
                warn(f"{path} p{pno}: GST statement not found; assuming excl")
                gst = "excl"
            # footnotes keyed by marker
            footnotes = {}
            for line in text.split("\n"):
                mm = re.match(r"^([*&^%])\s*(.+)$", line.strip())
                if mm:
                    footnotes[mm.group(1)] = mm.group(2).strip()
            for tbl in grid_tables(page, AUSGRID_CODE_RE, name_hdr_re=r"Tariff Name"):
                is_trial_tbl = any(r["code"] >= "EA900" for r in tbl["rows"])
                comps = []
                for c in tbl["columns"]:
                    comp = ausgrid_component(c["raw_header"], c["unit"])
                    comps.append(comp)
                    if DEBUG:
                        print(f"  [{fy} p{pno} gst={gst}] {c['unit']:<10} <- {c['raw_header']!r} => {comp}")
                for r in tbl["rows"]:
                    code = r["code"]
                    marker = "*" if code.endswith("*") else ""
                    code = code.rstrip("*")
                    name = r["name"]
                    if name.endswith("*"):
                        name = name.rstrip("*").strip()
                        marker = "*"
                    notes = [f"Network Price List {fy} p{pno}", "GST exclusive" if gst == "excl" else "GST inclusive",
                             "network price = NUOS (list shows no DUOS/TUOS/CCF split; excludes the separate Metering Service Charge column)"]
                    if pdf_date:
                        notes.append(f"PDF dated {pdf_date}")
                    if "storage" in name.lower():
                        notes.append("storage tariff")
                    if is_trial_tbl:
                        notes.append("trial tariff (partner retailers only)")
                    if re.search(r"\bclosed\b", name, re.I):
                        notes.append("closed to new customers")
                    if marker and footnotes.get("*"):
                        notes.append("footnote: " + footnotes["*"])
                    for mk, fn in footnotes.items():
                        if mk != "*" and code in fn:
                            notes.append("footnote: " + fn)
                    for (v, raw), comp, col in zip(r["values"], comps, tbl["columns"]):
                        if v is not None and comp is None and "metering" in col["raw_header"].lower():
                            METERING.append({
                                "distributor": dist, "fin_year": fy, "tariff_code": code, "meter_class": name,
                                "component": header_text(col["raw_header"]), "unit": col["unit"],
                                "value": raw.replace("\n", "").replace(",", "").rstrip("*").strip(), "gst": gst,
                                "source_file": path, "locator": locators.pdf(pno),
                                "note": f"Network Price List {fy} p{pno}"
                                        + (f"; footnote: {footnotes['*']}" if raw.endswith("*") and footnotes.get("*") else "")})
                            continue
                        if v is None or comp is None:
                            if raw and comp is not None and raw not in ("", "-"):
                                warn(f"{path} p{pno} {code} {comp}: unparsed cell {raw!r}")
                            continue
                        if gst == "incl":
                            incl[(code, comp)] = v
                        note = "; ".join(notes)
                        if comp == "TUOS demand":
                            note += "; TUOS demand component of the NUOS storage tariff"
                        if raw.endswith("*") and footnotes.get("*"):
                            note += "; footnote: " + footnotes["*"]
                        rows.append(make_row(dist, fy, side, path, url, code, name, r["class"], comp, col["unit"], raw.replace("\n", "").replace(",", "").rstrip("*").strip(),
                                             "NUoS", gst, note, locators.pdf(pno)))
    # the GST-inclusive table repeats the exclusive one, but some years (2026-27 p2) draw its class column as one cell
    # per row with the class printed once per group: take the class the exclusive table prints for the code
    excl_class = {r["tariff_code"]: r["customer_class"] for r in rows if r["gst"] == "excl" and r["customer_class"]}
    for r in rows:
        if r["gst"] == "incl" and not r["customer_class"] and r["tariff_code"] in excl_class:
            r["customer_class"] = excl_class[r["tariff_code"]]
            r["note"] += "; tariff class as printed in the GST-exclusive table (this table prints it once per group)"
    # validate GST-inclusive pages against exclusive rows (incl = excl * 1.1, 4dp)
    checked = 0
    for r in rows:
        if r["gst"] != "excl":
            continue
        key = (r["tariff_code"], r["component"])
        if key in incl:
            checked += 1
            ex = float(r["value"])
            if abs(float(incl[key]) - round(ex * 1.1, 4)) > 0.00051:
                warn(f"{path}: GST check {key}: excl {ex} *1.1 != incl {incl[key]}")
    if DEBUG:
        print(f"  {path}: {len(rows)} rows, {checked} incl-GST cross-checks")
    return rows


# ----------------------------------------------------------------------------------------------------------------------
# Ausgrid 2023-24 annual pricing proposal (AER hosted): Tables 4.1 - 4.4
# ----------------------------------------------------------------------------------------------------------------------
AUSGRID_2324_BASIS = [
    (r"network use of system \(NUOS\)", "NUoS"),
    (r"distribution use of system \(DUOS\)", "DUoS"),
    (r"transmission use of system \(TUOS\)", "TUoS"),
    (r"jurisdictional scheme components", "JSA"),
]


def parse_ausgrid_proposal_2324(dist, fy, side, path, url):
    rows = []
    found = set()
    with pdfplumber.open(os.path.join(ROOT, path)) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            m = re.search(r"Table (4\.\d)\. Ausgrid’s (.+?) by charging parameter from 1 July 2023 \((exclusive|inclusive) of GST\)", text)
            if not m:
                continue
            table_no, what, gst_word = m.groups()
            basis = next((b for pat, b in AUSGRID_2324_BASIS if re.search(pat, what)), None)
            if basis is None:
                warn(f"{path} p{pno}: unrecognised table {what!r}")
                continue
            found.add(basis)
            gst = "excl" if gst_word == "exclusive" else "incl"
            for tbl in grid_tables(page, re.compile(r"^EA\d{3}$")):
                comps = []
                for c in tbl["columns"]:
                    comp = ausgrid_2324_component(c["raw_header"], c["unit"])
                    comps.append(comp)
                    if DEBUG:
                        print(f"  [2023-24 Table {table_no} {basis}] {c['unit']:<10} <- {c['raw_header']!r} => {comp}")
                for r in tbl["rows"]:
                    name = r["name"]
                    notes = [f"proposed (pre-approval) prices; Pricing Proposal Table {table_no} p{pno}"]
                    if basis == "JSA":
                        notes.append("jurisdictional scheme components (NSW Climate Change Fund etc.)")
                    if re.search(r"\bclosed\b", name, re.I):
                        notes.append("closed to new customers")
                    for (v, raw), comp, col in zip(r["values"], comps, tbl["columns"]):
                        if v is None:
                            if raw:
                                warn(f"{path} p{pno} {r['code']} {comp}: unparsed cell {raw!r}")
                            continue
                        rows.append(make_row(dist, fy, side, path, url, r["code"], name, r["class"], comp, col["unit"], raw.replace("\n", "").replace(",", "").rstrip("*").strip(),
                                             basis, gst, "; ".join(notes), locators.pdf(pno)))
    if found != {"NUoS", "DUoS", "TUoS", "JSA"}:
        warn(f"{path}: expected 4 tables, found {found}")
    # consistency check: NUOS = DUOS + TUOS + JSA
    by = {}
    for r in rows:
        by.setdefault((r["tariff_code"], r["component"]), {})[r["basis"]] = float(r["value"])
    bad = 0
    for k, d in by.items():
        if "NUoS" in d and all(b in d for b in ("DUoS", "TUoS", "JSA")):
            if abs(d["NUoS"] - (d["DUoS"] + d["TUoS"] + d["JSA"])) > 0.0002:
                bad += 1
                warn(f"{path}: NUOS != DUOS+TUOS+JSA for {k}: {d}")
    if DEBUG:
        print(f"  {path}: {len(rows)} rows; additive check failures: {bad}")
    return rows


# ----------------------------------------------------------------------------------------------------------------------
# Endeavour Energy NUOS Price List (DNSP, 2024-25 .. 2026-27)
# ----------------------------------------------------------------------------------------------------------------------
ENDEAVOUR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{1,4}$")


def endeavour_table_columns(table):
    """Return (unit_row_index, [(col_index, label, unit)]) from an extract_tables() grid with hierarchical header."""
    urow = None
    for i, row in enumerate(table[:8]):
        if sum(1 for c in row if c and UNIT_RE.match(c.strip())) >= 3:
            urow = i
            break
    if urow is None:
        return None, []
    hdr_rows = table[:urow]
    cols = []
    for j, u in enumerate(table[urow]):
        if not u or not UNIT_RE.match(u.strip()):
            continue
        parts = []
        for hr in hdr_rows:
            # None = spanned by the previous non-None cell in this row (horizontal merge); '' = empty cell
            k = j
            while k >= 0 and hr[k] is None:
                k -= 1
            lab = clean_label(hr[k]) if k >= 0 and hr[k] else ""
            if lab and not lab.startswith("Table ") and lab not in parts:
                parts.append(lab)
        cols.append((j, " - ".join(parts), norm_unit(u.strip())))
    # disambiguate duplicate labels (e.g. High Season Demand in c/kW/day and c/kVA/day) with the unit
    labels = [c[1] for c in cols]
    cols = [(j, (f"{lab} ({u})" if labels.count(lab) > 1 else lab), u) for j, lab, u in cols]
    return urow, cols


TABLE_2_COLUMNS = [("Daily Access Charge", "c/day", "excl"), ("Daily Access Charge", "c/day", "incl"),
                   ("Energy Charge - Flat", "c/kWh", "excl"), ("Energy Charge - Flat", "c/kWh", "incl")]


def endeavour_table_2(path, page, pno, tables):
    """[(code, name, [access excl, access incl, energy excl, energy incl], locator)] of Table 2. The 2026-27 list
    draws this table with horizontally scaled glyphs that extract as fragments, so the page is read by OCR then."""
    out = []
    if tables:
        table = max(tables, key=len)
        hdr = next((i for i, r in enumerate(table) if r and r[0] == "NTC"), None)
        for r in table[hdr + 1:] if hdr is not None else []:
            if r and r[0] and ENDEAVOUR_CODE_RE.match(r[0].strip()):
                vals = [num(c) for c in r[2:6]]
                if None not in vals:
                    out.append((r[0].strip(), clean_label(r[1] or ""), vals, locators.pdf(pno)))
        return out
    text = locators.ocr_text(os.path.join(ROOT, path), pno)
    n = r"(-?\d+\.\d+)"
    for m in re.finditer(rf"\b(N99|ENSL|ENTL|ENNW)\s+(.+?)\s+{n}\s+{n}\s+{n}\s+{n}", text):
        out.append((m.group(1), m.group(2).strip(), [Decimal(m.group(k)) for k in range(3, 7)], locators.pdf(pno, ocr=True)))
    return out


def endeavour_words_table(page, cols):
    """(grid, grey) for a price table that find_tables() cannot see: row 0 is a stand-in for the unit row, then one
    row per tariff code with each number placed in the column whose unit word (c/day, c/kWh, ...) it sits under;
    grey[(row, column)] is True for a light-grey placeholder number."""
    words = page.extract_words(x_tolerance=1.5, extra_attrs=["non_stroking_color"])
    units = sorted((w for w in words if UNIT_RE.match(w["text"])), key=lambda w: w["x0"])
    if not units or len(units) != len(cols):
        return [], {}
    unit_top = units[0]["top"]
    centre = {j: (w["x0"] + w["x1"]) / 2 for (j, _, _), w in zip(sorted(cols), units)}
    width = max(centre) + 1
    grid, grey = [[None] * width], {}
    # rows end at the notes, whose sentences also start with a tariff code ('N89 is a Transitional Network Tariff
    # applicable to ... > 160 MWh')
    notes_top = min((w["top"] for w in words if w["text"] == "IMPORTANT" and w["top"] > unit_top), default=page.height)
    for code_w in sorted((w for w in words if unit_top + 3 < w["top"] < notes_top and w["x0"] < 45
                          and ENDEAVOUR_CODE_RE.match(w["text"])), key=lambda w: w["top"]):
        line = sorted((w for w in words if abs(w["top"] - code_w["top"]) < 3 and w is not code_w), key=lambda w: w["x0"])
        row = [None] * width
        row[0] = code_w["text"]
        row[1] = " ".join(w["text"] for w in line if w["x1"] < min(centre.values()) - 15)
        for w in line:
            if num(w["text"]) is None:
                continue
            j, d = min(((j, abs((w["x0"] + w["x1"]) / 2 - c)) for j, c in centre.items()), key=lambda t: t[1])
            if d <= 18:
                row[j] = w["text"]
                grey[(len(grid), j)] = tuple(w["non_stroking_color"] or ()) == (0.949,)
        grid.append(row)
    return grid, grey


def placeholder_cell(page, bbox):
    """True when every digit inside a table cell is printed in near-white grey (0.949): the price lists fill
    non-applicable cells of the dense Table 3 grids with such invisible 0.0000 placeholders."""
    if bbox is None:
        return False
    digits = [c for c in page.within_bbox(bbox).chars if c["text"].isdigit()]
    return bool(digits) and all(tuple(c["non_stroking_color"] or ()) == (0.949,) for c in digits)


def endeavour_version(pdf):
    first = pdf.pages[0].extract_text() or ""
    m = re.search(r"Version\s+(\d+\.\d+)", first)
    ver = m.group(1) if m else ""
    hist = []
    for p in pdf.pages[1:3]:
        t = p.extract_text() or ""
        for line in t.split("\n"):
            mm = re.match(r"^(\d+\.\d+)\s+(\d{1,2} \w{3} \d{4})\s+(.*)$", line.strip())
            if mm:
                hist.append(f"v{mm.group(1)} {mm.group(2)} {mm.group(3).strip()}")
    return ver, "; ".join(hist)


def parse_endeavour_price_list(dist, fy, side, path, url):
    rows = []
    incl = {}
    excl_vals = {}
    table2 = []
    layouts = {}  # "1a"/"3a" -> columns of the GST-exclusive table
    with pdfplumber.open(os.path.join(ROOT, path)) as pdf:
        ver, hist = endeavour_version(pdf)
        vnote = f"NUOS Price List {fy} v{ver}" + (f" ({hist})" if hist else "")
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if "TABLE OF CONTENTS" in text or not re.search(r"Table [123][ab]? [–-]", text):
                continue
            m = re.search(r"10\.\d\.\s+Table (1a|1b|2|3a|3b)\s*[–-]\s*([^\n]*?)\s*(?:\((exclusive|inclusive) GST\))?\s*(?:Prices effective[^\n]*)?$",
                          text, re.M)
            if not m:
                continue
            tno, gst_word = m.group(1), m.group(3)
            found = page.find_tables()
            tables = [t.extract() for t in found]
            if tno == "2":
                # unmetered options: the Table 1a prices again, with excl and incl GST columns (repeated printing)
                t2 = endeavour_table_2(path, page, pno, tables)
                if not t2:
                    warn(f"{path} p{pno}: Table 2 (unmetered) not extractable")
                for code, name, vals, locator in t2:
                    t2_rows = []
                    for (lab, u, g), v in zip(TABLE_2_COLUMNS, vals):
                        t2_rows.append(make_row(dist, fy, side, path, url, code, name, "", lab, u, v, "NUoS", g,
                                                f"{vnote}; Table 2 p{pno} (Unmetered Pricing Options - NUOS); "
                                                f"{'GST exclusive' if g == 'excl' else 'GST inclusive'}", locator))
                    rows += t2_rows
                    table2.append((code, vals, t2_rows))
                continue
            if tables:
                ti = max(range(len(tables)), key=lambda i: len(tables[i]))
                table, cell_boxes = tables[ti], found[ti].rows
                urow, cols = endeavour_table_columns(table)

                def placeholder(ri, j):
                    return placeholder_cell(page, cell_boxes[ri].cells[j])
            elif tno.endswith("b") and tno[0] + "a" in layouts:
                # the 2026-27 Table 1b has no ruling lines for find_tables(): read its words against the columns of
                # the matching GST-exclusive table, which the list prints in the same layout
                cols = layouts[tno[0] + "a"]
                table, grey = endeavour_words_table(page, cols)
                urow = 0 if table else None

                def placeholder(ri, j):
                    return grey.get((ri, j), False)
            else:
                table = None
            if not table:
                warn(f"{path} p{pno}: Table {tno} not extractable")
                continue
            notes_text = ""
            for row in table:
                if row and row[0] and "IMPORTANT NOTES" in row[0]:
                    notes_text = row[0]
            if not notes_text and "IMPORTANT NOTES" in text:
                notes_text = text[text.index("IMPORTANT NOTES"):]
            gst = "excl" if (gst_word == "exclusive" or "GST exclusive" in (table[0][0] or "")) else (
                "incl" if (gst_word == "inclusive" or "GST inclusive" in (table[0][0] or "")) else None)
            if gst is None:
                warn(f"{path} p{pno}: GST basis for Table {tno} not stated; assuming excl")
                gst = "excl"
            if urow is None:
                warn(f"{path} p{pno}: no unit row in Table {tno}")
                continue
            if tno.endswith("a"):
                layouts[tno] = cols
            if DEBUG:
                for j, lab, u in cols:
                    print(f"  [{fy} Table {tno} p{pno} gst={gst}] col{j} {u:<10} {lab}")
            n89 = re.search(r"N89 is a Transitional Network Tariff[^\n]*", notes_text)
            comp_note = re.search(r"Network prices comprise[^\n]*", notes_text)
            comp_quote = comp_note.group(0).strip() if comp_note else ""
            for ri, r in enumerate(table[urow + 1:], start=urow + 1):
                if not r or not r[0] or not ENDEAVOUR_CODE_RE.match(r[0].strip()):
                    continue
                code, name = r[0].strip(), clean_label(r[1] or "")
                cells = []
                for j, lab, u in cols:
                    raw = (r[j] or "").strip() if j < len(r) else ""
                    v = num(raw)
                    if v is None:
                        if raw:
                            warn(f"{path} p{pno} {code} {lab}: unparsed cell {raw!r}")
                        continue
                    cells.append((lab, u, v, j))
                if not cells:
                    continue
                if tno.startswith("3"):
                    # dense grid: 0.0000 in light grey (non-printing placeholder) in every non-applicable cell -> keep
                    # priced cells, the access charge and zeros printed in black ('Export - Energy - All Time')
                    cells = [c for c in cells if c[2] != 0 or c[0] == "Daily Access Charge"
                             or not placeholder(ri, c[3])]
                if all(v == 0 for _, _, v, _ in cells) and all(lab.endswith("All Time") for lab, _, _, _ in cells):
                    # NESN/NESG/GENR, NFT3/NFT4/NFIT/NFT2: generation-measurement codes priced only by a printed
                    # 0.0000 all-time export rate; footnote (1): no export charge or reward
                    notes_gen = "generation-measurement code: only a 0.0000 all-time export rate is printed"
                else:
                    notes_gen = ""
                notes = [vnote, f"Table {tno} p{pno}", "GST exclusive"]
                if tno.startswith("3"):
                    notes.append("OBSOLETE pricing option (Table 3a: legacy combination codes = standard tariff + controlled load "
                                 "and/or gross/net generation metering variants, priced as the parent tariff); 'obsolete and no new "
                                 "customers will be added ... not available on application'; placeholder 0.0000 cells dropped")
                if code == "N89" and n89:
                    notes.append(n89.group(0))
                if "Transitional" in name:
                    notes.append("transitional tariff")
                if code in ("ENSL", "ENTL", "ENNW"):
                    notes.append(f"published as {code}; the 2023-24 proposal and AER files list this tariff as {code[2:]}")
                if notes_gen:
                    notes.append(notes_gen)
                if gst == "incl":
                    notes[2] = "GST inclusive"
                for lab, u, v, _ in cells:
                    if gst == "incl":
                        incl[(code, lab)] = v
                    else:
                        excl_vals[(code, lab)] = v
                    note = "; ".join(notes)
                    if lab == "Daily Access Charge":
                        note += ("; Daily Access Charge is the NUOS network access charge incl. metering - document: \""
                                 + (comp_quote or "Network prices comprise Distribution (DUOS) charges including Metering, "
                                    "Transmission (TUOS) passthroughs and recovery of the NSW CCF and EIR contributions")
                                 + "\"; no metering/network split published")
                    rows.append(make_row(dist, fy, side, path, url, code, name, "", lab, u, v, "NUoS", gst, note, locators.pdf(pno)))
    for key, v in incl.items():
        if key in excl_vals and abs(v - round(excl_vals[key] * Decimal("1.1"), 4)) > 0.00051:
            warn(f"{path}: GST check {key}: excl {excl_vals[key]} *1.1 != incl {v}")
    # Table 2 reprints the Table 1a (GST exclusive) and Table 1b (GST inclusive) access and block-1 energy prices: a
    # reprint that matches is marked as a repeated printing, so the reconciliation compares the price once
    for code, vals, t2_rows in table2:
        for g, printed, first in (("excl", excl_vals, (vals[0], vals[2])), ("incl", incl, (vals[1], vals[3]))):
            table1 = "Table 1a" if g == "excl" else "Table 1b"
            orig = tuple(printed.get((code, lab))
                         for lab in ("Daily Access Charge", "Import - Energy Charges - Block 1"))
            if orig != first:
                warn(f"{path}: Table 2 {code} {g} {first} differs from {table1} {orig}")
                continue
            for r in t2_rows:
                if r["gst"] == g:
                    r["note"] += f"; {schema.REPEATED_PRINTING} of the {table1} prices (identical)"
    if DEBUG:
        print(f"  {path}: {len(rows)} rows, {len(incl)} incl-GST cross-checks, {len(table2)} Table 2 cross-checks")
    return rows


# ----------------------------------------------------------------------------------------------------------------------
# Endeavour Energy 2023-24 Pricing Proposal (AER hosted): Proposed Network / Distribution / DPPC / JSA Prices - FY24
# ----------------------------------------------------------------------------------------------------------------------
ENDEAVOUR_2324_BASIS = {"Network": "NUoS", "Distribution": "DUoS", "DPPC": "DPPC", "JSA": "JSA"}
ENDEAVOUR_2324_CODE_RE = re.compile(r"^(N[0-9A-Z]{2,3}|SL|TL|NW)$")


def header_labels_by_anchor(words, anchors, top_lo, top_hi, pad=20):
    """Header words whose x-centre lies within `pad` of an anchor's centre, joined top-to-bottom."""
    labels = []
    for a in anchors:
        cx = (a["x0"] + a["x1"]) / 2
        lab = [w for w in words if top_lo <= w["top"] < top_hi and abs((w["x0"] + w["x1"]) / 2 - cx) <= pad]
        lab.sort(key=lambda w: (round(w["top"]), w["x0"]))
        labels.append(clean_label(" ".join(w["text"] for w in lab)))
    return labels


def parse_endeavour_proposal_2324(dist, fy, side, path, url):
    grids = {}  # basis -> {code: (name, [values])}
    columns = None
    with pdfplumber.open(os.path.join(ROOT, path)) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            m = re.search(r"^Proposed (Network|Distribution|DPPC|JSA) Prices - FY24\s*$", text, re.M)
            if not m:
                continue
            basis = ENDEAVOUR_2324_BASIS[m.group(1)]
            gst = "excl" if "All prices ex GST" in text else None
            if gst is None:
                warn(f"{path} p{pno}: GST statement missing; assuming excl")
                gst = "excl"
            words = page.extract_words(x_tolerance=1.5, y_tolerance=1)
            anchors = sorted([w for w in words if UNIT_RE.match(w["text"])], key=lambda w: w["x0"])
            unit_top = anchors[0]["top"]
            labels = header_labels_by_anchor(words, anchors, unit_top - 40, unit_top - 1)
            cols = []
            for a, lab in zip(anchors, labels):
                cols.append((lab, norm_unit(a["text"])))
            labs = [c[0] for c in cols]
            cols = [((f"{lab} ({u})" if labs.count(lab) > 1 else lab), u) for lab, u in cols]
            if DEBUG:
                for lab, u in cols:
                    print(f"  [2023-24 Endeavour {basis} p{pno}] {u:<10} {lab}")
            if columns is None:
                columns = cols
            elif [c[0] for c in cols] != [c[0] for c in columns]:
                warn(f"{path} p{pno}: column headers differ from first table: {cols}")
            grid = {}
            for line in text.split("\n"):
                toks = line.split()
                nums = []
                while toks and NUM_RE.match(toks[-1]):
                    nums.insert(0, toks.pop())
                if len(nums) != len(anchors) or not toks:
                    continue
                code = toks[-1]
                if not ENDEAVOUR_2324_CODE_RE.match(code):
                    warn(f"{path} p{pno}: row with {len(nums)} numbers but odd code {code!r}: {line[:60]}")
                    continue
                name = " ".join(toks[:-1])
                grid[code] = (name, [num(x) for x in nums], pno, gst)
            grids[basis] = grid
    if set(grids) != {"NUoS", "DUoS", "DPPC", "JSA"}:
        warn(f"{path}: expected 4 price tables, found {sorted(grids)}")
    # Which cells are real components? The proposal prints a dense grid with 0.0000 in every non-applicable cell.
    # Keep a (code, column) if it is non-zero in ANY basis table, and always keep the Fixed charge.
    keep = set()
    for basis, grid in grids.items():
        for code, (name, vals, pno, gst) in grid.items():
            for j, v in enumerate(vals):
                if v is not None and (v != 0 or columns[j][0].lower().startswith("fixed")):
                    keep.add((code, j))
    rows = []
    order = ["NUoS", "DUoS", "DPPC", "JSA"]
    ref = grids.get("NUoS", {})
    for basis in order:
        grid = grids.get(basis, {})
        for code, (name, vals, pno, gst) in grid.items():
            for j, v in enumerate(vals):
                if (code, j) not in keep or v is None:
                    continue
                lab, u = columns[j]
                notes = [f"proposed (pre-approval) prices; Pricing Proposal 'Proposed {[k for k, b in ENDEAVOUR_2324_BASIS.items() if b == basis][0]} Prices - FY24' p{pno}",
                         "dense grid: 0.0000 placeholder cells dropped unless non-zero in some basis table"]
                if basis == "DPPC":
                    notes.append("DPPC = designated pricing proposal charges (transmission/TUOS passthrough)")
                if basis == "JSA":
                    notes.append("jurisdictional scheme amounts (NSW CCF etc.)")
                if name.lower().startswith("obsolete"):
                    notes.append("obsolete tariff")
                if "Transitional" in name:
                    notes.append("transitional tariff")
                rows.append(make_row(dist, fy, side, path, url, code, name, "", lab, u, v, basis, gst, "; ".join(notes),
                                     locators.pdf(pno)))
    # additive check
    bad = 0
    for code, (name, vals, pno, gst) in ref.items():
        for j, v in enumerate(vals):
            if (code, j) in keep:
                parts = [grids[b][code][1][j] for b in ("DUoS", "DPPC", "JSA") if code in grids.get(b, {})]
                if len(parts) == 3 and abs(v - sum(parts)) > 0.0002 + (0.006 if columns[j][1] == "c/day" else 0):
                    bad += 1
                    warn(f"{path}: NUOS != DUOS+DPPC+JSA for {code} {columns[j][0]}: {v} vs {parts}")
    if DEBUG:
        print(f"  {path}: {len(rows)} rows; additive check failures: {bad}")
    return rows


# ----------------------------------------------------------------------------------------------------------------------
def main():
    inv = load_inventory()
    all_rows = []
    for dist, fy, side, path in FILES:
        url = inv.get(path)
        if url is None:
            warn(f"{path}: no inventory row -> source_url blank")
            url = ""
        if dist == "Ausgrid" and side == "AER_HOSTED":
            rows = parse_ausgrid_proposal_2324(dist, fy, side, path, url)
        elif dist == "Ausgrid":
            rows = parse_ausgrid_price_list(dist, fy, side, path, url)
        elif side == "AER_HOSTED":
            rows = parse_endeavour_proposal_2324(dist, fy, side, path, url)
        else:
            rows = parse_endeavour_price_list(dist, fy, side, path, url)
        codes = sorted({r["tariff_code"] for r in rows})
        bases = sorted({r["basis"] for r in rows})
        print(f"{dist:17s} {fy} {side:10s} rows={len(rows):4d} codes={len(codes):3d} bases={'/'.join(bases)} gst={'/'.join(sorted({r['gst'] for r in rows}))}")
        all_rows.extend(rows)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=schema.COLUMNS)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in schema.COLUMNS})
    print(f"wrote {len(all_rows)} rows -> {os.path.relpath(OUT, ROOT)}")
    schema.write_metering("ausgrid_endeavour", METERING)
    print(f"wrote {len(METERING)} metering cells -> {schema.METERING_OUT_DIR}/ausgrid_endeavour.csv")
    for sk in SKIPPED:
        print("skipped:", sk)
    if WARNINGS:
        print(f"{len(WARNINGS)} warning(s)")


if __name__ == "__main__":
    main()
