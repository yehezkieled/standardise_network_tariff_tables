#!/usr/bin/env python
"""Ausgrid (EnergyAustralia until 2011) and Endeavour Energy (Integral Energy until 2011) network price lists for the
pricing years before 2023-24, read into out/history/ausgrid_endeavour.csv (contract: scripts/history/CONTRACT.md).

One document per distributor-year: the distributor's own published network price list (every year from 2004-05 /
2005-06 to 2022-23 has one; see YEARS). Layouts:

  Ausgrid    one price table per list, printed GST exclusive and again GST inclusive (the exclusive page is read).
             Columns are found from the printed unit row (c/day, c/kWh, $/mth, ...) and the DLF heading; each layout
             is named by its unit sequence and checked against its printed column headings (AUSGRID_LAYOUTS).
             From 2015-16 the table also prints a per-tariff Metering Service Charge (non capital, capital): written to
             the metering side output.
  Endeavour  Appendix 1 "Network Price Tables" (Table 1 standard, combination, unmetered, small generation, Solar Bonus
             Scheme, obsolete), every price printed as an "Excl. GST" / "Incl. GST" pair. Ruled tables read with
             pdfplumber's table finder; each Excl. GST column is labelled by the heading cells above it. 2014-15 and
             2015-16 print the tables as images: read by OCR (rapidocr, as scripts/tariffdb/locators.py re-reads them)
             with the column headings taken from the same table of the year before or after, every OCR value checked
             against its printed GST-inclusive twin (incl = excl x 1.1).

Run from the repository root:  .venv/bin/python scripts/history/ausgrid_endeavour.py
"""
import os
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402
sys.path.insert(0, os.path.join(common.ROOT, "scripts", "tariffdb"))
import locators  # noqa: E402
import schema  # noqa: E402

SLUG = "ausgrid_endeavour"
ARCHIVE = "sources/archive"

# the distributor's own published network price list of each pricing year (the version customers were billed on)
AUSGRID = {
    "2004-05": "2004-05/NetworkPricelistJuly04.pdf",
    "2005-06": "2005-06/Network_Price_List_2005_06v4.pdf",
    "2006-07": "2006-07/Network_Price_List_FY07.pdf",
    "2007-08": "2007-08/Network_Price_List_FY08.pdf",
    "2008-09": "2008-09/FinalNetworkPricelist20082009290508.pdf",
    "2009-10": "2009-10/20090706NetworkPricelist200910.pdf",
    "2010-11": "2010-11/20100621NetworkPricelist201011.pdf",
    "2011-12": "2011-12/Ausgrid_Network_Price_List_FY12.pdf",
    "2012-13": "2012-13/Network_Price_List_FY13_Updated_Sept12.pdf",
    "2013-14": "2013-14/Ausgrid_Network_Price_List_FY201314.pdf",
    "2014-15": "2014-15/Network_Price_List_FY2014-15.pdf",
    "2015-16": "2015-16/Ausgrid-Network-Price-List-2015_16.pdf",
    "2016-17": "2016-17/AUSGRID-NETWORK-PRICE-LIST-FY201617.pdf",
    "2017-18": "2017-18/AUSGRID-NETWORK-PRICE-LIST-FY201718.pdf",
    "2018-19": "2018-19/Ausgrid-Network-Price-List-FY-201819.pdf",
    "2019-20": "2019-20/Ausgrid-Network-Price-List-FY201920.pdf",
    "2020-21": "2020-21/AUSGRID-NETWORK-PRICE-LIST-FY2020-21.pdf",
    "2021-22": "2021-22/AUSGRID-NETWORK-PRICE-LIST-FY2021-22.pdf",
    "2022-23": "2022-23/Ausgrid-Network-Price-List-2022-23.pdf",
}

WARNINGS = []


def warn(msg):
    WARNINGS.append(msg)
    print("WARN:", msg, file=sys.stderr)


DEC_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})*\.\d+$|^-?\d+\.\d+$")


def clean_value(text):
    """'1,352.9646' -> '1352.9646' (the number as printed, without thousands separators)."""
    return text.replace(",", "")


def words_of(page, chars=False):
    """Words of a page (duplicate glyphs of bold overprinting removed)."""
    return page.dedupe_chars().extract_words(x_tolerance=1.5, y_tolerance=2.0, return_chars=chars)


def xc(w):
    return (w["x0"] + w["x1"]) / 2


def yc(w):
    return (w["top"] + w["bottom"]) / 2


# ----------------------------------------------------------------------------------------------------------------------
# Ausgrid: one table of tariff rows, columns located by the printed unit row
# ----------------------------------------------------------------------------------------------------------------------
# A column: (kind, component, time_band, season); kind "price" (a network price), "metering" (per-tariff metering
# service charge, side output) or "dlf" (distribution loss factor, not a price). Units come from the printed unit row.
NAC = ("price", "Network Access Charge", "", "")


def energy(label, band):
    return ("price", f"Network Energy Rates - {label}", band, "")


A_ENERGY_TOU = [energy("Non-ToU", "anytime"), energy("Peak", "peak"), energy("Shoulder", "shoulder"),
                energy("Off Peak", "offpeak")]
A_STEPS = [("price", "Step Rates - Step 1", "block1", ""), ("price", "Step Rates - Step 2", "block2", "")]
A_BLOCKS = [("price", "Inclining Block Rates - 1st block", "block1", ""),
            ("price", "Inclining Block Rates - 2nd block", "block2", "")]
P_ENERGY = [("price", "Network Energy Prices - Non-ToU Flat", "anytime", ""),
            ("price", "Network Energy Prices - Non-ToU Block 1", "block1", ""),
            ("price", "Network Energy Prices - Non-ToU Block 2", "block2", ""),
            ("price", "Network Energy Prices - Non-ToU Block 3", "block3", ""),
            ("price", "Network Energy Prices - ToU Peak", "peak", ""),
            ("price", "Network Energy Prices - ToU Shoulder", "shoulder", ""),
            ("price", "Network Energy Prices - ToU Off-peak", "offpeak", "")]
METERING = [("metering", "Metering Service Charge - Non Capital", "", ""),
            ("metering", "Metering Service Charge - Capital", "", "")]
CAPACITY = [("price", "Network Capacity Prices - Peak", "peak", ""), ("price", "Network Capacity Prices - Peak", "peak", "")]

