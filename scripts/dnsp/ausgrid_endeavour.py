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
    incl = {}  # (code, component) -> value from GST-inclusive pages, for validation only
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
                    notes = [f"Network Price List {fy} p{pno}", "GST exclusive",
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
                        if v is None or comp is None:
                            if raw and comp is not None and raw not in ("", "-"):
                                warn(f"{path} p{pno} {code} {comp}: unparsed cell {raw!r}")
                            continue
                        if gst == "incl":
                            incl[(code, comp)] = v
                            continue
                        note = "; ".join(notes)
                        if comp == "TUOS demand":
                            note += "; TUOS demand component of the NUOS storage tariff"
                        if raw.endswith("*") and footnotes.get("*"):
                            note += "; footnote: " + footnotes["*"]
                        rows.append(make_row(dist, fy, side, path, url, code, name, r["class"], comp, col["unit"], raw.replace("\n", "").replace(",", "").rstrip("*").strip(),
                                             "NUoS", gst, note, locators.pdf(pno)))
    # validate GST-inclusive pages against exclusive rows (incl = excl * 1.1, 4dp)
    checked = 0
    for r in rows:
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
            tno, title, gst_word = m.group(1), m.group(2).strip(), m.group(3)
            tables = page.extract_tables()
            if not tables:
                if tno == "2":
                    warn(f"{path} p{pno}: Table 2 (unmetered) not extractable (rotated/vector text); values duplicate Table 1a")
                else:
                    warn(f"{path} p{pno}: Table {tno} not extractable")
                continue
            table = max(tables, key=len)
            notes_text = ""
            for row in table:
                if row and row[0] and "IMPORTANT NOTES" in row[0]:
                    notes_text = row[0]
            if tno == "2":
                # unmetered options with excl/incl columns; same codes/values as Table 1a -> validate only
                hdr = next((i for i, r in enumerate(table) if r and r[0] == "NTC"), None)
                if hdr is not None:
                    for r in table[hdr + 1:]:
                        if r and r[0] and ENDEAVOUR_CODE_RE.match(r[0]):
                            table2.append((r[0], num(r[2]), num(r[4])))
                continue
            gst = "excl" if (gst_word == "exclusive" or "GST exclusive" in (table[0][0] or "")) else (
                "incl" if (gst_word == "inclusive" or "GST inclusive" in (table[0][0] or "")) else None)
            if gst is None:
                warn(f"{path} p{pno}: GST basis for Table {tno} not stated; assuming excl")
                gst = "excl"
            urow, cols = endeavour_table_columns(table)
            if urow is None:
                warn(f"{path} p{pno}: no unit row in Table {tno}")
                continue
            if DEBUG:
                for j, lab, u in cols:
                    print(f"  [{fy} Table {tno} p{pno} gst={gst}] col{j} {u:<10} {lab}")
            n89 = re.search(r"N89 is a Transitional Network Tariff[^\n]*", notes_text)
            comp_note = re.search(r"Network prices comprise[^\n]*", notes_text)
            comp_quote = comp_note.group(0).strip() if comp_note else ""
            for r in table[urow + 1:]:
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
                    cells.append((lab, u, v))
                if not cells:
                    continue
                if all(v == 0 for _, _, v in cells) and all(lab.endswith("All Time") for lab, _, _ in cells):
                    # NESN/NESG/GENR, NFT3/NFT4/NFIT/NFT2: generation-measurement codes, "no export charge or reward"
                    if gst == "excl":
                        SKIPPED.append(f"{fy} {code} {name} (Table {tno}: generation-measurement code, 0.0000 export only)")
                    continue
                if tno.startswith("3"):
                    # dense grid: 0.0000 printed in every non-applicable cell -> keep priced cells + the access charge
                    cells = [c for c in cells if c[2] != 0 or c[0] == "Daily Access Charge"]
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
                for lab, u, v in cells:
                    if gst == "incl":
                        incl[(code, lab)] = v
                        continue
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
    for code, acc, en in table2:
        k1 = (code, "Daily Access Charge")
        k2 = (code, "Import - Energy Charges - Block 1")
        if excl_vals.get(k1) != acc or excl_vals.get(k2) != en:
            warn(f"{path}: Table 2 {code} ({acc}, {en}) differs from Table 1a ({excl_vals.get(k1)}, {excl_vals.get(k2)})")
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
    for sk in SKIPPED:
        print("skipped:", sk)
    if WARNINGS:
        print(f"{len(WARNINGS)} warning(s)")


if __name__ == "__main__":
    main()