# name -> (unit row as printed, spaces removed, '¢' as 'c'; heading words that must be printed above it; columns after
# the DLF column, in print order)
AUSGRID_LAYOUTS = {
    "2004 monthly demand and capacity (peak, shoulder, off-peak)": (
        ["c/day"] + ["c/kWh"] * 6 + ["$/mth"] * 6,
        ["Network Access Charge", "Non-ToU", "Peak", "Shoulder", "Off Peak", "Step 1", "Step 2",
         "Monthly Demand Rates", "Monthly Capacity Rates", "Off-Peak", "(per kVA or kW)"],
        [NAC] + A_ENERGY_TOU + A_STEPS
        + [("price", f"Monthly Demand Rates - {p}", b, "") for p, b in
           (("Peak", "peak"), ("Shoulder", "shoulder"), ("Off-Peak", "offpeak"))]
        + [("price", f"Monthly Capacity Rates - {p}", b, "") for p, b in
           (("Peak", "peak"), ("Shoulder", "shoulder"), ("Off-Peak", "offpeak"))]),
    "2005 monthly peak demand and capacity": (
        ["c/day"] + ["c/kWh"] * 6 + ["$/(kWorkVA)/mth"] * 2,
        ["Network Access Charge", "Non-ToU", "Peak", "Shoulder", "Off Peak", "Step 1", "Step 2", "Monthly", "Demand",
         "Capacity"],
        [NAC] + A_ENERGY_TOU + A_STEPS + [("price", "Monthly Demand Rates - Peak", "peak", ""),
                                          ("price", "Monthly Capacity Rates - Peak", "peak", "")]),
    "2006 monthly peak demand and capacity": (
        ["c/day"] + ["c/kWh"] * 5 + ["c/kwh"] + ["$(kWorkVA)/mth"] * 2,
        ["Network Access Charge", "Non-ToU", "Peak", "Shoulder", "Off Peak", "Step 1", "Step 2", "Monthly", "Demand",
         "Capacity"],
        [NAC] + A_ENERGY_TOU + A_STEPS + [("price", "Monthly Demand Rates - Peak", "peak", ""),
                                          ("price", "Monthly Capacity Rates - Peak", "peak", "")]),
    "2007 monthly peak demand (kVA) and capacity (kW, kVA)": (
        ["c/day"] + ["c/kWh"] * 6 + ["$/kVA/month", "$/kW/month", "$/kVA/month"],
        ["Network Access Charge", "Non-ToU", "Peak", "Shoulder", "Off Peak", "Step 1", "Step 2", "Monthly", "Demand",
         "Capacity"],
        [NAC] + A_ENERGY_TOU + A_STEPS + [("price", "Monthly Demand Rates - Peak", "peak", ""),
                                          ("price", "Monthly Capacity Rates - Peak", "peak", ""),
                                          ("price", "Monthly Capacity Rates - Peak", "peak", "")]),
    "2008 daily peak capacity, step rates": (
        ["c/day"] + ["c/kWh"] * 6 + ["c/kW/day", "c/kVA/day"],
        ["Network Access Charge", "Non-ToU", "Time of Use (ToU)", "Peak", "Shoulder", "Off Peak", "Step 1", "Step 2",
         "Daily", "Capacity"],
        [NAC] + A_ENERGY_TOU + A_STEPS + [("price", "Daily Capacity Rates - Peak", "peak", ""),
                                          ("price", "Daily Capacity Rates - Peak", "peak", "")]),
    "2009 daily peak capacity, inclining block rates": (
        ["c/day"] + ["c/kWh"] * 6 + ["c/kW/day", "c/kVA/day"],
        ["Network Access Charge", "Non‐ToU", "Time of Use (ToU)", "Peak", "Shoulder", "Off Peak", "Inclining Block",
         "1st block", "2nd block", "Daily", "Capacity"],
        [NAC] + A_ENERGY_TOU + A_BLOCKS + [("price", "Daily Capacity Rates - Peak", "peak", ""),
                                           ("price", "Daily Capacity Rates - Peak", "peak", "")]),
    "2012 flat, three blocks, ToU, capacity": (
        ["c/day"] + ["c/kWh"] * 7 + ["c/kW/day", "c/kVA/day"],
        ["Network Access Charge", "Network Energy Prices", "Non-ToU", "ToU", "Flat", "Block 1", "Block 2", "Block 3",
         "Peak", "Shoulder", "Off-peak", "Network Capacity"],
        [NAC] + P_ENERGY + CAPACITY),
    "2015 metering service charge, flat, three blocks, ToU, capacity": (
        ["c/day"] * 3 + ["c/kWh"] * 7 + ["c/kW/day", "c/kVA/day"],
        ["Service", "Non", "Capital", "Network Access", "Network Energy Prices", "Non ToU", "ToU", "Flat", "Block 1",
         "Block 2", "Block 3", "Peak", "Shoulder", "Off-peak", "Network Capacity"],
        METERING + [NAC] + P_ENERGY + CAPACITY),
    "2019 metering service charge, ToU, seasonal demand, capacity": (
        ["c/day"] * 3 + ["c/kWh"] * 4 + ["c/kW/day"] * 3 + ["c/kVA/day"],
        ["Metering Service", "Non", "Capital", "Network Access", "Network Energy Prices", "Non ToU", "Peak", "Shoulder",
         "Off-peak", "Network Demand", "High", "Low", "Season", "Network Capacity"],
        METERING + [NAC, ("price", "Network Energy Prices - Non ToU", "anytime", ""),
                    ("price", "Network Energy Prices - Peak", "peak", ""),
                    ("price", "Network Energy Prices - Shoulder", "shoulder", ""),
                    ("price", "Network Energy Prices - Off-peak", "offpeak", ""),
                    ("price", "Network Demand Prices - High Season", "", "high"),
                    ("price", "Network Demand Prices - Low Season", "", "low")] + CAPACITY[:1] + CAPACITY[1:]),
}

UNIT_START_RE = re.compile(r"^[c¢$]")
A_CODE_RE = re.compile(r"^(EA\d{3}|\d{2,3})(\*{0,2})$")


def unit_key(u):
    return u.replace("¢", "c").replace(" ", "")


def ausgrid_unit_row(words):
    """The printed unit row: [(unit as printed, x-centre, top)], units spanning several words joined."""
    by_line = defaultdict(list)
    for w in words:
        if re.search(r"/(?:day|kWh|kwh|mth|month)\b|\(kW|kVA\)", w["text"]) or w["text"] in ("$", "or"):
            by_line[round(w["top"] / 4)].append(w)
    best = max(by_line.values(), key=lambda ws: sum(1 for w in ws if "/" in w["text"]))
    top = min(w["top"] for w in best)
    # unit tokens printed a little above or below the main unit line belong to it
    row = sorted([w for w in words if abs(w["top"] - top) <= 6 and (
        re.search(r"/(?:day|kWh|kwh|mth|month)|\(kW|kVA\)", w["text"]) or w["text"] in ("$", "or"))],
        key=lambda w: w["x0"])
    units = []
    for w in row:
        if UNIT_START_RE.match(w["text"]) and not (units and units[-1]["text"].endswith(("$", "(kW", "or"))):
            units.append(dict(w))
        elif units:
            u = units[-1]
            u["text"] += " " + w["text"]
            u["x1"] = w["x1"]
    return [(u["text"], xc(u), u["top"], u["bottom"]) for u in units]


def ausgrid(year, path):
    doc = common.document(path)
    rows, metering = [], []
    with pdfplumber.open(os.path.join(common.ROOT, path)) as pdf:
        pages = [(i, p) for i, p in enumerate(pdf.pages, 1)
                 if re.search(r"\(?(?:excludes|exclude) GST|Excludes GST", p.extract_text() or "")]
        if not pages:
            raise SystemExit(f"{path}: no page states 'excludes GST'")
        page_no, page = pages[0]
        words = words_of(page)
        units = ausgrid_unit_row(words)
        printed = [unit_key(u[0]) for u in units]
        layout = [name for name, (us, _, _) in AUSGRID_LAYOUTS.items() if [unit_key(x) for x in us] == printed]
        header_bottom = min(u[2] for u in units)
        title = next(w for w in words if w["text"].startswith("Effective") or w["text"] == "Network")
        header_text = " ".join(w["text"] for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
                               if title["top"] <= w["top"] < header_bottom)
        header_words = set(header_text.split())
        layout = [n for n in layout if all(set(h.split()) <= header_words for h in AUSGRID_LAYOUTS[n][1])]
        if len(layout) != 1:
            raise SystemExit(f"{path} p{page_no}: unit row {printed} / headings {header_text!r} match layouts {layout}")
        _, _, columns = AUSGRID_LAYOUTS[layout[0]]
        dlf = next(w for w in words if w["text"] == "DLF" and w["top"] < header_bottom)
        cols = [(("dlf", "DLF", "", ""), None, xc(dlf))] + [(c, u[0], u[1]) for c, u in zip(columns, units)]
        gap = min(b[2] - a[2] for a, b in zip(cols, cols[1:]))
        unit_bottom = max(u[3] for u in units)

        anchors = [w for w in words if w["top"] > unit_bottom and A_CODE_RE.match(w["text"]) and w["x1"] < dlf["x0"]]
        # the first word on its line that looks like a code (Ausgrid names contain numbers: 'Controlled Load 1')
        anchors = [a for a in anchors if not any(o is not a and abs(o["top"] - a["top"]) < 3 and o["x0"] < a["x0"]
                                                 and A_CODE_RE.match(o["text"]) for o in anchors)]
        # codes are printed in one column: drop numbers elsewhere (footnotes: '1750kWh per 91 days')
        x_code = Counter(round(a["x0"] / 3) for a in anchors).most_common(1)[0][0] * 3
        anchors = [a for a in anchors if abs(a["x0"] - x_code) <= 6]
        anchors.sort(key=lambda w: w["top"])
        last = anchors[-1]["bottom"] + 12
        values = [w for w in words if unit_bottom < w["top"] < last and DEC_RE.match(w["text"])
                  and w["x0"] > dlf["x0"] - 15]
        names = [w for w in words if unit_bottom < w["top"] < last and not DEC_RE.match(w["text"])
                 and w["x0"] < dlf["x0"] - 3]
        cells = defaultdict(dict)
        for v in values:
            a = min(anchors, key=lambda a: abs(yc(a) - yc(v)))
            col = min(range(len(cols)), key=lambda k: abs(cols[k][2] - xc(v)))
            if abs(cols[col][2] - xc(v)) > gap * 0.6:
                raise SystemExit(f"{path} p{page_no}: value {v['text']} at x={xc(v):.0f} is in no column")
            if col in cells[id(a)]:
                raise SystemExit(f"{path} p{page_no}: two values in one cell ({a['text']}, column {col})")
            cells[id(a)][col] = v["text"]
        for i, a in enumerate(anchors):
            lo = (yc(anchors[i - 1]) + yc(a)) / 2 if i else a["top"] - 8
            hi = (yc(a) + yc(anchors[i + 1])) / 2 if i + 1 < len(anchors) else last
            name = " ".join(w["text"] for w in sorted(names, key=lambda w: (round(w["top"]), w["x0"]))
                            if lo <= yc(w) < hi and w["x0"] > a["x1"] - 1)
            code, mark = A_CODE_RE.match(a["text"]).groups()
            mark = mark or re.search(r"(\**)$", name).group(1)  # 2004-05 to 2007-08 mark the name: 'Domestic*'
            got = cells[id(a)]
            if 0 not in got:
                raise SystemExit(f"{path} p{page_no}: {code} has no DLF")
            notes = [f"p{page_no} network price table, GST exclusive"]
            if mark == "*" and int(year[:4]) >= 2015:
                notes.append("* a metering service charge applies to sites with generation (table footnote)")
            elif mark == "*" and int(year[:4]) < 2007:
                notes.append("* not available to new installations / type 6 meter only (table footnote)")
            elif mark:
                notes.append(f"{mark} see table footnotes")
            if re.search(r"Closed|Obsolete", name):
                notes.append("closed to new customers")
            for k, text in sorted(got.items()):
                (kind, comp, band, season), unit, _ = cols[k]
                if kind == "dlf":
                    continue
                note = list(notes)
                if unit_key(unit) in ("$/mth", "$/(kWorkVA)/mth", "$(kWorkVA)/mth"):
                    if mark == "**" and "Capacity" in comp:
                        unit = "$/kW/month"
                        note.append("$/kW/month: footnote 'The monthly capacity rates for prices marked ** are in "
                                    "$/kW/month'")
                    elif unit_key(unit) == "$/mth":
                        unit = "$/(kVA or kW)/mth"
                        note.append("unit: '$/mth' under the heading '(per kVA or kW)'")
                unit_std = unit.replace("¢", "c")
                if kind == "metering":
                    metering.append({"distributor": "Ausgrid", "fin_year": year, "tariff_code": code,
                                     "meter_class": "", "component": comp, "unit": unit_std,
                                     "value": clean_value(text), "gst": "excl", "source_file": path,
                                     "locator": locators.pdf(page_no), "note": "; ".join(notes)})
                    continue
                rows.append(common.row(doc, code, comp, clean_value(text), unit_std, locators.pdf(page_no), name=name,
                                       note="; ".join(note), time_band=band, season=season,
                                       charge_type=charge_type(comp, unit_std)))
    return rows, metering


# ----------------------------------------------------------------------------------------------------------------------
# Endeavour: Appendix network price tables, every price printed as an Excl. GST / Incl. GST pair
# ----------------------------------------------------------------------------------------------------------------------
ENDEAVOUR = {  # year -> (file, pages printed as images, read by OCR)
    "2005-06": ("2005-06/NetworkPriceList_010605_released_030605.pdf", False),
    "2006-07": ("2006-07/Network_Price_List_01_July_06_published_2006-06-27.pdf", False),
    "2007-08": ("2007-08/Network_Price_List_01_July_07_-_published_2007-05-08.pdf", False),
    "2008-09": ("2008-09/Network_Price_List_01_July_08_-_issued_13-06-2008.pdf", False),
    "2009-10": ("2009-10/Network_Price_List_01_July_09_-_Rev_3.pdf", False),
    "2010-11": ("2010-11/2010_11_Network_Price_List_-_version_2.pdf", False),
    "2011-12": ("2011-12/Network_Price_List_201112_v3.pdf", False),
    "2012-13": ("2012-13/Network_Price_List_201213_28_June_2012.pdf", False),
    "2013-14": ("2013-14/Network_Price_List_201314_v4.pdf", False),
    "2014-15": ("2014-15/Network_Price_List_201415_Final_v2.pdf", True),
    "2015-16": ("2015-16/NUOS_Price_List_201516F_Published__v4.pdf", True),
    "2016-17": ("2016-17/NUOS_Price_List_201617_v2.pdf", False),
    "2017-18": ("2017-18/NUOS_Price_List_201718_v2.pdf", False),
    "2018-19": ("2018-19/NUOS_Price_List_201819_v4.0.pdf", False),
    "2019-20": ("2019-20/NUOS_Price_List_201920_v1.pdf", False),
    "2020-21": ("2020-21/NETWORK-PRICE-LIST_NETWORK-TARIFFS-2020-2021_July2020.pdf", False),
    "2021-22": ("2021-22/Network-Price-List-2021-22_v1.2.pdf", False),
    "2022-23": ("2022-23/NUOS-Price-List-202223-v1.0.pdf", False),
}
TABLE_RE = re.compile(r"Table\s*(\d+[a-c]?)\s*[-–]\s*(.+)")
E_CODE_RE = re.compile(r"^(?:[NEGF][A-Z0-9]{2,4}|n\.a\.)$")
NUM_RE = re.compile(r"^-?\d*\.\d+$|^-?\d+$")


def lines_of(words, tol=3.0):
    """Words grouped into printed lines, top to bottom, each line left to right."""
    out = []
    for w in sorted(words, key=lambda w: (yc(w), w["x0"])):
        if out and abs(yc(w) - out[-1][0]) <= tol:
            out[-1][1].append(w)
        else:
            out.append([yc(w), [w]])
    return [sorted(ws, key=lambda w: w["x0"]) for _, ws in out]


def glue(words):
    """Rejoin a word that text extraction split between two touching glyphs ('N' '70' -> 'N70')."""
    out = []
    for w in sorted(words, key=lambda w: (round(yc(w)), w["x0"])):
        p = out[-1] if out else None
        if p and abs(yc(p) - yc(w)) < 1 and -1 < w["x0"] - p["x1"] < 0.6:
            out[-1] = dict(p, text=p["text"] + w["text"], x1=w["x1"])
        else:
            out.append(dict(w))
    return out


def split_at_bounds(words, bounds):
    """A heading glued over a column border ('Off-' + 'Time' -> 'Off-Time') is split after its hyphen."""
    out = []
    for w in words:
        cut = None
        for i, c in enumerate(w.get("chars") or []):
            if i and w["chars"][i - 1]["text"] == "-" and any(w["x0"] < b <= c["x0"] + 0.5 for b in bounds):
                cut = i
        if cut is None:
            out.append(w)
            continue
        cs = w["chars"]
        out.append(dict(w, text="".join(c["text"] for c in cs[:cut]), x1=cs[cut - 1]["x1"], chars=cs[:cut]))
        out.append(dict(w, text="".join(c["text"] for c in cs[cut:]), x0=cs[cut]["x0"], chars=cs[cut:]))
    return out


def endeavour_page(words, text, ocr=False):
    """One Endeavour price table page -> (table id, title, effective date, columns, rows), or None when the page has no
    price table. columns: [header text above the column pair]; rows: [(name, [codes], {column: Excl. GST value},
    {column: Incl. GST value}, line top)]."""
    m = next((TABLE_RE.search(l) for l in text.splitlines() if TABLE_RE.search(l)), None)
    if not m or not any(re.match(r"^Excl\.?$", w["text"]) for w in words):
        return None
    lines = lines_of(words)
    head_line = max(lines, key=lambda ws: sum(1 for w in ws if re.match(r"^(Excl|Incl)\.?$", w["text"])))
    ref = Counter(round(w["top"]) for w in head_line if re.match(r"^(Excl|Incl)\.?$", w["text"])).most_common(1)[0][0]
    # a heading wrapped in a narrow column ('Excl.' over 'GST') sits a few points higher
    head_line = sorted([w for w in words if abs(w["top"] - ref) <= 6], key=lambda w: w["x0"])
    pairs = []
    for w in [w for w in head_line if re.match(r"^(Excl|Incl)\.?$", w["text"])]:
        gst = next((g for g in head_line if g["text"] == "GST" and 0 <= g["x0"] - w["x1"] < 8), None)
        pairs.append((w["text"][:4], w["x0"], (gst or w)["x1"]))
    cols = []  # (excl x-centre, incl x-centre, span x0, span x1)
    for a, b in zip(pairs, pairs[1:]):
        if a[0] == "Excl" and b[0] == "Incl":
            cols.append(((a[1] + a[2]) / 2, (b[1] + b[2]) / 2, a[1], b[2]))
    gst_words = [w for w in head_line if re.match(r"^(Excl|Incl)\.?$|^GST$", w["text"])]
    if not cols:
        return None
    head_top = min(w["top"] for w in gst_words)
    head_bottom = max(w["bottom"] for w in gst_words)
    first = cols[0][2]
    # column borders, nearer the next pair: a heading is centred over its pair but may overhang to the right
    bounds = [(cols[k - 1][3] + 3 * c[2]) / 4 if k else c[2] - 12 for k, c in enumerate(cols)] + [cols[-1][3] + 12]
    # the column headings start with 'Network Access Charge' over the first column pair
    access = [w for w in words if re.search(r"Access|Network", w["text"]) and bounds[0] - 20 <= xc(w) < bounds[1]
              and w["bottom"] < head_top]
    top = min(w["top"] for w in access if w["top"] > max(a["top"] for a in access) - 30) - 4
    heads = split_at_bounds([w for w in words if top <= w["top"] and w["bottom"] < head_top + 0.5], bounds[1:-1])
    labels = [" ".join(" ".join(w["text"] for w in l) for l in lines_of(
        [w for w in heads if bounds[k] <= xc(w) < bounds[k + 1]])) for k in range(len(cols))]
    date = None
    for line in text.splitlines():
        if not re.search(r"definitions|apply|Loss|from", line):
            date = date or re.search(r"effective(\d{1,2})(January|February|March|April|May|June|July|August|"
                                     r"September|October|November|December)(\d{4})", re.sub(r"\s+", "", line))
    dlf = re.search(r"Loss\s+Factor", text)
    rows = []
    centres = [(k, 0, c[0]) for k, c in enumerate(cols)] + [(k, 1, c[1]) for k, c in enumerate(cols)]
    gap = min(abs(a[2] - b[2]) for a in centres for b in centres if a is not b)
    for l in lines_of(glue([w for w in words if w["top"] > head_bottom])):
        if re.match(r"IMPORTANT|NOTES?:?$|Notes", l[0]["text"]):
            break
        left = [w for w in l if w["x1"] < first - 1]
        k = len(left)
        while k and E_CODE_RE.match(left[k - 1]["text"]) and len(left) - k < 2:
            k -= 1
        codes = [w["text"] for w in left[k:]]
        if not codes and not (ocr and left and any(w["x1"] >= first - 1 and NUM_RE.match(w["text"]) for w in l)):
            continue
        name = " ".join(w["text"] for w in left[:k])
        ex, inc = {}, {}
        for w in l:
            if w["x1"] < first - 1 or (dlf and w["x0"] > bounds[-1]):  # distribution loss factor and its code
                continue
            col, which, c = min(centres, key=lambda c: abs(c[2] - xc(w)))
            if abs(c - xc(w)) > gap * 0.6:
                raise SystemExit(f"value {w['text']} at x={xc(w):.0f} in no column")
            (inc if which else ex)[col] = w["text"]
        rows.append([name, codes, ex, inc, (min(w["top"] for w in l), max(w["bottom"] for w in l), cols), left])
    code_x = min((w["x0"] for r in rows if r[1] for w in r[5] if w["text"] == r[1][0]), default=None)
    for r in rows:
        if not r[1] and code_x is not None:  # a code OCR cannot read ('66N'): the name is what lies left of the codes
            r[0] = " ".join(w["text"] for w in r[5] if w["x1"] < code_x - 1)
    rows = [tuple(r[:5]) for r in rows]
    return m.group(1), m.group(2).strip(), date and " ".join(date.groups()), labels, rows


def endeavour_debug(year):
    rel, ocr = ENDEAVOUR[year]
    path = os.path.join(common.ROOT, ARCHIVE, "endeavour", rel)
    with pdfplumber.open(path) as pdf:
        for i, p in enumerate(pdf.pages, 1):
            text = p.extract_text() or ""
            if "Excl" not in text:
                continue
            text = p.dedupe_chars().extract_text() or ""
            got = endeavour_page(words_of(p, chars=True), text)
            if not got:
                continue
            tid, title, date, labels, rows = got
            print(f"p{i} Table {tid} {title[:50]} | effective {date}")
            for k, l in enumerate(labels):
                print(f"   c{k}: {l}")
            for r in rows:
                print("     ", r[0][:30], r[1], [r[2].get(k, "") for k in range(len(labels))],
                      "" if all(r[3].get(k) for k in r[2]) else "MISSING INCL")


# compact sub-heading (lower case, no spaces, group heading words removed) -> (component suffix, time band, season)
ENERGY_SUBS = {
    "": ("", "anytime", ""),
    "flat": ("Flat", "anytime", ""),
    "non-timeofuse,block1": ("Non-Time Of Use, Block 1", "block1", ""),
    "non-timeofuse,block2": ("Non-Time Of Use, Block 2", "block2", ""),
    "non-timeofuseblock1": ("Non-Time Of Use Block 1", "block1", ""),
    "non-timeofuseblock2": ("Non-Time Of Use Block 2", "block2", ""),
    "non-timeofuseblock3": ("Non-Time Of Use Block 3", "block3", ""),
    "uncontrollednon-timeofuse,block1": ("Uncontrolled Non-Time Of Use, Block 1", "block1", ""),
    "uncontrollednon-timeofuse,block2": ("Uncontrolled Non-Time Of Use, Block 2", "block2", ""),
    "uncontrollednon-timeofuseblock1": ("Uncontrolled Non-Time Of Use Block 1", "block1", ""),
    "uncontrollednon-timeofuseblock2": ("Uncontrolled Non-Time Of Use Block 2", "block2", ""),
    "uncontrollednon-timeofuseblock3": ("Uncontrolled Non-Time Of Use Block 3", "block3", ""),
    "controllednon-timeofuse": ("Controlled Non-Time Of Use", "", ""),
    "controlledloadnon-timeofuse": ("Controlled Load Non-Time Of Use", "", ""),
    "controlledloadflat": ("Controlled Load Flat", "", ""),
    "timeofusepeak": ("Time Of Use Peak", "peak", ""),
    "timeofuseshoulder": ("Time Of Use Shoulder", "shoulder", ""),
    "timeofuseoff-peak": ("Time Of Use Off-Peak", "offpeak", ""),
    "flat/block1": ("Flat / Block 1", "block1", ""),
    "block2": ("Block 2", "block2", ""),
    "toupeak": ("TOU Peak", "peak", ""),
    "toushoulder": ("TOU Shoulder", "shoulder", ""),
    "touoffpeak": ("TOU Off Peak", "offpeak", ""),
    "stouhigh-seasonpeak(nov-mar)": ("STOU High-season Peak (Nov-Mar)", "peak", "high"),
    "stoulow-seasonpeak(apr-oct)": ("STOU Low-season Peak (Apr-Oct)", "peak", "low"),
    "stouoffpeak": ("STOU Off Peak", "offpeak", ""),
}
UNITS = {"$/day": "$/day", "¢/kWh": "c/kWh", "$/kVA/month": "$/kVA/month", "$(kVAorkW)/mth": "$(kVA or kW)/mth",
         "¢/(kVAorkW)/day": "c/(kVA or kW)/day"}


def compact(text):
    return re.sub(r"\s+", "", text)


def endeavour_column(label, table_text):
    """A column heading as printed -> (component, unit, charge type, time band, season)."""
    c = compact(label)
    unit = next((u for u in sorted(UNITS, key=len, reverse=True) if c.endswith(u)), None)
    if unit is None:
        raise SystemExit(f"no unit in column heading {label!r}")
    c = c[:-len(unit)].lower()
    unit = UNITS[unit]
    charge_style = "energycharge" in compact(table_text).lower() or "demandcharge" in compact(table_text).lower()
    if c.startswith("networkaccess"):
        return "Network Access Charge", unit, "fixed", "", ""
    if "generated" in c or "(credit)" in c:
        group = "Generated Energy Rate" + (" (Credit)" if "(credit)" in c else "")
        sub = re.sub(r"^(?:generated|energy|rate|\(credit\)|\(#\)|-)+", "", c)
        sub = {"non-timeofuse": "Non-Time of Use", "flat": "Flat"}[sub]
        return f"{group} - {sub}", unit, "export", "anytime", ""
    if "kva" in unit.lower():
        peak_only = "peak-only" in c
        if "summer&winter" in c or "highseason" in c:
            name = ("High Season Demand Rate (Summer & Winter)" if re.search(r"High\s+Season\s+Demand", table_text)
                    else "Demand Rate - Summer & Winter")
            season = "high"
        elif "othermonths" in c or "lowseason" in c:
            name = ("Low Season Demand Rate (Other Months)" if re.search(r"Low\s+Season\s+Demand", table_text)
                    else "Demand Rate - Other Months")
            season = "low"
        elif "high-season(nov-mar)" in c:
            name, season = "Demand Charge - High-season (Nov-Mar)", "high"
        elif "low-season(apr-oct)" in c:
            name, season = "Demand Charge - Low-season (Apr-Oct)", "low"
        elif re.fullmatch(r"demandrate(timeofusepeak-only)?", c):
            name, season = "Demand Rate", ""
        else:
            raise SystemExit(f"unknown demand column {label!r}")
        if peak_only:
            name += " - Time Of Use Peak-only"
        return name, unit, "demand", "peak" if peak_only else "", season
    sub = c.replace("energyrate", "").replace("energycharge", "")
    sub = re.sub(r"^(?:energy|ener|rate|charge|gy|y)+", "", sub).replace("non-time", "non-time").replace("off-peak", "off-peak")
    sub = sub.replace("non-timeofuse", "non-timeofuse").replace("controllednon-", "controllednon-")
    sub = re.sub(r"non-?timeofuse", "non-timeofuse", sub)
    if sub not in ENERGY_SUBS:
        raise SystemExit(f"unknown energy column {label!r} ({sub!r})")
    name, band, season = ENERGY_SUBS[sub]
    group = "Energy Charge" if charge_style else "Energy Rate"
    return (f"{group} - {name}" if name else group), unit, "energy", band, season


def footnotes(text):
    """Footnotes printed under a table: mark -> text."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^\s*(\[\d+\]|\*|#)\s*(.+)", line)
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2).strip()
    return out


def gst_check(excl, incl):
    """The printed GST inclusive price is the exclusive one plus 10%, rounded to its printed decimals."""
    places = len(incl.split(".")[1]) if "." in incl else 0
    want = (Decimal(excl) * Decimal("1.1")).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return abs(want - Decimal(incl)) <= Decimal(1).scaleb(-places)


SKIPPED = []  # (year, table, reason) of printed tables not stored


def ocr_words(page, resolution=288):
    """Words recognised on a rendered page (the engine and resolution scripts/tariffdb/locators.py re-reads with), in
    PDF points. A recognised phrase is split into words spread over its box in proportion to their length."""
    result, _ = locators._ocr_engine()(page.to_image(resolution=resolution).original)
    k = 72 / resolution
    words = []
    for box, text, _ in result or []:
        x0, x1 = min(p[0] for p in box) * k, max(p[0] for p in box) * k
        top, bottom = min(p[1] for p in box) * k, max(p[1] for p in box) * k
        text = re.sub(r"\b[lI1]ncl\.", "Incl.", text)
        text = re.sub(r"(Excl\.|Incl\.)(?=\S)", r"\1 ", text)  # 'Excl.GST'
        text = re.sub(r"(?<=\S)(Excl\.|Incl\.)", r" \1", re.sub(r"GST(?=\S)", "GST ", text))  # 'GSTExcl.'
        parts = text.split()
        if bottom - top > 2 * (x1 - x0) and len(text) > 3:
            continue  # vertical text (rotated code column headings)
        total = sum(len(t) for t in parts) + len(parts) - 1
        at = x0
        for t in parts:
            w = (x1 - x0) * len(t) / max(1, total)
            words.append({"text": t, "x0": at, "x1": at + w, "top": top, "bottom": bottom})
            at += w + (x1 - x0) / max(1, total)
    return words


OCR_PROBLEMS = []


def twins(v, incl, ctype):
    return bool(incl is not None and NUM_RE.match(v) and NUM_RE.match(incl)
                and (gst_check(v, incl) or ctype == "export" and v == incl))


def ocr_cell(path, page_no, code, comp, v, incl, ctype, geom, k):
    """An OCR-read Excl. GST price, kept only when it is a number its printed Incl. GST twin confirms. A pair the page
    reading garbles ('00°0', '1057694') is read again from the cell pair alone at twice the resolution."""
    if twins(v, incl, ctype):
        return v, ""
    top, bottom, cols = geom
    with pdfplumber.open(os.path.join(common.ROOT, path)) as pdf:
        crop = pdf.pages[page_no - 1].crop((cols[k][2] - 6, top - 3, cols[k][3] + 6, bottom + 3))
        result, _ = locators._ocr_engine()(crop.to_image(resolution=576).original)
    got = [re.sub(r"\s+", "", t) for _, t, _ in sorted(result or [], key=lambda r: r[0][0][0])]
    got = [t for t in " ".join(got).split() if NUM_RE.match(t)]
    if len(got) == 2 and twins(got[0], got[1], ctype) and locators.verify(
            os.path.join(common.ROOT, path), locators.pdf(page_no, ocr=True), got[0])[0]:
        return got[0], f"the page OCR read this Excl./Incl. GST pair as {v!r} / {incl!r}; re-read from the cells at 576 dpi"
    OCR_PROBLEMS.append((path, page_no, code, comp, v, incl, got))
    return None, ""


def similar(a, b):
    import difflib
    return difflib.SequenceMatcher(None, compact(a).lower(), compact(b).lower()).ratio()


def endeavour_tables(year, path, ocr):
    """[(page, table id, title, effective date, labels, rows, page text)] of an Endeavour price list."""
    if path in _TABLES:
        return _TABLES[path]
    out = _TABLES[path] = []
    with pdfplumber.open(os.path.join(common.ROOT, path)) as pdf:
        for i, p in enumerate(pdf.pages, 1):
            text = p.extract_text() or ""
            if ocr:
                if not re.search(r"Table\s+\d+[a-c]?\s*[-–]", text) or "Network Price Tables" in text and i < 5:
                    continue
                words = ocr_words(p)
                text = "\n".join(" ".join(w["text"] for w in l) for l in lines_of(words))
                got = endeavour_page(words, text, ocr=True)
                if got:
                    out.append((i,) + got + (text,))
                continue
            if "Excl" not in text:
                continue
            text = p.dedupe_chars().extract_text() or ""
            got = endeavour_page(words_of(p, chars=True), text)
            if got:
                out.append((i,) + got + (text,))
    return out


def neighbours(year):
    """The nearest earlier and the nearest later Endeavour list printed as text."""
    text = [y for y, (_, ocr) in ENDEAVOUR.items() if not ocr]
    return [y for y in (max((y for y in text if y < year), default=None), min((y for y in text if y > year), default=None))
            if y]


_TABLES = {}


def known_codes(year):
    return {r[1][0] for ny in neighbours(year)
            for t in endeavour_tables(ny, f"{ARCHIVE}/endeavour/{ENDEAVOUR[ny][0]}", False) for r in t[5]}


def endeavour(year, rel, ocr):
    path = f"{ARCHIVE}/endeavour/{rel}"
    doc = common.document(path)
    start = f"1 July {year[:4]}"
    rows = []
    # a group heading spanning several columns ('Energy Charge', 'High Season Demand Rate') is not always read over
    # each of them: name the groups the way the document's (or for OCR, the neighbouring lists') headings print them
    doc_head = " ".join(" ".join(t[4]) for y in ([year] if not ocr else neighbours(year))
                        for t in endeavour_tables(y, f"{ARCHIVE}/endeavour/{ENDEAVOUR[y][0]}", False))
    for page_no, tid, title, date, labels, trows, text in endeavour_tables(year, path, ocr):
        title = re.sub(r"\s*(?:\(GST Inclusive.*|Prices?\s*eff.*|P\s*r\s*i\s*c\s*e.*|effective.*)$", "", title).strip()
        if date is None:
            raise SystemExit(f"{path} p{page_no} Table {tid}: no effective date")
        d0 = int(date.split()[2]) * 10000 + ["January", "February", "March", "April", "May", "June", "July", "August",
                                            "September", "October", "November", "December"].index(date.split()[1]) * 100 + int(date.split()[0])
        if d0 > int(year[:4]) * 10000 + 600 + 1:
            SKIPPED.append((year, f"p{page_no} Table {tid} {title}", f"prices effective {date}, after 1 July"))
            continue
        heading_note = ""
        if ocr:  # OCR misreads headings ('d / kWh'): take them from the same table of a neighbouring year's list
            best = None
            for ny in neighbours(year):
                for np_, ntid, _, _, nlabels, _, ntext in endeavour_tables(ny, f"{ARCHIVE}/endeavour/{ENDEAVOUR[ny][0]}",
                                                                          False):
                    if ntid == tid and len(nlabels) == len(labels):
                        score = min(similar(a, b) for a, b in zip(labels, nlabels))
                        if best is None or score > best[0]:
                            best = (score, ny, np_, nlabels, ntext)
            if best is None or best[0] < 0.6:
                raise SystemExit(f"{path} p{page_no} Table {tid}: OCR headings {labels} match no neighbouring table "
                                 f"(best {best and best[:3]})")
            labels, text_for_cols = best[3], best[4]
            heading_note = (f"; column headings and units as printed in the {best[1]} list p{best[2]} Table {tid}, "
                            f"which the OCR reading of this page's headings matches")
        else:
            text_for_cols = text
        head = " ".join(labels)
        cols = [endeavour_column(l, head + " " + doc_head + " " + text_for_cols) for l in labels]
        notes_at = footnotes(text[text.find("NOTES"):] if "NOTES" in text else text)
        for name, codes, ex, inc, geom in trows:
            marks = re.findall(r"\[\d+\]|[*#](?=\s|$)", name + " ")
            clean = re.sub(r"\s*\[\d+\]|\s*[*#](?=\s|$)", "", name).strip()
            note = [f"p{page_no} Table {tid} {title}, Excl. GST column{heading_note}"]
            if not codes:  # OCR cannot read the code ('66N'): the neighbouring years print it for this tariff name
                if not ocr:
                    raise SystemExit(f"{path} p{page_no} Table {tid}: row {name!r} has prices but no tariff code")
                same = {r[1][0] for ny in neighbours(year)
                        for t in endeavour_tables(ny, f"{ARCHIVE}/endeavour/{ENDEAVOUR[ny][0]}", False)
                        if t[1] == tid for r in t[5] if r[1] and compact(r[0]).lower() == compact(clean).lower()}
                if len(same) != 1:
                    raise SystemExit(f"{path} p{page_no} Table {tid}: no code for {name!r} (neighbours: {same})")
                code = same.pop()
                note.append(f"OCR cannot read the tariff code; the neighbouring years' Table {tid} print {code} for "
                            f"'{clean}'")
                codes = [code]
            code = codes[0]
            if ocr and code not in known_codes(year):
                fixed = code[:1] + code[1:].replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
                if fixed not in known_codes(year):
                    raise SystemExit(f"{path} p{page_no}: OCR code {code!r} is in no neighbouring year's list")
                note.append(f"OCR reads the code as {code!r}; {fixed} in the neighbouring years' lists")
                code = fixed
                codes = [fixed] + [c[:3] + c[3:].replace("O", "0") for c in codes[1:]]
            if ocr:  # OCR drops spaces and misreads letters ('Feed-ln', 'DomesticTOU.Type5Meter'): take the spelling
                # of the closest name a neighbouring list prints for the same code
                names = {(ny, re.sub(r"\s*\[\d+\]|\s*[*#](?=\s|$)", "", r[0]).strip())
                         for ny in neighbours(year) for t in endeavour_tables(
                             ny, f"{ARCHIVE}/endeavour/{ENDEAVOUR[ny][0]}", False) for r in t[5] if r[1][:1] == [code]}
                fuzzy = lambda n: compact(n).lower().replace("l", "i").replace(".", ",")  # noqa: E731
                best = max(names, key=lambda yn: similar(fuzzy(yn[1]), fuzzy(clean)), default=None)
                if best and similar(fuzzy(best[1]), fuzzy(clean)) >= 0.85:
                    if best[1] != clean:
                        note.append(f"name spelled as in the {best[0]} list (OCR: {clean!r})")
                    clean = best[1]
            if date != start:
                note.append(f"table heading prints 'effective {date}'")
            if len(codes) > 1 and codes[1] != code:
                note.append(f"second code column: {codes[1]}")
            for mk in marks:
                if mk in notes_at:
                    note.append(f"{mk} {notes_at[mk]}")
            text_values = [v for v in ex.values() if not NUM_RE.match(v.replace(",", ""))]
            if text_values and not ocr:
                raise SystemExit(f"{path} p{page_no} {code}: non-numeric cells {text_values}")
            for k, v in sorted(ex.items()):
                comp, unit, ctype, band, season = cols[k]
                vnote, incl = list(note), inc.get(k)
                if ocr:
                    v, why = ocr_cell(path, page_no, code, comp, v, incl, ctype, geom, k)
                    if v is None:
                        continue
                    if why:  # the re-read pair has been checked
                        vnote.append(why)
                        incl = None
                    elif ctype == "export" and incl == v:
                        vnote.append("credit printed the same in the Excl. and Incl. GST columns")
                    incl = None
                if incl is None:
                    if not ocr:
                        warn(f"{path} p{page_no} {code} {comp}: no Incl. GST twin for {v}")
                elif ctype == "export" and incl == v:
                    vnote.append("credit printed the same in the Excl. and Incl. GST columns"
                                 + (f" (* {notes_at['*']})" if "*" in notes_at else ""))
                elif not gst_check(v.replace(",", ""), incl.replace(",", "")):
                    warn(f"{path} p{page_no} {code} {comp}: Excl {v} vs Incl {incl} is not +10%")
                seen = next((r for r in rows if (r["tariff_code"], r["component"], r["unit"]) == (code, comp, unit)), None)
                if seen:
                    if seen["value"] != clean_value(v):
                        raise SystemExit(f"{path} p{page_no} {code} {comp}: {v} here, {seen['value']} at {seen['locator']}")
                    vnote.append(f"repeated printing of {seen['locator']} ({seen['tariff_name']}) as '{clean}'")
                rows.append(common.row(doc, code, comp, clean_value(v), unit,
                                       locators.pdf(page_no, ocr=ocr), name=clean, note="; ".join(vnote),
                                       charge_type=ctype, time_band=band, season=season))
    if not ocr:
        rows = [reread(r) for r in rows]
    return rows


def reread(r):
    """Bold prices are overprinted: a glyph drawn twice at one spot ('10.4581' over '1') doubles in the text layer
    ('110.4581'). Such a value, read with the duplicate removed, is re-read on the rendered page (OCR) instead."""
    path = os.path.join(common.ROOT, r["source_file"])
    if locators.verify(path, r["locator"], r["value"])[0]:
        return r
    page = int(re.search(r"p(\d+)", r["locator"]).group(1))
    ocr = locators.pdf(page, ocr=True)
    if not locators.verify(path, ocr, r["value"])[0]:
        raise SystemExit(f"{r['source_file']} {r['locator']} {r['tariff_code']} {r['component']}: {r['value']} is "
                         "neither in the text layer nor on the rendered page")
    return dict(r, locator=ocr, note=r["note"] + "; the text layer doubles an overprinted bold glyph, value re-read "
                "from the rendered page")


def charge_type(comp, unit):
    c = comp.lower()
    if "access charge" in c or "network access" in c:
        return "fixed"
    if "capacity" in c:
        return "capacity"
    if "demand" in c:
        return "demand"
    if "credit" in c or "generated" in c or "feed-in" in c:
        return "export"
    if "kwh" in unit.lower():
        return "energy"
    return schema.charge_type_from_label(comp, unit)


def main():
    rows, metering = [], []
    for year, rel in AUSGRID.items():
        r, m = ausgrid(year, f"{ARCHIVE}/ausgrid/{rel}")
        print(f"Ausgrid {year}: {len(r)} rates, {len({x['tariff_code'] for x in r})} tariffs, {len(m)} metering")
        rows += r
        metering += m
    for year, (rel, ocr) in ENDEAVOUR.items():
        r = endeavour(year, rel, ocr)
        print(f"Endeavour {year}: {len(r)} rates, {len({x['tariff_code'] for x in r})} tariffs")
        rows += r
    for s in SKIPPED:
        print("not stored:", *s)
    common.write(SLUG, rows)
    schema.write_metering(f"history_{SLUG}", metering)
    if WARNINGS:
        print(f"{len(WARNINGS)} warnings")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--debug":
        endeavour_debug(sys.argv[2])
    else:
        main()
