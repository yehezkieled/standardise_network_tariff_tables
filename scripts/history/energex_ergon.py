#!/usr/bin/env python
"""Energex and Ergon Energy network prices for pricing years before 2023-24, read from the archived documents
(sources/archive/{energex,ergon}/) into the parser rows of the tariff database (contract: scripts/history/CONTRACT.md).

  .venv/bin/python scripts/history/energex_ergon.py   ->  out/history/energex_ergon.csv

DOCUMENTS lists the one document read per distributor and pricing year (the final price list customers were billed
on, normally the latest issue) with the function that reads its layout. Each layout function finds its tables by their
printed headings and column labels, and emits every standard control tariff price as printed:
  - the total network price (basis NUoS) where the document prints one; a document printing only the distribution and
    transmission parts gets DUoS / TUoS rows (the build stores totals only);
  - the GST-exclusive table where both are printed; a document printing only GST-inclusive prices gets gst 'incl' rows;
  - zone-priced codes carry 'zone: <name>' in the note.
Ergon prints no total network price before 2020-21 (DUOS / TUOS / jurisdictional parts, GST-inclusive only to
2006-07), so those years give part rows only. Per-tariff metering charges printed in the price tables (Energex 2015-16
Table 3.2, the 2017-18+ ACS metering columns) go to out/dnsp_metering/history_energex_ergon.csv.
The OCR of the Ergon 2005-06 and 2006-07 price books makes a full run take several minutes.
"""
import functools
import os
import re
import sys
import warnings
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402
import openpyxl  # noqa: E402
from published import cell_value  # noqa: E402  (common puts scripts/ on the path)
from tariffdb import locators  # noqa: E402

schema = common.schema

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

SLUG = "energex_ergon"
A = "sources/archive"

# a number as printed in these documents: '$1,103.60', '0.9320', '-44.000', '$ 0.3874' (the '$' may stand apart)
NUM_RE = re.compile(r"^\(?-?\$?-?\(?[0-9][0-9,]*(?:\.[0-9]+)?\)?$|^-?\$?\.[0-9]+$")


# ---------------------------------------------------------------------------------------------------------- PDF access
@functools.lru_cache(maxsize=None)
def _pdf(path):
    import pdfplumber
    return pdfplumber.open(os.path.join(common.ROOT, path))


def n_pages(path):
    return len(_pdf(path).pages)


@functools.lru_cache(maxsize=None)
def lines(path, page):
    """The text lines of a page (1-based), layout order, runs of spaces collapsed."""
    text = _pdf(path).pages[page - 1].extract_text(layout=True) or ""
    return [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]


@functools.lru_cache(maxsize=None)
def words(path, page):
    """Words of a page with their boxes (pdfplumber extract_words)."""
    return _pdf(path).pages[page - 1].extract_words(x_tolerance=1.5, y_tolerance=2.0)


def tables(path, page, **settings):
    """pdfplumber tables of a page: lists of rows of cell text (newlines kept)."""
    return _pdf(path).pages[page - 1].extract_tables(settings or {})


def pages_with(path, *patterns):
    """1-based pages whose text matches every regex pattern (case-insensitive, per line)."""
    return [p for p in range(1, n_pages(path) + 1)
            if all(re.search(pat, "\n".join(lines(path, p)), re.I | re.M) for pat in patterns)]


def number(tok):
    """The number as printed, without '$', thousands separators or spaces ('$1,103.60' -> '1103.60'); None when the
    token is not a number ('N/A', '-', 'POA', 'Site')."""
    t = str(tok).strip().replace(" ", "")
    if not t or not NUM_RE.match(t):
        return None
    neg = t.startswith("-") or t.startswith("(") or "$-" in t or "$(" in t
    t = t.replace("$", "").replace(",", "").replace("(", "").replace(")", "").lstrip("-")
    return ("-" + t) if neg else t


def tokens_with_dollar_joined(line):
    """Split a text line into tokens, joining a lone '$' to the number after it ('$ 0.3874' -> '$0.3874')."""
    out = []
    for t in line.split():
        if out and out[-1] == "$":
            out[-1] = "$" + t
        else:
            out.append(t)
    return out


# ---------------------------------------------------------------------------------------------------------- rows
def price(doc, code, component, value, unit, page, *, ocr=False, **kw):
    """One parser row for a value printed on a PDF page (common.row with a pdf locator)."""
    return common.row(doc, code, component, value, unit, locators.pdf(page, ocr=ocr), **kw)


def cell_price(doc, code, component, cell, unit, ws, **kw):
    """One parser row for a spreadsheet cell, its value as Excel displays it."""
    from published import cell_value
    return common.row(doc, code, component, cell_value(cell), unit, locators.xlsx(ws, cell), **kw)


# ======================================================================================================================
# Energex 2000-01 to 2006-07: "Network Price List - Contestable Customers" / "Network Pricing Schedule"
# Tables headed '... (GST Exclusive)' / '(EXCLUDES GST)'; from 2004-05 each user group prints DUOS, TUOS and NUOS tables.
#   Standard Asset Customers: <code> <description> <DLF> <min demand> <max demand> <service availability $/month>
#                             <demand $/kW/month> <volume c/kWh>
#   Volume charge (2000-01):  <code> <description> <service availability $/month> <volume c/kWh>
#   Connection Asset Customers: <code> <description> <DLF> Site Specific <capacity $/kW/month>
#                             <actual demand $/kW/month> <volume c/kWh>   (fixed charge site specific, not published)
# ======================================================================================================================
def energex_contestable(doc):
    path = doc["local_path"]
    rows = []
    totals = bool(pages_with(path, r"\(NUOS\)"))  # from 2004-05 the parts are printed too: read the totals only
    for page in range(1, n_pages(path) + 1):
        gst = basis = group = None
        heading = ""
        page_lines = lines(path, page)
        for i, ln in enumerate(page_lines):
            m = re.search(r"\((DUOS|TUOS|NUOS)\)", ln)
            if m:
                basis = {"DUOS": "DUoS", "TUOS": "TUoS", "NUOS": "NUoS"}[m.group(1)]
            if re.search(r"Connection Asset Customers", ln):
                group, heading = "CAC", ln
            elif re.search(r"Standard Asset Customers|Demand Charge . Zone P", ln):
                group, heading = "SAC", ln
            elif re.search(r"Volume Charge Zone P", ln):
                group, heading = "VOL", ln
            elif re.search(r"Network Prices for Franchise Customers", ln):
                group = None  # read by energex_franchise
            if re.search(r"GST\s+Inclusive|INCLUDES GST", ln, re.I):
                gst = "incl"
                continue
            if re.search(r"GST\s+Exclusive|EXCLUDES GST", ln, re.I):
                gst = "excl"
                if not heading.endswith(ln):
                    heading = f"{heading} {ln}" if "Asset" in heading and "GST" not in heading else heading or ln
                continue
            if gst != "excl" or group is None or (totals and basis != "NUoS"):
                continue
            toks = tokens_with_dollar_joined(ln)
            if not toks or not re.fullmatch(r"(DH\d|DL|DM|DS|VS|VH\d|VL|VM|SACV|CAC\d+[BL]?)", toks[0]):
                continue
            code = toks[0]
            vals = [t if t in ("N/A", "-", "POA") else number(t) for t in toks[1:]]
            tail = vals[-3:] if group in ("SAC", "CAC") else vals[-2:]
            if any(v is None or v == "POA" for v in tail):
                continue  # 'Site Specific Prices' / POA rows print no price
            rows += contestable_rows(doc, page, group, code, toks, page_lines[i + 1:i + 3], tail,
                                     basis or "NUoS", heading)
    return rows


DLF_WORDS = re.compile(r"\s+(?:\d+(?:/\d+)?\s*(?:kV|kilovolt)\b.*|LV\b.*|Low\b.*|FLCL\b.*|Site\b.*|N/A\b.*)$")


def contestable_rows(doc, page, group, code, toks, after, tail, basis, heading):
    """The prices of one code row of energex_contestable; the name is the description printed beside the code (and
    its wrapped word 'Demand' on the next line)."""
    desc = " ".join(t for t in toks[1:] if number(t) is None and t not in ("N/A", "-", "POA", "$"))
    name = (re.sub(r"\s+(?:N/A|Site)\b.*$", "", desc) if group == "CAC" else DLF_WORDS.sub("", " " + desc)).strip()
    if after and re.fullmatch(r"Demand\b.*", after[0]) and "Demand" not in name and group == "SAC":
        name += " Demand"
    note = f"p{page} {heading}" + ("; zone: Zone P" if doc["pricing_year"] == "2000-01" else "")
    cls = {"SAC": "Standard Asset Customers", "CAC": "Connection Asset Customers",
           "VOL": "Standard Asset Customers (volume charge, transitional)"}[group]
    if group == "SAC":
        comps = [("Service Availability Charge", "$ per month", "fixed"), ("Demand Charge", "$ per kW per month", None),
                 ("Volume Charge", "cents per kWh", None)]
    elif group == "VOL":
        comps = [("Service Availability Charge", "$ per month", "fixed"), ("Volume Charge", "cents per kWh", None)]
    else:
        comps = [("Capacity Charge", "$ per kW per month", "capacity"),
                 ("Actual Demand Charge", "$ per kW per month", None), ("Volume Charge", "cents per kWh", None)]
    return [price(doc, code, comp, v, unit, page, name=name, customer_class=cls, basis=basis, note=note,
                  charge_type=ct)
            for (comp, unit, ct), v in zip(comps, tail) if v not in ("N/A", "-")]


def energex_franchise(doc):
    """Energex 2005-06 and 2006-07 'Network Prices for Franchise Customers' (GST exclusive): no tariff codes; 2005-06
    prints DUOS and TUOS charges side by side (no total), 2006-07 one DUOS / TUOS / NUOS row each per customer class.
    The 2005-06 'Supplementary Network Prices for Franchise Customers' (p14, 'not approved by the QCA but provided as
    supplementary information') are not read."""
    path = doc["local_path"]
    rows = []
    totals = bool(pages_with(path, r"Franchise Customers \(Excl.*\n(?:.*\n)*.*\bNUOS\b"))
    table = r"^(?!.*Supplementary).*Network Prices for Franchise Customers \((GST )?Excl"
    for page in pages_with(path, table):
        ls = lines(path, page)
        start = next(i for i, ln in enumerate(ls) if re.search(table, ln))
        heading = ls[start]
        name = ""
        for ln in ls[start + 1:]:
            if ln.startswith("Notes"):
                break
            toks = tokens_with_dollar_joined(ln)
            nums = [number(t) for t in toks]
            if "DUOS Charges" in ln or not any(nums):
                continue
            j = next((i for i, t in enumerate(toks) if t in ("DUOS", "TUOS", "NUOS")), None)
            if j is not None:  # 2006-07: <class> <component> fixed volume
                if j and not toks[0].startswith("("):
                    name = " ".join(toks[:j])
                parts = [(toks[j], nums[j + 1:j + 3])]
            else:  # 2005-06: <class> DUOS fixed, DUOS volume, TUOS fixed, TUOS volume
                k = next(i for i, v in enumerate(nums) if v is not None)
                name = " ".join(toks[:k])
                parts = [("DUOS", nums[k:k + 2]), ("TUOS", nums[k + 2:k + 4])]
            for b, (fixed, volume) in parts:
                if totals and b != "NUOS":
                    continue  # 2006-07 prints the total under the DUOS and TUOS parts
                basis = {"DUOS": "DUoS", "TUOS": "TUoS", "NUOS": "NUoS"}[b]
                for comp, unit, v, ct in (("Fixed Charge", "$/month", fixed, "fixed"),
                                          ("Volume Charge", "c/kWh", volume, None)):
                    rows.append(price(doc, name, comp, v, unit, page, name=name, basis=basis, charge_type=ct,
                                      customer_class="Franchise Customers",
                                      note=f"p{page} {heading}; no code printed"))
    return rows


def energex_2005_2006(doc):
    return energex_contestable(doc) + energex_franchise(doc)


# ======================================================================================================================
# Energex 2007-08 to 2009-10: "Network Pricing Schedule" / "Tariff Schedule", ruled tables
# Each table carries a title row ('Network Component of Network Prices (NUOS) for Connection Asset Customers (GST
# Exclusive)') or, 2009-10, a 'Tariff Component' column (DUoS / TUoS / NUoS / 'NUoS (GST)' rows per code), and a header
# row naming every price column with its unit ('Capacity Charge $ per kW per month', 'Energy Price (c/kWh)').
# ======================================================================================================================
UNIT_IN_HEADER = re.compile(r"\(?((?:\$|c\b|Cents)\s*(?:per\b|/).*?)\)?$", re.I)
GROUPS = [(r"Connection Asset", "Connection Asset Customers"),
          (r"Non-?\s*Demand", "Standard Asset Customers - Non-Demand Metered"),
          (r"Standard Asset|Demand Metered", "Standard Asset Customers - Demand Metered"),
          (r"Unmetered|Street\s*light", "Unmetered Supply and Streetlights"),
          (r"Solar PV", "Solar PV")]


def flat(cell):
    return re.sub(r"\s+", " ", (cell or "").replace("\n", " ")).strip()


def price_column(header):
    """(component, unit) of a price column header ('Fixed Charge $ per month' -> 'Fixed Charge', '$ per month'), or
    None for a column printing no price (code, description, loss factor, chargeable demand kW)."""
    m = UNIT_IN_HEADER.search(header)
    if not m or not re.search(r"Charge|Price", header):
        return None
    unit = re.sub(r"\s*/\s*per\b", " per", m.group(1)).replace("kW.h", "kWh").replace("Cents", "c")
    return header[:m.start()].strip(" (") , unit


def page_title(path, page, table_index):
    """The 'Table n: ...' title printed above the table_index-th table of a page (2009-10 tables carry none)."""
    titles = [ln for ln in lines(path, page) if re.match(r"Table \d+:", ln)]
    return titles[table_index] if table_index < len(titles) else ""


def energex_ruled(doc, pages):
    path = doc["local_path"]
    rows = []
    for page in pages:
        for t, table in enumerate(tables(path, page)):
            table = [[flat(c) for c in r] for r in table]
            title = next((r[0] for r in table if r[0] and not any(r[1:]) and re.search(r"GST|Network", r[0])), "")
            title = title or page_title(path, page, t)
            hdr = next((i for i, r in enumerate(table) if any(re.search(r"Code$", c) for c in r)), None)
            if hdr is None:
                continue
            header = table[hdr]
            cols = {j: price_column(c) for j, c in enumerate(header)}
            cols = {j: c for j, c in cols.items() if c}
            code_col = next(j for j, c in enumerate(header) if c.endswith("Code"))
            desc_col = next((j for j, c in enumerate(header) if "Description" in c), None)
            comp_col = next((j for j, c in enumerate(header) if c.endswith("Component")), None)
            cond_col = next((j for j, c in enumerate(header) if c in ("Conditions", "Tariff Conditions")), None)
            group = next((g for pat, g in GROUPS if re.search(pat, title, re.I)), "")
            gst = "incl" if re.search(r"GST Inclusive", title, re.I) else "excl"
            m = re.search(r"\((DUOS|TUOS|NUOS)\)", title, re.I)
            code = name = cond = ""
            for r in table[hdr + 1:]:
                if re.fullmatch(r"\d{4}", r[code_col]):
                    code = r[code_col]
                    name = r[desc_col] if desc_col is not None else ""
                    cond = re.sub(r"(?<=\d) (?=\d)", "", r[cond_col]) if cond_col is not None else ""
                elif r[code_col]:
                    code = ""  # 'HV Demand with Distributor owned assets': site-specific prices, no code
                if not code:
                    continue
                part = r[comp_col] if comp_col is not None else (m.group(1) if m else "NUOS")
                if part.upper() == "NUOS (GST)":
                    continue  # the GST-inclusive NUoS row under the GST-exclusive one
                basis = {"DUOS": "DUoS", "TUOS": "TUoS", "NUOS": "NUoS"}[part.upper()]
                vals = {j: number(r[j]) for j in cols}
                note = f"p{page} {title}" + (f"; {cond}" if cond else "")
                peak = [j for j in cols if re.match(r"Peak", cols[j][0], re.I)]
                off = [j for j in cols if re.match(r"Off.?Peak", cols[j][0], re.I)]
                same = (peak and off and vals[peak[0]] is not None and vals[peak[0]] == vals[off[0]]
                        and not re.search(r"TOU", name))
                for j, (comp, unit) in cols.items():
                    v = vals[j]
                    if v is None:
                        continue  # 'Site Specific', 'POA', 'N/A', '-'
                    kw = {}
                    if same and j in off:
                        continue
                    if same and j in peak:
                        comp = re.sub(r"^Peak\s*", "", comp)
                        kw = {"note": note + "; peak and off-peak columns print the same price"}
                    ct = ("fixed" if re.match(r"Fixed|Service Availability", comp) else
                          "capacity" if comp.startswith("Capacity") else None)
                    rows.append(price(doc, code, comp, v, unit, page, name=name, customer_class=group, basis=basis,
                                      gst=gst, charge_type=ct, **{"note": note, **kw}))
    return totals_only(rows)


def totals_only(rows):
    """Drop the DUoS / TUoS part rows of a code whose NUoS total is printed too, and the GST-inclusive rows of a code
    whose GST-exclusive prices are printed too."""
    excl = {r["tariff_code"] for r in rows if r["gst"] == "excl"}
    rows = [r for r in rows if r["gst"] == "excl" or r["tariff_code"] not in excl]
    with_total = {(r["tariff_code"], r["gst"]) for r in rows if r["basis"] == "NUoS"}
    return [r for r in rows if r["basis"] == "NUoS" or (r["tariff_code"], r["gst"]) not in with_total]


def energex_blocks(doc, page, title):
    """Energex 2007-08 non-demand and unmetered tables (GST-exclusive half of the page from `title`): blocks of
    DUOS / TUOS / NUOS lines per customer category, the code printed as '(NTC 8600)' somewhere in the block's left
    column, a TOU fixed charge printed on its own line below the 'Peak' line."""
    path = doc["local_path"]
    ls = lines(path, page)
    start = next(i for i, ln in enumerate(ls) if re.search(title, ln))
    heading = f"{ls[start - 1]} {ls[start]}" if "GST" in ls[start] and "GST" not in ls[start - 1] else ls[start]
    group = next((g for pat, g in GROUPS if re.search(pat, heading, re.I)), "")
    rows, label, codes, pending = [], [], [], []
    for i, ln in enumerate(ls[start + 1:], start + 1):
        if re.match(r"^\d+$", ln) or re.match(r"Network prices for", ln) or "Page" in ln:
            break
        m = re.search(r"\b(DUOS|TUOS|NUOS)\b( Peak| Off-Peak)?", ln)
        if not m:
            if label and not any(number(t) for t in ln.split()):
                label.append(ln)  # a wrapped line of the category name ('Supply (including Night-')
            continue
        left = ln[:m.start()].strip()
        if m.group(1) == "DUOS" and m.group(2) in (None, " Peak"):
            label, codes = [], []  # a new customer category starts on its DUOS line
        nc = re.search(r"\(NTC (\d{4})(?: or (\d{4}))?\)", left)
        if nc:
            codes = [c for c in nc.groups() if c]
            left = left.replace(nc.group(0), "")
        left = re.sub(r"\)\d$", ")", left).strip()
        if left:
            label.append(left)
        if m.group(1) != "NUOS":
            continue
        vals = [number(t) for t in ln[m.end():].split()]
        band = (m.group(2) or "").strip()
        if band:
            comps = [(f"{band} Energy Charge", "c/kWh", vals[-1], None)]
            if band == "Peak" and i + 1 < len(ls) and number(ls[i + 1]) is not None:
                comps.insert(0, ("Fixed Charge", "$/day", number(ls[i + 1]), "fixed"))
        else:
            comps = [("Fixed Charge", "$/day", vals[0], "fixed"), ("Energy Charge", "c/kWh", vals[1], None)]
        pending.append((codes, comps))
        name = " ".join(label).replace("- ", "-")
        for cs, cmp in pending:
            for code in cs:
                for comp, unit, v, ct in cmp:
                    rows.append(price(doc, code, comp, v, unit, page, name=name, customer_class=group,
                                      charge_type=ct, note=f"p{page} {heading}" + (
                                          f"; one price row printed for NTC {' or '.join(cs)}" if len(cs) > 1 else "")))
        pending = []
    return rows


def energex_2007_08(doc):
    path = doc["local_path"]
    ruled = pages_with(path, r"\(NUOS\) for (Connection|Standard) Asset Customers")
    return (energex_ruled(doc, ruled)
            + energex_blocks(doc, pages_with(path, r"Non-Demand Metered\s*$")[-1], r"^\(GST exclusive\)")
            + energex_blocks(doc, pages_with(path, r"Unmetered Supply including Streetlights\s*$")[-1],
                             r"^\(GST exclusive\)"))


def energex_pv_2008_09(doc):
    """Energex 2008-09 'PV_Tariff_2008-09.pdf', the same-day companion of the 2008-09 Network Pricing Schedule pricing
    the Solar Bonus Scheme codes 9700 / 9800 / 9900 (one ruled table, page 1). The table carries no GST heading; its
    only GST statement is that the 44c/kWh feed-in credit includes GST, so the rows are GST inclusive."""
    path = doc["local_path"]
    ls = lines(path, 1)
    title = next(ln for ln in ls if ln.startswith("Queensland Government Solar Bonus Scheme"))
    title += " " + ls[ls.index(title) + 1]
    gst_note = next(ln for ln in ls if ln.startswith("The 44c/kWh"))
    rows = energex_ruled(doc, [1])
    for r in rows:
        r.update(gst="incl", customer_class="Solar PV", note=r["note"].replace("p1 ", f"p1 {title}", 1)
                 + f"; '{gst_note} ...'")
        if r["charge_type"] == "energy":
            r["charge_type"] = "export"
    return rows


def energex_2008_10(doc):
    path = doc["local_path"]
    return energex_ruled(doc, pages_with(path, r"Customers \(GST Exclusive\)|Street Lights \(GST Exclusive\)|"
                                               r"^Table \d+: Network Tariffs"))



# ======================================================================================================================
# Energex 2010-11 to 2016-17 tariff schedules (PDF)
# Energex tariff schedules 2010-11 to 2016-17 (PDF): the NUOS (total network) price table of standard control services.
#
# Part module for scripts/history/energex_ergon.py. One document per pricing year, the latest issue:
#   2010-11 V5 (7 Sep 2010), 2011-12 V2 (23 Jun 2011), 2012-13 version 9 (11 Feb 2013, capture 2013-04-11),
#   2013-14 v6 (4 Feb 2014), 2014-15 Version 1.2 (27 Jun 2014), 2015-16 V6 (8 Dec 2015), 2016-17 V2 (27 Jun 2016).
# Earlier issues of these years print the same NUOS prices (2012-13 v9 adds only the NTC8400 charge note f; 2013-14 v4
# swaps the 9700/9800 labels), so no issue starts part-way through a year.
# ======================================================================================================================
EGX_SOLAR = {"9700", "9800", "9900", "7500"}  # solar PV codes: their energy price is the feed-in tariff (paid to customer)
CODE_RE = re.compile(r"^(?:NTC)?(\d{4})\d?[a-z]?/?$")  # '8600b', '30003' (code + footnote 3), '8500/'


def _is_num(t):
    return number(t) is not None and re.search(r"\d", t) is not None


def _component(label, unit, code):
    """(charge_type, time_band) of a NUOS column."""
    low = label.lower()
    if "fixed" in low:
        return "fixed", ""
    if "capacity" in low:
        return "capacity", ""
    if "demand" in low:
        return "demand", ""
    ct = "export" if code in EGX_SOLAR else "energy"
    if "off peak" in low or "off-peak" in low:
        return ct, "offpeak"
    if "shoulder" in low:
        return ct, "shoulder"
    if "peak" in low:
        return ct, "peak"
    return ct, "anytime"


def _unit(text):
    """'($/kW/ month)' -> '$/kW/month'; 'c/kW.h' (2012-13 heading) is c/kWh."""
    u = text.strip("()").replace(" ", "")
    return "c/kWh" if u == "c/kW.h" else u


# ======================================================================================================================
# Layout A (2010-11 to 2015-16): one row per tariff; columns headed '<label> (<unit>)'. 2010-11 to 2012-13 print a
# separate 'Table 2 ... NUOS tariffs' with one column group; 2013-14 to 2015-16 print 'Table 3.1 ... SCS tariff charges'
# with DUOS, DPPC and NUOS groups side by side (each starting with Fixed ($/day)): only the NUOS group is read.
# Cells may be blank (site-specific CAC/EG fixed charges, FiT rows), so each value is placed by its word x-centre
# (nearest column heading) and each row is bounded by the horizontal rules of the price columns.
# ======================================================================================================================
def nuos_table(doc, heading):
    path = doc["local_path"]
    heading += r"[^.]*$"  # not the contents line (dot leaders)
    pages = pages_with(path, heading)
    assert len(pages) == 1, (path, heading, pages)
    page = pages[0]
    pdf = _pdf(path).pages[page - 1]
    ws = words(path, page)
    title = next(ln for ln in lines(path, page) if re.search(heading, ln, re.I))
    title_w = [w for w in ws if w["text"] == "Table"]
    y_title = min(w["top"] for w in title_w)

    # column headings: unit words '($/day)', '($/kW/' + 'month)', '(c/kWh)'
    units = [w for w in ws if w["top"] > y_title and re.match(r"^\((\$|c)/", w["text"])]
    y_units = min(w["top"] for w in units)
    units = [w for w in units if w["top"] < y_units + 25]
    units.sort(key=lambda w: w["x0"])
    centres = [(w["x0"] + w["x1"]) / 2 for w in units]
    bounds = []
    for i, c in enumerate(centres):
        left = (centres[i - 1] + c) / 2 if i else c - (centres[1] - c) / 2
        right = (c + centres[i + 1]) / 2 if i + 1 < len(centres) else c + (c - centres[i - 1]) / 2
        bounds.append((left, right))
    # the code column: the leftmost column of 4-digit codes left of the prices (2015-16 prints the billing codes
    # '8500 / 8550 / 8570' in a second column right of it)
    cand = [w for w in ws if w["top"] > y_units + 5 and w["x1"] < bounds[0][0] and CODE_RE.match(w["text"])]
    by_x = {}
    for w in cand:
        by_x.setdefault(round(w["x0"] / 4), []).append(w)
    code_x0 = min(k for k, v in by_x.items() if len(v) >= 3)
    codes = [w for w in cand if abs(w["x0"] / 4 - code_x0) <= 1.5]
    y_data = min(w["top"] for w in codes) - 3
    notes = [w for w in ws if w["top"] > y_data and w["text"] in ("Notes:", "Note", "a.")]
    y_end = min(w["top"] for w in notes) if notes else pdf.height
    codes = [w for w in codes if w["top"] < y_end]

    columns = []
    for (left, right), uw in zip(bounds, units):
        unit = uw["text"]
        if not unit.endswith(")"):
            # 'month)' after it on the same line (2010-11) or below it
            unit += " " + next(w["text"] for w in ws if w is not uw and w["text"].endswith(")") and (
                (abs(w["top"] - uw["top"]) < 2 and 0 <= w["x0"] - uw["x1"] < 8)
                or (uw["top"] < w["top"] < y_data and w["x0"] < uw["x1"] and w["x1"] > uw["x0"])))
        label = " ".join(w["text"] for w in sorted(ws, key=lambda w: (round(w["top"]), w["x0"]))
                         if y_title + 5 < w["top"] < y_data and left <= (w["x0"] + w["x1"]) / 2 < right
                         and not w["text"].startswith("(") and not w["text"].endswith(")")
                         and not re.match(r"^(DUOS|DPPC|NUOS|DUoS|NUoS|Charges|Tariff|Class|NTC|Code|Network)", w["text"]))
        columns.append({"label": label, "unit": _unit(unit), "left": left, "right": right})
    fixed = [i for i, c in enumerate(columns) if c["unit"] == "$/day"]
    columns = columns[fixed[-1]:]  # the NUOS group (the only group in 2010-11 to 2012-13)
    lo, hi = columns[0]["left"], columns[-1]["right"]

    # rows: the code cells (code words of the code column less than 3pt apart, '8500/' over '8600b'); every price
    # word belongs to the code cell nearest its vertical centre
    cells = []
    for w in sorted(codes, key=lambda w: w["top"]):
        if cells and w["top"] - cells[-1][-1]["bottom"] < 3:
            cells[-1].append(w)
        else:
            cells.append([w])
    cells = [([CODE_RE.match(w["text"]).group(1) for w in c], (c[0]["top"] + c[-1]["bottom"]) / 2) for c in cells]
    found = {}
    for w in ws:
        mid = (w["top"] + w["bottom"]) / 2
        if not (y_data <= w["top"] < y_end and lo <= (w["x0"] + w["x1"]) / 2 < hi):
            continue
        i = min(range(len(cells)), key=lambda i: abs(cells[i][1] - mid))
        assert abs(cells[i][1] - mid) < 20, (path, page, w["text"], mid)
        found.setdefault(i, []).append(w)

    rows, skipped = [], []
    for i, vals in sorted(found.items()):
        band_codes, _ = cells[i]
        printed = " / ".join(band_codes)
        text = " ".join(w["text"] for w in vals if not _is_num(w["text"]))
        if text:
            skipped.append(f"{printed}: {text}")
        for col in columns:
            got = [w for w in vals if col["left"] <= (w["x0"] + w["x1"]) / 2 < col["right"] and _is_num(w["text"])]
            if not got:
                continue
            got.sort(key=lambda w: w["top"])
            if len(got) > 1:
                skipped.append(f"{printed} {col['label']}: also " + " ".join(w["text"] for w in got[1:]))
            value = number(got[0]["text"])
            for code in band_codes:
                charge_type, band_tou = _component(col["label"], col["unit"], code)
                note = f"p{page} {title}"
                if len(band_codes) > 1:
                    note += f"; row prints codes {printed}"
                rows.append(price(doc, code, col["label"], value, col["unit"], page, charge_type=charge_type,
                                       time_band=band_tou, note=note))
    return rows, skipped, page, title


def layout_a(doc, heading, extra_notes=None):
    rows, skipped, page, title = nuos_table(doc, heading)
    for r in rows:
        for code, why in (extra_notes or {}).items():
            if r["tariff_code"] == code:
                r["note"] += "; " + why
        if r["tariff_code"] in EGX_SOLAR and r["charge_type"] == "export":
            r["note"] += "; solar PV feed-in tariff, paid to the customer"
    for s in skipped:
        print(f"  {doc['pricing_year']} p{page} not read: {s}")
    return rows


SITE = "fixed charge site-specific (not printed)"


def energex_2010_11(doc):
    return layout_a(doc, r"^Table 2 2010-11 NUOS tariffs", {
        "3500": SITE, "4000": SITE, "4500": SITE, "2000": SITE, "2500": SITE, "3000": SITE,
        "9700": "Government mandated tariff - paid to the customer by ENERGEX (note 3)",
        "9800": "Government mandated tariff - paid to the customer by ENERGEX (note 3)",
        "9900": "Government mandated tariff - paid to the customer by ENERGEX (note 3)"})


def energex_2011_12(doc):
    return layout_a(doc, r"^Table 2 2011-12 NUOS tariffs", {
        "3500": SITE, "4000": SITE, "4500": SITE, "2000": SITE, "2500": SITE, "3000": SITE,
        "9700": "Government mandated tariff paid by ENERGEX to the customer (note 3)",
        "9800": "Government mandated tariff paid by ENERGEX to the customer (note 3)",
        "9900": "Government mandated tariff paid by ENERGEX to the customer (note 3)"})


def energex_2012_13(doc):
    return layout_a(doc, r"^Table 2 2012-13 NUOS tariffs", {
        "3500": SITE, "4000": SITE, "4500": SITE, "2500": SITE, "3000": SITE,
        "8600": "Customers on Network Tariff Code 8600 will be transitioned to NTC 8500 by 31/12/12 (note b)",
        "8700": "Customers on Network Tariff Code 8700 will be transitioned to NTC 8800 by 31/12/12 (note c)",
        "7500": "Effective from 10/7/12 (note d)",
        "8400": "subject to Shareholding Minister's Directions dated 28 June 2012 and 31 January 2013 (note e); "
                "a further fixed 0.09198 $/day printed below it is 'Only chargeable according to the terms of the "
                "Shareholding Minister's Directions dated 31 January 2013' (note f), not read"})


def energex_2013_14(doc):
    return layout_a(doc, r"^Table 3\.1 - 2013/14 SCS tariff charges", {
        "3500": SITE, "4000": SITE, "4500": SITE, "2500": SITE, "3000": SITE})


def energex_2014_15(doc):
    reduced = ("Rates differ from AER approved rates: Energex has reduced its network charges for NTC8400 "
               "(and net tariffs NTC8900, NTC7600) (note 2)")
    return layout_a(doc, r"^Table 3\.1 – 2014/15 SCS tariff charges", {
        "2000": SITE + "; whole tariff site-specific", "8400": reduced, "8900": reduced, "7600": reduced})


def energex_2015_16_with_metering(doc):
    METERING.extend(energex_2015_16_metering(doc))
    return energex_2015_16(doc)


def energex_2015_16(doc):
    closed = "These tariffs will no longer be offered from 1 July 2015 to new customers (note 3)"
    notes = {"3000": closed + "; " + SITE, "8000": closed, "4000": SITE, "4500": SITE}
    for c in ("8500", "8800", "8400", "8900", "9000", "9100"):  # NTC column, then the billing codes beside it
        notes[c] = (f"billing codes {c} / {c[:2]}50 / {c[:2]}70 print the same prices ('These tariffs are duplicates "
                    "in order to implement the Meter Service Charge', note 4)")
    return layout_a(doc, r"^Table 3\.1 – 2015-16 SCS tariff charges", notes)


# 2015-16 'Table 3.2 - 2015-16 SAC and PV Metering Charges' (p18): the SCS Metering Service Charge (c/day) per billing
# tariff code, GST exclusive; a per-tariff metering charge, so it goes to the metering side output.
def energex_2015_16_metering(doc):
    path = doc["local_path"]
    (page,) = pages_with(path, r"^Table 3\.2 - 2015-16 SAC and PV Metering Charges[^.]*$")
    out = []
    for ln in lines(path, page):
        m = re.match(r"^(?:.*?\s)?(\d{4}) (\d{4})(\d?) (Capital and Non-capital|Capital & Non-capital|Capital only) "
                     r"([0-9.]+)\b", ln)
        if not m:
            continue
        network, billing, foot, which, value = m.groups()
        note = f"p{page} Table 3.2 - 2015-16 SAC and PV Metering Charges, GST exclusive; network tariff {network}"
        if foot == "6":
            note += "; As at 1 July 2015 these tariffs are not available (note 6)"
        out.append({"distributor": "Energex", "fin_year": doc["pricing_year"], "tariff_code": billing,
                    "meter_class": "Type 6", "component": f"SCS Metering Service Charge ({which})", "unit": "c/day",
                    "value": value, "gst": "excl", "source_file": path, "locator": locators.pdf(page), "note": note})
    return out


# ======================================================================================================================
# Layout B (2016-17): 'Table 2.3 - 2016-17 SCS tariff charges', one line per charge:
#   NTC<code> <parameter> <unit> <DUoS> <Jurisdictional> <DPPC> <NUoS>; the NUoS column is read.
# The table runs over two pages (the second repeats the heading row without the title).
# ======================================================================================================================
def energex_2016_17(doc):
    path = doc["local_path"]
    (first,) = pages_with(path, r"^Table 2\.3 - 2016-17 SCS tariff charges[^.]*$")
    title = "Table 2.3 - 2016-17 SCS tariff charges"
    rows = []
    for page in (first, first + 1):
        ws = words(path, page)
        head = {w["text"]: w for w in ws if w["text"] in ("Unit", "NUoS", "DUoS")}
        param = [w for w in ws if w["text"] == "parameter"][0]
        nuos_x = (head["NUoS"]["x0"] + head["NUoS"]["x1"]) / 2
        unit_x0 = head["Unit"]["x0"] - 25
        y0 = head["NUoS"]["bottom"]
        code = None
        for line in _lines_of(w for w in ws if w["top"] > y0):
            for w in line:
                m = re.match(r"^NTC(\d{4})$", w["text"])
                if m:
                    code = m.group(1)
            unit = [w for w in line if unit_x0 <= w["x0"] < head["DUoS"]["x0"] - 5 and re.match(r"^(\$|c)/", w["text"])]
            if not unit:
                continue
            label = " ".join(w["text"] for w in line if param["x0"] - 15 <= w["x0"] < unit_x0)
            val = [w for w in line if abs((w["x0"] + w["x1"]) / 2 - nuos_x) < 20]
            if not val or not _is_num(val[0]["text"]):
                print(f"  2016-17 p{page} not read: {code} {label}: "
                      + " ".join(w["text"] for w in line if w["x0"] > head["DUoS"]["x0"] - 15))
                continue
            u = unit[0]["text"].replace("$/Day", "$/day")
            low = label.lower()
            ct = "fixed" if low == "supply" else "demand" if "demand" in low else "energy"
            tb = ("offpeak" if "off-peak" in low else "shoulder" if "shoulder" in low else
                  "peak" if low.startswith(("usage peak", "peak demand")) else "anytime" if "flat" in low else "")
            note = f"p{page} {title}" + ("" if page == first else " (continued)")
            if code in ("4000", "4500", "3000") and ct != "fixed":
                note += "; " + SITE
            rows.append(price(doc, code, label, number(val[0]["text"]), u, page, charge_type=ct,
                                   time_band=tb, note=note))
    return rows


def _lines_of(ws):
    out = []
    for w in sorted(ws, key=lambda w: (w["top"], w["x0"])):
        if out and abs(w["top"] - out[-1][0]["top"]) < 2.5:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(ln, key=lambda w: w["x0"]) for ln in out]


E = f"{A}/energex"
EGX_PDF_DOCUMENTS = [
    (f"{E}/2010-11/20100907_V5_Tariff_Schedule_2010-11.pdf", energex_2010_11),
    (f"{E}/2011-12/20110623-Tariff-Schedule-2011-12-V2-pm.pdf", energex_2011_12),
    (f"{E}/2012-13/2012-13-Tariff-Schedule_3.pdf", energex_2012_13),
    (f"{E}/2013-14/2013-14-Tariff-Schedule-v6.pdf", energex_2013_14),
    (f"{E}/2014-15/2014-15-Tariff-Schedule-Version-1.2.pdf", energex_2014_15),
    (f"{E}/2015-16/2015-16-Tariff-Schedule-V6.pdf", energex_2015_16_with_metering),
    (f"{E}/2016-17/Tariff-Schedule-2016-17.pdf", energex_2016_17),
]


# ======================================================================================================================
# Energex and Ergon network price list workbooks (2015-16 to 2022-23)
# Energex and Ergon network price list workbooks, pricing years 2015-16 to 2022-23 (part of energex_ergon.py).
#
# Layouts:
# - energex_scs: Energex 2017-18 to 2019-20, sheet 'SCS charges <year>': one row per charge element under a header
#   'Tariff Class | Tariff Description | NTC | Billing Code Permutations | Tariff / Charge Element | Unit | DUOS |
#   JURISDICTIONAL | DPPC | NUOS'; the NUOS column is the total network price.
# - qld_price_list: Energex and Ergon 2020-21 to 2022-23, sheets 'SAC'/'CAC' (Energex), 'East|West|Mt Isa SAC|CAC' and
#   'Additional MEG NTCs' (Ergon): header 'Tariff | NTC | Charging parameter | Units | SCS Rates (DUOS, DPPC*, JS, NUOS)
#   | ACS metering Rates (one column per metering billing permutation)'. The NTC cell lists the billing codes of the
#   tariff (one per metering permutation, same network rates).
# - ergon_parts: Ergon 2015-16 and 2017-18 to 2019-20: tables headed by 'Network Tariff Code' with the price split into
#   'Distribution Use Of System (DUOS)', 'Transmission Use Of System (TUOS)' and 'Jurisdictional Scheme Charge' groups
#   (no total printed) and, from 2017-18, 'ACS Meter Charges'. 2015-16 prints a GST-inclusive copy of each table to the
#   right of the exclusive one.
# ======================================================================================================================
METERING = []  # schema.METERING_COLUMNS dicts
UNREAD = []  # printed values not read, with the reason


def clean(v):
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def blank(v):
    return v is None or (isinstance(v, str) and clean(v).upper() in ("", "N/A", "NA", "-"))


def numeric(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def site_specific(v):
    return isinstance(v, str) and "site" in v.lower() and "specific" in v.lower()


def code_text(v):
    return str(int(v)) if isinstance(v, float) and v.is_integer() else clean(v)


def workbook(doc):
    return openpyxl.load_workbook(os.path.join(common.ROOT, doc["local_path"]), data_only=True)


def check_year(doc, texts, where):
    years = {f"{m.group(1)}-{m.group(2)}" for t in texts for m in re.finditer(r"\b(20\d\d)\s*-\s*(\d\d)\b", t)}
    if doc["pricing_year"] not in years:
        raise SystemExit(f"{doc['local_path']} {where}: pricing year {doc['pricing_year']} not in title {sorted(years)}")


def metering(doc, code, meter_class, component, cell, unit, ws, note):
    METERING.append({
        "distributor": common.NAMES[doc["distributor_id"]], "fin_year": doc["pricing_year"], "tariff_code": code,
        "meter_class": meter_class, "component": component, "unit": unit, "value": cell_value(cell), "gst": "excl",
        "source_file": doc["local_path"], "locator": locators.xlsx(ws, cell), "note": note,
    })


def mark_repeats(rows):
    """A price printed twice in one document (same code, component, basis and value): the later printing is marked
    schema.REPEATED_PRINTING; differing values are noted on both."""
    seen = {}
    for r in rows:
        key = (r["tariff_code"], r["component"], r["basis"], r["unit"], r["time_band"], r["season"], r["gst"])
        prev = seen.setdefault(key, r)
        if prev is r:
            continue
        if prev["value"] == r["value"]:
            r["note"] += f"; {schema.REPEATED_PRINTING} of {prev['locator']} (identical)"
        else:
            prev["note"] += f"; also printed as {r['value']} at {r['locator']}"
            r["note"] += f"; also printed as {prev['value']} at {prev['locator']}"
    return rows


def pair_blocks(rows):
    """Two volume prices of one tariff split by a usage threshold ('Volume Peak Charge' and 'Volume Peak Charge (over
    10,000)'; 'Volume (below threshold)' and 'Volume (above threshold)') are blocks 1 and 2."""
    by = {}
    for r in rows:
        by.setdefault((r["tariff_code"], r["basis"], r["locator"].split("!")[0]), {})[r["component"]] = r
    for comps in by.values():
        for label, r in comps.items():
            m = re.fullmatch(r"(.*?)\s*\((?:over [\d,]+|above threshold)\)", label)
            if not m:
                continue
            first = comps.get(m.group(1)) or comps.get(f"{m.group(1)} (below threshold)")
            if first is None or first["charge_type"] != r["charge_type"]:
                continue
            prefix = "peak_" if first["time_band"] == "peak" else ""
            if first["time_band"] not in ("", "peak") or r["time_band"] != first["time_band"]:
                continue
            first["time_band"], r["time_band"] = f"{prefix}block1", f"{prefix}block2"
    return rows


def band(component, charge_type=None):
    """time_band from the label, with the labels the schema helper does not know: 'Volume Flat' is anytime energy,
    'Volume Sholuder' (sic) is shoulder."""
    l = component.lower()
    if "sholuder" in l:
        return "shoulder"
    if re.search(r"\bflat\b", l) and "volume" in l:
        return "anytime"
    return None


# ======================================================================================================================
# Energex 2017-18 to 2019-20: sheet 'SCS charges <year>'

def energex_scs(doc):
    wb = workbook(doc)
    ws = next(w for w in wb.worksheets if re.match(r"SCS charges", w.title, re.I))
    grid = list(ws.iter_rows())
    h = next(i for i, r in enumerate(grid) if any(clean(c.value) == "NTC" for c in r))
    head = {clean(c.value): c.column - 1 for c in grid[h] if clean(c.value)}

    def col(pattern):
        found = [i for t, i in head.items() if re.search(pattern, t, re.I)]
        if len(found) != 1:
            raise SystemExit(f"{doc['local_path']}: header {pattern!r} found {len(found)} times in {list(head)}")
        return found[0]

    c_cls, c_name, c_code = col(r"^Tariff Class$"), col(r"^Tariff Description"), col(r"^NTC$")
    c_perm, c_comp, c_unit = col(r"^Billing Code"), col(r"Charge Element"), col(r"^Unit$")
    c_duos, c_nuos = col(r"^DUOS\d*$"), col(r"^NUOS\d*$")
    notes_text = next(clean(c.value) for r in grid for c in r if clean(c.value).startswith("Notes:"))
    notes = dict(re.findall(r"(\d)\.\s*(.+?)(?=\s\d\.\s|$)", notes_text.replace("Notes:", "")))
    if not re.search(r"All prices exclude GST", notes_text):
        raise SystemExit(f"{doc['local_path']}: GST statement not found in {notes_text!r}")
    check_year(doc, [ws.title], "sheet title")

    def is_unit(u):
        return bool(re.search(r"\$|c/kWh", u))

    # the unit printed for each charge element, for a row whose unit cell prints something else
    unit_for = Counter((clean(r[c_comp].value), clean(r[c_unit].value)) for r in grid[h + 1:]
                       if is_unit(clean(r[c_unit].value)))

    cls = name = code = perms = name_note = prefix = ""
    pending, site = [], {}
    for r in grid[h + 1:]:
        if clean(r[c_cls].value).startswith("Notes"):
            break
        if not blank(r[c_cls].value):
            cls = clean(r[c_cls].value)
        if not blank(r[c_name].value):
            name, name_note = clean(r[c_name].value), ""
            m = re.match(r"^(.*[A-Za-z])(\d)$", name)
            if m and m.group(2) in notes:
                name, name_note = m.group(1), f"note {m.group(2)}: {notes[m.group(2)].strip()}"
            code = code_text(r[c_code].value)
            perms = clean(r[c_perm].value) if not blank(r[c_perm].value) else ""
            prefix = ""
        comp = clean(r[c_comp].value)
        if not comp:
            continue
        cell, unit = r[c_nuos], clean(r[c_unit].value)
        if blank(cell.value):
            duos = r[c_duos].value
            if site_specific(duos):
                site.setdefault(code, []).append(f"site-specific: {comp} ({unit}) published as '{clean(duos)}'")
            elif comp.endswith(":"):
                prefix = comp
            continue
        notes_ = [f"sheet '{ws.title}' row {cell.row}, NUOS column, all prices exclude GST (note 1)"]
        if name_note:
            notes_.append(name_note)
        if perms:
            notes_.append(f"billing code permutations {perms}")
        allowance = bool(prefix) and comp.startswith("Access Band")
        if allowance:
            notes_.append(prefix.rstrip(":"))
        if not is_unit(unit):
            known = [u for (c, u), _ in unit_for.most_common() if c == comp]
            if not known:
                raise SystemExit(f"{doc['local_path']} row {cell.row}: unit cell {unit!r} and no unit for {comp!r}")
            notes_.append(f"unit cell prints '{unit}'; unit as printed for '{comp}' on the other rows")
            unit = known[0]
        if unit == "$/kWh/month":
            notes_.append("unit printed '$/kWh/month'")
        kw = dict(name=name, customer_class=cls, time_band=band(comp))
        if allowance and re.match(r"network access allowance", prefix, re.I):
            kw["charge_type"] = "fixed"
        pending.append((code, comp, cell, unit, kw, notes_))
    rows = []
    for code, comp, cell, unit, kw, notes_ in pending:
        rows.append(cell_price(doc, code, comp, cell, unit, ws, note="; ".join(notes_ + site.get(code, [])), **kw))
    return pair_blocks(mark_repeats(rows))


# ======================================================================================================================
# Energex and Ergon 2020-21 to 2022-23: 'Network Tariffs <year> GST Exclusive' sheets

ZONE_SHEET = re.compile(r"^(East|West|Mt Isa) ")
FIT_GST = re.compile(r"The (\d+) c/kWh feed-in tariff rate includes GST")


def permutation_codes(codes, perms):
    """The billing code of each ACS metering permutation column. Energex: the last two digits ('00', '20', '50',
    '70'). Ergon: the letter before the transmission-zone suffix ('Blank', 'B', 'C', 'X'; ERIBBT1 is permutation B
    of ERIBT1, EVCCT1 permutation C of EVCT1)."""
    out = {}
    if all(re.fullmatch(r"\d\d", p) for p in perms.values()):
        for c in codes:
            out.setdefault(c[-2:], c)
        return out
    letter = {}
    for c in codes:
        m = re.fullmatch(r"(.*?)([A-Z])(T?\d+)", c)
        letter[c] = m.group(2) if m and m.group(2) in "BCX" else "Blank"
    for c in sorted(codes, key=len, reverse=True):
        same = [d for d in codes if letter[d] == letter[c] and d != c]
        if same and len(c) <= min(len(d) for d in same):
            letter[c] = "Blank"
    for c in codes:
        if letter[c] in out:
            raise SystemExit(f"billing codes {codes}: two codes for permutation {letter[c]}")
        out[letter[c]] = c
    return out


def qld_price_list(doc):
    wb = workbook(doc)
    rows = []
    for ws in wb.worksheets:
        grid = list(ws.iter_rows())
        h = next((i for i, r in enumerate(grid[:15]) if {"NTC", "Charging parameter", "SCS Rates"}
                  <= {clean(c.value) for c in r}), None)
        if h is None:
            continue
        titles = [clean(c.value) for r in grid[:h] for c in r if clean(c.value)]
        if not any(re.search(r"GST Exclusive", t) for t in titles):
            raise SystemExit(f"{doc['local_path']} '{ws.title}': no GST statement in {titles}")
        check_year(doc, titles, f"'{ws.title}'")
        head = {clean(c.value): c.column - 1 for c in grid[h] if clean(c.value)}
        c_name, c_code, c_comp, c_unit = head["Tariff"], head["NTC"], head["Charging parameter"], head["Units"]
        basis = {clean(c.value).rstrip("*"): c.column - 1 for c in grid[h + 1] if clean(c.value)}
        c_nuos = basis["NUOS"]
        c_parts = [basis[b] for b in ("DUOS", "DPPC", "JS")]
        meter_cols = {}
        if "ACS metering Rates" in head:
            for c in grid[h + 1]:
                if c.column - 1 >= head["ACS metering Rates"] and clean(c.value):
                    perm = clean(grid[h + 2][c.column - 1].value)
                    meter_cols[c.column - 1] = (re.sub(r"[\d,]+$", "", clean(c.value)), perm)
        zone = (ZONE_SHEET.match(ws.title).group(1)
                if doc["distributor_id"] == "ergon" and ZONE_SHEET.match(ws.title) else "")
        text = " ".join(clean(c.value) for r in grid for c in r if isinstance(c.value, str))
        fit = FIT_GST.search(text)
        cls = name = ""
        codes, site, pending = [], {}, []
        for r in grid[h + 3:]:
            nm, cd, comp = r[c_name].value, r[c_code].value, clean(r[c_comp].value)
            if not blank(nm) and blank(cd) and not comp:
                t = clean(nm)
                if not (t.startswith("*") or "DLF" in t or re.match(r"\d\.", t) or "GST" in t):
                    cls, name, codes = t, "", []
                continue
            if not blank(nm):
                name = clean(nm)
            if not blank(cd):
                codes = str(cd).split() if isinstance(cd, str) else [code_text(cd)]
            if not comp or not codes:
                continue
            code, unit = codes[0], clean(r[c_unit].value)
            cell = r[c_nuos]
            others = [] if len(codes) == 1 else [f"NTC cell lists {', '.join(codes)} (same network rates)"]
            for i, (label, perm) in meter_cols.items():
                v = r[i].value
                if numeric(v) and v != 0:
                    by_perm = permutation_codes(codes, {k: p for k, (_, p) in meter_cols.items()})
                    if perm not in by_perm:
                        UNREAD.append(f"{doc['local_path']} {ws.title}!{r[i].coordinate}: ACS metering {cell_value(r[i])} "
                                      f"for permutation '{perm}', no such billing code printed ({', '.join(codes)})")
                        continue
                    metering(doc, by_perm[perm], f"{label} ({perm})", f"ACS metering {comp} - {label}", r[i], unit, ws,
                             f"sheet '{ws.title}' ACS metering Rates, billing permutation '{perm}' of {name}"
                             + (f"; zone: {zone}" if zone else ""))
            parts = [r[i].value for i in c_parts]
            if site_specific(cell.value) or any(site_specific(v) for v in parts):
                printed = f"; printed NUOS {cell_value(cell)} is the JS part only, not read" if numeric(cell.value) else ""
                site.setdefault(code, []).append(f"site-specific: {comp} ({unit}) DUOS published as 'Site Specific'"
                                                 + printed)
                continue
            if not numeric(cell.value):
                if not blank(cell.value):
                    UNREAD.append(f"{doc['local_path']} {ws.title}!{cell.coordinate}: {code} {comp} printed as "
                                  f"'{clean(cell.value)}'")
                continue  # metering-only row (controlled load fixed charge) or N/A
            notes_ = [f"sheet '{ws.title}' row {cell.row}, NUOS column, GST exclusive"]
            if zone:
                notes_.append(f"zone: {zone}")
                if code[0] != zone[0]:
                    notes_.append(f"code printed '{code}' on the {zone} sheet (other {zone} codes start with "
                                  f"'{zone[0]}')")
            notes_ += others
            gst = "excl"
            if fit and re.search(r"volume", comp, re.I) and abs(round(cell.value * 100, 6)) == int(fit.group(1)):
                gst = "incl"
                notes_.append(f"sheet footnote: '{fit.group(0)}'")
            kw = dict(name=name, customer_class=cls, gst=gst, time_band=band(comp))
            if "MEG" in ws.title and re.search(r"volume", comp, re.I):
                kw["charge_type"] = "export"
                notes_.append("micro-embedded generation (Solar Bonus Scheme) feed-in volume charge")
            pending.append((code, comp, cell, unit, kw, notes_))
        for code, comp, cell, unit, kw, notes_ in pending:
            rows.append(cell_price(doc, code, comp, cell, unit, ws,
                                        note="; ".join(notes_ + site.get(code, [])), **kw))
        # a tariff whose every component is site-specific
        for code, notes_ in site.items():
            if not any(p[0] == code for p in pending):
                raise SystemExit(f"{doc['local_path']} {ws.title}: {code} has only site-specific prices: {notes_}")
    return pair_blocks(mark_repeats(rows))


# ======================================================================================================================
# Ergon 2015-16, 2017-18 to 2019-20: DUOS / TUOS / Jurisdictional Scheme parts, no total printed

PART_GROUPS = [(r"Distribution Use Of System", "DUoS"), (r"Transmission Use Of System", "TUoS"),
          (r"Jurisdictional Scheme", "JSA"), (r"ACS Meter Charges", "metering")]
XLSX_UNIT_WORDS = [(r"^dollars per ", "$/"), (r"kilowatt hour", "kWh"), (r"kilovolt-ampere", "kVA"), (r"kilowatt", "kW"),
              (r"of Authorised Demand", "of AD"), (r"\s+per\s+", "/"), (r"\s*/\s*", "/")]
ZONE_NAME = re.compile(r"^(East|West|Mount Isa|Mt Isa)\b")
ZONE_LETTER = {"E": "East", "W": "West", "M": "Mt Isa"}


def unit_symbol(u):
    for pattern, repl in XLSX_UNIT_WORDS:
        u = re.sub(pattern, repl, u, flags=re.I)
    return u


def ergon_parts(doc):
    wb = workbook(doc)
    rows = []
    for ws in wb.worksheets:
        if not re.search(r"\b(CAC|SAC)\b|MEG", ws.title):
            continue
        grid = list(ws.iter_rows())
        fit = FIT_GST.search(" ".join(clean(c.value) for r in grid for c in r if isinstance(c.value, str)))
        top = [clean(c.value) for r in grid[:6] for c in r if clean(c.value)]
        check_year(doc, top + [clean(c.value) for r in grid[6:12] for c in r if re.search(r"Rates$", clean(c.value))],
                   f"'{ws.title}'")
        for h, hr in enumerate(grid):
            ntc = [c.column - 1 for c in hr if clean(c.value) == "Network Tariff Code"]
            if not ntc:
                continue
            c_code = ntc[0]
            right = ntc[1] - 2 if len(ntc) > 1 else len(hr) - 1  # the GST-inclusive copy starts at its description
            labels = {c.column - 1: clean(c.value) for c in hr if clean(c.value) and c.column - 1 <= right}
            first = min(labels)
            title = next((clean(c.value) for r in reversed(grid[max(0, h - 3):h]) for c in r
                          if first <= c.column - 1 <= right and "GST" in clean(c.value)), "")
            gst = "incl" if re.search(r"GST Inclusive", title, re.I) else "excl"
            if not re.search(r"GST (Ex|In)clusive", title, re.I):
                raise SystemExit(f"{doc['local_path']} {ws.title} row {h + 1}: no GST statement in {title!r}")
            c_name = next((i for i, t in labels.items() if re.fullmatch(r"Tariff( Description)?", t, re.I)), None)
            c_group = next((i for i, t in labels.items() if t == "Tariff Group"), None)
            starts = sorted(labels)
            spans = []
            for i, t in labels.items():
                for pattern, basis in PART_GROUPS:
                    if re.search(pattern, t, re.I):
                        end = next((s for s in starts if s > i), right + 1) - 1
                        spans.append((i, end, basis, t))
            u = next(k for k in range(h + 1, h + 6) if any(
                re.search(r"^\$|dollars per", clean(grid[k][i].value), re.I) for i0, i1, _, _ in spans
                for i in range(i0, i1 + 1)))
            cols = []
            for i0, i1, basis, group in spans:
                for i in range(i0, i1 + 1):
                    unit = clean(grid[u][i].value)
                    if not unit:
                        continue
                    comp = next((clean(grid[h + 1][j].value) for j in range(i, i0 - 1, -1)
                                 if clean(grid[h + 1][j].value)), "")
                    sub = [clean(grid[k][i].value) for k in range(h + 2, u) if clean(grid[k][i].value)]
                    cols.append((i, basis, " ".join([comp] + sub), unit))
            customer_class = title.split(" GST")[0]
            feed_in = bool(re.search(r"SOLAR BONUS|MICRO-EMBEDDED", title, re.I))
            name = group = ""
            for r in grid[u + 1:]:
                cd = clean(r[c_code].value)
                if not cd:
                    break
                if c_name is not None and not blank(r[c_name].value):
                    name = clean(r[c_name].value)
                if c_group is not None and not blank(r[c_group].value):
                    group = clean(r[c_group].value)
                zm = ZONE_NAME.match(name)
                zone = (zm.group(1) if zm else ZONE_LETTER[cd[0]] if re.fullmatch(r"[EWM][A-Z0-9]*T\d", cd) else "")
                site = [f"site-specific: {basis} {comp} published as '{clean(r[i].value)}'"
                        for i, basis, comp, _ in cols if site_specific(r[i].value)]
                text = sorted({clean(r[i].value) for i, _, _, _ in cols
                               if isinstance(r[i].value, str) and not blank(r[i].value) and not site_specific(r[i].value)})
                if text:
                    UNREAD.append(f"{doc['local_path']} {ws.title} row {r[0].row}: {cd} prices printed as {text}")
                for i, basis, comp, unit in cols:
                    cell = r[i]
                    if not numeric(cell.value):
                        continue
                    if basis == "metering":
                        if cell.value != 0:
                            metering(doc, cd, comp, f"ACS Meter Charges - {comp}", cell, unit_symbol(unit), ws,
                                     f"sheet '{ws.title}' table '{customer_class}'")
                        continue
                    notes_ = [f"sheet '{ws.title}' table '{title}', {group_label(spans, i)} column; "
                              "no total network price printed"]
                    if zone:
                        notes_.append(f"zone: {zone}")
                    if group:
                        notes_.append(f"tariff group: {group}")
                    row_gst = gst
                    if fit and gst == "excl" and abs(round(cell.value * 100, 6)) == int(fit.group(1)):
                        row_gst = "incl"
                        notes_.append(f"sheet footnote: '{fit.group(0)}'")
                    kw = {}
                    if feed_in and charge_type_is_volume(comp):
                        kw["charge_type"] = "export"
                        notes_.append("micro-embedded generation (Solar Bonus Scheme) feed-in volume charge")
                    rows.append(cell_price(doc, cd, comp, cell, unit_symbol(unit), ws, name=name or cd,
                                                customer_class=customer_class, basis=basis, gst=row_gst,
                                                note="; ".join(notes_ + site), **kw))
    return pair_blocks(mark_repeats(rows))


def charge_type_is_volume(comp):
    return bool(re.search(r"volume", comp, re.I))


def group_label(spans, i):
    return next(t for i0, i1, _, t in spans if i0 <= i <= i1)


WORKBOOK_DOCUMENTS = [
    (f"{A}/ergon/2015-16/Network-Tariffs-2015-16.xlsx", ergon_parts),
    # Ergon's own 2016-17 Network Tariff Guide prints no rates ('Appendix 1 Provides a website link to our 2016-17
    # Network Tariff Codes and rates'): the AER-approved Appendix 1 tariff tables are read instead.
    (f"{A}/ergon/2016-17/AER_approved_-_Ergon_Energy_2016-17_Annual_Pricing_Proposal_-_Appendix_1_Network_Tariff_Tables"
     "_-_3_June_2016.xlsm", ergon_parts),
    (f"{A}/energex/2017-18/2017-18-Energex-Approved-Network-Prices.xlsm", energex_scs),
    (f"{A}/ergon/2017-18/2017-18-Network-Tariff-Rates.xlsx", ergon_parts),
    (f"{A}/energex/2018-19/2018-19-Network-Tariff-Tables-Att1-v-1_7-updated-for-Sched-8-03_10_18.xlsm", energex_scs),
    (f"{A}/ergon/2018-19/2018-19-Network-Tariff-Tables-with-Sch8.xlsx", ergon_parts),
    (f"{A}/energex/2019-20/2019-20-Network-Tariff-Tables-updated-for-Sch8-prices.xlsm", energex_scs),
    (f"{A}/ergon/2019-20/2019-20-Network-Tariff-Tables-updated-for-Schedule-8.xlsx", ergon_parts),
    (f"{A}/energex/2020-21/EGX-Network-Tariff-Tables-2020-21-Final-post-Sch8.xlsx", qld_price_list),
    (f"{A}/ergon/2020-21/ERG-Attachment-1-Network-Tariff-Tables-2020-21-Final-updated-for-Sch8.xlsx",
     qld_price_list),
    (f"{A}/energex/2021-22/EGX-Attachment-1-2021-22-Network-Price-List-Updated-for-Final-Sch8.xlsx",
     qld_price_list),
    (f"{A}/ergon/2021-22/ERG-Attachment-1-2021-22-Network-Price-List.xlsx", qld_price_list),
    (f"{A}/energex/2022-23/EGX-Attachment-1-2022-23-Network-Price-List-Updated-for-Sch8.xlsx", qld_price_list),
    (f"{A}/ergon/2022-23/ERG-Attachment-1-2022-23-Network-Price-List-Updated-for-Sch8.xlsx", qld_price_list),
]


# ======================================================================================================================
# Ergon Energy PDF price books and network tariff guides (2002-03 to 2014-15)
# Ergon Energy PDF price documents 2002-03 to 2016-17 (not 2015-16: read from its workbook) - part module of
# scripts/history/energex_ergon.py.
# ======================================================================================================================
# ---------------------------------------------------------------------------------------------------------- OCR access
@functools.lru_cache(maxsize=None)
def ocr_lines(path, page):
    """Token lines of an image-only page, recognised as locators.ocr_text re-reads it (rapidocr at 288 dpi): boxes
    grouped into lines by their top edge, left to right."""
    res, _ = locators._ocr_engine()(_pdf(path).pages[page - 1].to_image(resolution=288).original)
    boxes = sorted((b[0][0][1], b[0][0][0], b[1]) for b in res or [])
    out = []
    for y, x, text in boxes:
        if out and abs(out[-1][0] - y) < 18:
            out[-1][1].append((x, text))
        else:
            out.append([y, [(x, text)]])
    return [" ".join(t for _, t in sorted(ws)) for _, ws in out]


def page_lines(path, page, ocr):
    return ocr_lines(path, page) if ocr else lines(path, page)


# ======================================================================================================================
# Ergon Network Price Book 2002-03 to 2006-07 (GST inclusive: '{These prices are GST Inclusive - to determine GST
# Exclusive divide by 1.1}'). Distribution (DUOS) prices only, East and West zone tables; TUOS is printed per
# transmission connection point (Table 5) and is not read.
#   Tables 1 & 2 - CAC: <code> <description> <DLF> Site Specific <capacity $/kW/month> <actual demand $/kW/month>
#                       <volume c/kWh>     (POA cells are not prices)
#   Tables 3 & 4 - SAC: <code> <description> <DLF> <min demand kW> <service availability $/month>
#                       [<demand $/kW/month>] <volume c/kWh>
# 2005-06 and 2006-07 print these tables as images (read by OCR); their other tables (SAC < 100 MWh, controlled and
# unmetered supplies) are 'shown for information only and are not available to customers at this time'.
# ======================================================================================================================
BOOK_TABLE = re.compile(r"^Table([1-4])-(East|West)Zone$", re.I)
BOOK_CODE = re.compile(r"^(?:[EW]?CAC\d*|[EW]?D[HLMS]|VS)$")


def book_code(toks, zone):
    code = toks[0]
    if zone == "West":  # OCR reads the West prefix 'W' as 'VV', 'V' or 'WV', or drops it
        code = "W" + re.sub(r"^[VW]*", "", code)
    rest = toks[1:]
    if rest and re.fullmatch(r"22/1\s?1[BL]", rest[0]):
        code, rest = f"{code} {rest[0].replace(' ', '')}", rest[1:]
    return code, rest


def book_values(toks):
    """The price tokens at the end of a table row: '$' amounts, then the volume charge; '0 .2002' is one number."""
    toks = tokens_with_dollar_joined(" ".join(toks))
    out = []
    for t in toks:
        if out and out[-1] == "0" and re.fullmatch(r"\.\d+", t):
            out[-1] = "0" + t
        else:
            out.append(t)
    dollars = [v for v in (number(t) for t in out if t.startswith("$")) if v]
    last = out[-1] if out else ""
    volume = last if re.fullmatch(r"\d+\.\d{4}", last) and not last.startswith("$") else None
    return dollars, volume


def book_pages(path):
    """Pages headed 'TABLES 1 & 2 - CAC ...' / 'TABLES 3 & 4 - SAC ...' (not the contents pages)."""
    return [p for p in pages_with(path, r"^\S*\s*TABLES? (1 & 2|3 & 4) [–-] (CAC|SAC)[^.]*\D\s*$")
            if sum(bool(re.search(r"\s\d{1,2}$", ln)) for ln in lines(path, p)) < 8]  # contents: '... 21'


def price_book(doc, ocr=False):
    path = doc["local_path"]
    pages = book_pages(path)
    rows, table, zone, skipped = [], None, None, []
    for p in pages:
        for ln in page_lines(path, p, ocr):
            squeezed = re.sub(r"\s+", "", ln)
            m = BOOK_TABLE.match(squeezed)
            if m:
                table, zone = int(m.group(1)), m.group(2).title()
                continue
            if re.match(r"^Table\d", squeezed, re.I) or re.match(r"^\d+\.?\d*Tables?\d", squeezed, re.I):
                table = None  # a table this layout does not read (TUOS per TNI, SAC < 100 MWh for information only)
                continue
            if table is None or not ln.split():
                continue
            toks = ln.split()
            code, rest = book_code(toks, zone)
            if not BOOK_CODE.match(code.split()[0]):
                continue
            if "POA" in rest:
                skipped.append(f"{code} POA")
                continue
            dollars, volume = book_values(rest)
            note = f"zone: {zone}"
            kw = dict(basis="DUoS", gst="incl", note=note, ocr=ocr)
            if table in (1, 2):
                if len(dollars) != 2:
                    raise SystemExit(f"{path} p{p}: CAC row not read: {ln}")
                name = " ".join(t for t in rest if not t.startswith("$") and not re.fullmatch(r"[\d.]+", t)
                                and t not in ("Site", "Specific"))
                name = re.sub(r"kv\b", "kV", re.sub(r"22/1 1", "22/11", name))  # OCR spacing and case
                kw.update(name=name, customer_class="Connection Asset Customer")
                rows.append(price(doc, code, "Capacity Charge", dollars[0], "$/kW/month", p, **kw))
                rows.append(price(doc, code, "Actual Demand Charge", dollars[1], "$/kW/month", p, **kw))
            else:
                demand = code.lstrip("EW") != "VS"
                if len(dollars) != (2 if demand else 1):
                    raise SystemExit(f"{path} p{p}: SAC row not read: {ln}")
                name = " ".join(t for t in rest if re.fullmatch(r"[A-Za-z]+", t) and t != "N/A")
                kw.update(name=name, customer_class="Standard Asset Customer")
                rows.append(price(doc, code, "Service Availability Charge", dollars[0], "$/month", p,
                                       charge_type="fixed", **kw))
                if demand:
                    rows.append(price(doc, code, "Demand Charge", dollars[1], "$/kW/month", p, **kw))
            if volume is None:
                skipped.append(f"{code} volume not read (p{p})")
            else:
                rows.append(price(doc, code, "Volume Charge", volume, "c/kWh", p, **kw))
    if skipped:
        print(f"  {path}: not read: {'; '.join(skipped)}")
    return rows


# ======================================================================================================================
# Ergon Network Tariff Rate Listing 2007-08 and Network Tariff Guides 2008-09 to 2014-15: every rate table heads its
# columns with the charge's abbreviation and unit, distribution (ND..) and transmission (NT..) parts side by side,
# never a total:
#   (NDFC) fixed, (NDCC) capacity, (NDADC) actual demand, (NDVC) volume;
#   (NTFC) fixed, (NTCC) capacity, (NTCGC) common service and general, (NTVC) volume
#   unit row: 'dollars per day', 'dollars per kilowatt per month' / 'per mth', 'dollars per kilowatthour' /
#             'kilowatt hour' ('CENTS per kilowatthour' for the Solar Bonus Scheme codes)
# A row is <code> [description] [min demand] [DLF value, DLF code] <rates>; each rate is read in the column whose
# heading starts at or left of the rate's centre. From 2010-11 each table is printed twice, '... GST Exclusive' and
# '... GST Inclusive': the exclusive one is read. The Solar Bonus Scheme feed-in codes (NVG*, GVG*) carry the
# statement 'The 44c/Kwh associated with the Solar PV Bonus Scheme includes GST (if any)': gst 'incl'.
# Not read: ICC codes (individually calculated customers), POA cells, tables 'provided for information only as they
# are not available to customers at this time'.
# ======================================================================================================================
COLUMN = {  # abbreviation -> (component, basis, charge type, unit when the heading's unit cannot be read)
    "NDFC": ("Fixed Charge (NDFC)", "DUoS", "fixed", "$/day"),
    "NDCC": ("Capacity Charge (NDCC)", "DUoS", "capacity", "$/kW/month"),
    "NDADC": ("Actual Demand Charge (NDADC)", "DUoS", "demand", "$/kW/month"),
    "NDVC": ("Volume Charge (NDVC)", "DUoS", "energy", "$/kWh"),
    "NTFC": ("Fixed Charge (NTFC)", "TUoS", "fixed", "$/day"),
    "NTCC": ("Capacity Charge (NTCC)", "TUoS", "capacity", "$/kW/month"),
    "NTCGC": ("Fixed Common Service and General Charge (NTCGC)", "TUoS", "fixed", "$/day"),
    "NTVC": ("Volume Charge (NTVC)", "TUoS", "energy", "$/kWh"),
}
ABBREV = re.compile(r"^\((N[DT][A-Z]{2,4})\)$")
TARIFF_CODE = re.compile(r"^(?:[EW][A-Z]+\d[A-Z0-9]*|MI[A-Z]{2,}|[NG]VG\d+)\**$")
PDF_UNIT_WORDS = {"dollars", "cents", "per", "day", "kilowatt", "kilowatthour", "hour", "month", "mth"}
ERG_SOLAR = re.compile(r"^[NG]VG\d+$")


def heading_unit(words):
    """'$/day', '$/kW/month', '$/kWh' or 'c/kWh' from the unit words under one column heading, else None."""
    t = " ".join(words).lower()
    sign = "c" if t.startswith("cents") else "$" if t.startswith("dollars") else None
    if sign is None:
        return None
    if re.fullmatch(r"\w+ per day", t):
        return f"{sign}/day"
    if re.fullmatch(r"\w+ per kilowatt per (month|mth)", t):
        return f"{sign}/kW/month"
    if re.fullmatch(r"\w+ per (kilowatthour|kilowatt hour)", t):
        return f"{sign}/kWh"
    return None


def word_rows(path, page):
    """Words of a page grouped into printed rows (top within 2.5 pt), left to right."""
    rows = []
    for w in sorted(words(path, page), key=lambda w: (round(w["top"]), w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) < 2.5:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def code_descriptions(path):
    """'<code> <description>' lines of the '(i) Network Tariff Code & Descriptions' lists."""
    out = {}
    for p in range(1, n_pages(path) + 1):
        for ln in lines(path, p):
            m = re.match(r"^([EW][A-Z]+\d[A-Z0-9]*|MI[A-Z]{2,}|[NG]VG\d+) ((?:East|West|Mt Isa|Net|Gross)\b[^$]*)$",
                         ln)
            if m:
                out.setdefault(m.group(1), m.group(2).strip())
    return out


def solar_statement(path, page):
    """The Solar Bonus Scheme GST statement printed under the feed-in rates."""
    for p in [page] + list(range(1, n_pages(path) + 1)):  # the rate page, else where the scheme is described
        m = re.search(r"The [^.]*?Solar (?:PV )?Bonus Scheme (?:includes?|include) GST \(if any\)\.",
                      " ".join(lines(path, p)))
        if m:
            return "Solar Bonus Scheme feed-in tariff: " + m.group(0) + ("" if p == page else f" (p{p})")
    raise SystemExit(f"{path} p{page}: no Solar Bonus Scheme GST statement")


def abbrev_tables(doc, pages=None):
    path = doc["local_path"]
    names = code_descriptions(path)
    rows, unread = [], []
    gst, info_only, site, cols, units, header_rows, in_data = "excl", False, False, [], {}, [], False
    last_abbrev, centres = (0, 0), []
    recent = []  # rows printed since the last rate row: the heading above a table's abbreviations
    for p in pages or range(1, n_pages(path) + 1):
        for wr in word_rows(path, p):
            text = " ".join(w["text"] for w in wr)
            if re.search(r"GST Inclusive", text):
                gst = "incl"
            elif re.search(r"GST Exclusive", text):
                gst = "excl"
            if re.search(r"information only", text, re.I):
                info_only = True
            if re.search(r"SITE[- ]SPECIFIC (RATES|NETWORK TARIFF)", text):
                site = True
            elif re.search(r"STANDARD RATES|STANDARD ASSET CUSTOMERS|Network Tariff Codes? (&|and)? ?Rates", text):
                site = False
            abbrevs = [w for w in wr if ABBREV.match(w["text"])]
            if abbrevs:
                if in_data or not header_rows or last_abbrev[0] != p or abbrevs[0]["top"] - last_abbrev[1] > 40:
                    # a new table's heading
                    cols, header_rows, in_data = [], [], False
                    # 2009-10: 'Minimum monthly charge if no usage recorded (based on 30 days in month)', an example
                    # amount, not a rate
                    cols = [(r[i - 1]["x0"] if i and r[i - 1]["text"] == "Minimum" else w["x0"], "MINIMUM")
                            for q, r in recent for i, w in enumerate(r)
                            if q == p and w["text"] == "monthly" and 0 < abbrevs[0]["top"] - w["top"] < 40]
                cols = sorted(cols + [(w["x0"], ABBREV.match(w["text"]).group(1)) for w in abbrevs])
                centres = [((w["x0"] + w["x1"]) / 2, ABBREV.match(w["text"]).group(1)) for w in abbrevs] + (
                    centres if header_rows else [])
                header_rows.append(wr)
                units, last_abbrev = {}, (p, abbrevs[0]["top"])
                continue
            if not cols:
                recent.append((p, wr))
                continue

            def column(w):
                c = (w["x0"] + w["x1"]) / 2
                left = [k for x, k in cols if x <= c + 1]
                return left[-1] if left else None

            vals = [(column(w), w) for w in wr
                    if column(w) and re.fullmatch(r"-?\$?-?[\d,]*\.\d+|POA|N/A", w["text"])]
            if not vals:
                recent.append((p, wr))
                if not in_data:
                    header_rows.append(wr)
                    for w in wr:  # a unit word belongs to the nearest abbreviation (headings may be centred)
                        if w["text"].lower() in PDF_UNIT_WORDS and centres:
                            c = (w["x0"] + w["x1"]) / 2
                            units.setdefault(min(centres, key=lambda a: abs(a[0] - c))[1], []).append(w)
                continue
            recent = []
            if not in_data:  # first data row of a table: the heading's units, the information-only state
                units = {k: heading_unit([w["text"] for w in sorted(ws, key=lambda w: (round(w["top"]), w["x0"]))])
                         for k, ws in units.items()}
                skip_table, info_only = info_only, False
                in_data = True
            codes = [w["text"] for w in wr if column(w) is None and TARIFF_CODE.match(w["text"])]
            if not codes:
                unread.append(f"p{p} no code: {text}")
                continue
            code = codes[0].rstrip("*")
            after = [w["text"] for w in wr[next(i for i, w in enumerate(wr) if w["text"] == codes[0]) + 1:]
                     if column(w) is None]
            row_name = " ".join(t for t in after if not re.fullmatch(r"[\d.,]+|G[EWM][HL]L", t))
            if skip_table or "ICC" in code:
                continue
            solar = bool(ERG_SOLAR.match(code))
            if gst == "incl" and not solar:
                continue
            seen = [k for k, _ in vals]
            if len(seen) != len(set(seen)):
                raise SystemExit(f"{path} p{p}: two rates in one column: {text} {cols}")
            footnote = ""
            if solar and len(code) > 4:  # 'NVG2' with footnote mark 6: 'NVG26'
                code, mark = code[:4], code[4:]
                footnote = next((ln[len(mark):].strip() for ln in lines(path, p) if ln.startswith(mark + " ")), "")
            note = "; ".join(n for n in (
                "site-specific" if site else "",
                "additional (unregulated) network charges apply for this site" if codes[0].endswith("**") else "",
                solar_statement(path, p) if solar else "", footnote) if n)
            for k, w in vals:
                v = number(w["text"])
                if v is None or k == "MINIMUM":
                    continue  # POA, N/A
                component, basis, ctype, default_unit = COLUMN[k]
                unit = units.get(k) or default_unit
                unit_note = ""
                if solar and unit == "$/kWh" and "$" not in w["text"]:
                    # 2010-11: the heading reads 'dollars per kilowatt hour' over '-44.00', the '44c/Kwh' of the
                    # statement under the table (other years print 'CENTS per kilowatthour' over these figures)
                    unit, unit_note = "c/kWh", "column heading reads 'dollars per kilowatt hour'; the rate is in cents"
                if units.get(k) and units[k][-4:] != default_unit[-4:] and not solar:
                    raise SystemExit(f"{path} p{p}: {k} heading unit {units[k]} differs from {default_unit}")
                rows.append(price(doc, code, component, v, unit, p, name=names.get(code) or row_name, basis=basis,
                                       gst="incl" if solar else gst, note="; ".join(n for n in (note, unit_note) if n),
                                       charge_type="export" if solar else ctype))
    if unread:
        print(f"  {path}: rows with rates but no code:\n    " + "\n    ".join(unread))
    return rows


# ======================================================================================================================
# Ergon Network Tariff Guide 2014-15, Appendix 2 '2014-15 Network Tariff Codes and rates - GST Exclusive' (then the
# same tables GST Inclusive): ruled tables without charge abbreviations. Each rate column is headed by its label
# ('Fixed Charge', 'Actual Demand Charge', 'Capacity Charge', 'Volume Charge' with 'Peak' / 'Shoulder' / 'Off-Peak' or
# 'Block 1..3' sub-columns) and unit ('dollars per day', ...), under 'Distribution Use Of System (DUOS)' or
# 'Transmission Use Of System (TUOS)'. A rate's heading is the header words over its cell.
# ======================================================================================================================
GROUP_WORDS = {"Distribution", "Transmission", "Use", "Of", "System", "(DUOS)", "(TUOS)"}


def ruled_rates(doc):
    path = doc["local_path"]
    names = code_descriptions(path)
    rows = []
    for p in range(1, n_pages(path) + 1):
        title = " ".join(lines(path, p)[:2])
        m = re.search(r"Network Tariff Codes and rates – GST (Exclusive|Inclusive)", title)
        if not m:
            continue
        gst = "excl" if m.group(1) == "Exclusive" else "incl"
        page = _pdf(path).pages[p - 1]
        page_words = words(path, p)
        notes = {ln.split()[0]: ln for ln in lines(path, p) if re.match(r"^\*+ ", ln)}
        for tbl in page.find_tables():
            data = []
            for r, cells in zip(tbl.rows, tbl.extract()):
                code = next((c for c in cells if c and TARIFF_CODE.match(c.strip())), None)
                if code:
                    data.append((code.strip(), r.cells, cells))
            if not data:
                continue
            top = min(b[1] for b in data[0][1] if b)
            # the TUOS group heading is centred over its columns: a rate cell reaching past its first word is TUOS
            tuos_x = min((w["x0"] for w in page_words if w["text"] == "Transmission" and w["top"] < top), default=None)
            group = min((w["top"] for w in page_words if w["text"] in ("(DUOS)", "(TUOS)") and tbl.bbox[1] - 1 <= w["top"] < top),
                        default=tbl.bbox[1] - 1)
            header = [w for w in page_words if group - 1 <= w["top"] < top and w["text"] not in GROUP_WORDS]
            for code, boxes, cells in data:
                solar = bool(ERG_SOLAR.match(code))
                if gst == "incl" and not solar:
                    continue
                for box, cell in zip(boxes, cells):
                    m = re.fullmatch(r"(-?\$?-?[\d,]*\.\d+|POA)\s*(\**)", (cell or "").strip())
                    if not box or not m:
                        continue
                    x0, x1 = box[0], box[2]
                    over = sorted((w for w in header if x0 - 1 <= (w["x0"] + w["x1"]) / 2 <= x1 + 1),
                                  key=lambda w: (round(w["top"]), w["x0"]))
                    if any(re.fullmatch(r"Value|Code|kilowatts", w["text"]) for w in over):
                        continue  # DLF value / code, threshold demand
                    unit = heading_unit([w["text"] for w in over if w["text"].lower() in PDF_UNIT_WORDS])
                    label = " ".join(w["text"] for w in over if w["text"].lower() not in PDF_UNIT_WORDS
                                     and w["text"] != "*")
                    if re.fullmatch(r"Peak|Shoulder|Off-Peak|Block \d", label):
                        label = f"Volume Charge {label}"
                    if not unit or not re.search(r"Charge", label):
                        raise SystemExit(f"{path} p{p}: column of {code} {cell} not read: {label!r} {unit!r}")
                    v = number(m.group(1))
                    if v is None:
                        continue  # POA
                    basis = "TUoS" if tuos_x is not None and x1 > tuos_x else "DUoS"
                    marks = [c.strip() for c in cells if c and re.fullmatch(r"\*+", c.strip())] + [m.group(2)] * bool(m.group(2))
                    note = "; ".join(n for n in (
                        solar_statement(path, p) if solar else "",
                        *(notes.get(k, "")[len(k):].strip() for k in marks)) if n)
                    rows.append(price(doc, code, label, v, unit, p, name=names.get(code, ""), basis=basis,
                                           gst=gst, note=note, charge_type="export" if solar else None))
    return rows


# ======================================================================================================================
# One document per pricing year, the latest release (the version customers were billed on):
#   2004-05  the '17 June' copy: Release 2 of the price book, 'Updated TUOS Fixed Charge ($/month) in Table 5 for
#            QASF & QGAR'; its DUOS tables print the same rates as the other copy
#   2006-07  Release 2 (1 December 2006): Release 1 and the QCA-hosted copy are the same book; Release 2 only renames
#            the Woolooga TNI (Table 5) and prints the same DUOS tables
#   2007-08  the Network Tariff Rate Listing Release 2 (1 August 2007): the Tariff Guide Release 2 refers to it for
#            the rates and prints none
#   2009-10  the QCA-hosted Network Use of System Tariff Guide 2009-10 Release 2 (20 November 2009)
# Not read: 2003-04 (no document held); 2015-16 (read from its workbook); 2016-17: the Network Tariff Guide prints no
# rate tables ('Appendix 1 Provides a website link to our 2016-17 Network Tariff Codes and rates'), only worked
# examples.
ERG = f"{A}/ergon"
ERGON_PDF_DOCUMENTS = [
    (f"{ERG}/2002-03/Ergon_Network_Price_Book_2002-2003_Release2.pdf", price_book),
    (f"{ERG}/2004-05/Ergon_Network_Price_Book_2004-05_17June.pdf", price_book),
    (f"{ERG}/2005-06/Ergon_Network_Price_Book_2005-06_Release_3.pdf", lambda d: price_book(d, ocr=True)),
    (f"{ERG}/2006-07/Ergon_Network_Price_Book_2006-07_Release_2.pdf", lambda d: price_book(d, ocr=True)),
    (f"{ERG}/2007-08/Network_Tariff_Rate_Listing_Release_2.pdf", abbrev_tables),
    (f"{ERG}/2008-09/Ergon_Network_Tariff_Guide_2008-09_Release_2.pdf", abbrev_tables),
    (f"{ERG}/2009-10/E-distprice-DistServ-price-Ergon09-10-0410.pdf", abbrev_tables),
    (f"{ERG}/2010-11/EE-SCS-Network-Tariff-Guide-2010-11-FINAL_Release-1_7June2010.pdf", abbrev_tables),
    (f"{ERG}/2011-12/EE-Network-Tariff-Guide-for-SCS-2011-12-Release-1_10June2011.pdf", abbrev_tables),
    (f"{ERG}/2012-13/EE-Network-Tariff-Guide-for-SCS-2012-13-FINAL_Release-2_9July2012.pdf", abbrev_tables),
    (f"{ERG}/2013-14/EE-Network-Tariff-Guide-for-SCS-2013-14_Release-1-FINAL_June2013.pdf", abbrev_tables),
    (f"{ERG}/2014-15/Nework-tariff-guide-for-SCS.pdf", ruled_rates),
]

# ======================================================================================================================
# document per distributor and pricing year
# ======================================================================================================================
DOCUMENTS = [
    (f"{A}/energex/2000-01/Network_Price_List_Contestable_Customers.pdf", energex_contestable),
    (f"{A}/energex/2002-03/energex_02_03_price_book.pdf", energex_contestable),
    (f"{A}/energex/2003-04/ENERGEX_2003_04_Pricebook.pdf", energex_contestable),
    (f"{A}/energex/2004-05/network_prices_0405.pdf", energex_contestable),
    (f"{A}/energex/2005-06/Pricing_Book_200506_2.pdf", energex_2005_2006),
    (f"{A}/energex/2006-07/Network_Pricing_Schedule_2006_07.pdf", energex_2005_2006),
    (f"{A}/energex/2007-08/Network_Pricing_Schedule_2007_rev_13112007.pdf", energex_2007_08),
    (f"{A}/energex/2008-09/Network_Pricing_Schedule_2008_09_rev_29052008.pdf", energex_2008_10),
    (f"{A}/energex/2008-09/PV_Tariff_2008-09.pdf", energex_pv_2008_09),
    (f"{A}/energex/2009-10/Tariff_schedule_2009-10_22072009.pdf", energex_2008_10),
] + EGX_PDF_DOCUMENTS + WORKBOOK_DOCUMENTS + ERGON_PDF_DOCUMENTS
DOCUMENTS.sort(key=lambda d: (d[0].split("/")[2], d[0].split("/")[3]))  # distributor, pricing year


def main():
    rows = []
    for path, fn in DOCUMENTS:
        doc = common.document(path)
        got = fn(doc)
        if not got:
            raise SystemExit(f"{path}: no rows read")
        print(f"{doc['distributor_id']:8} {doc['pricing_year']:8} {len(got):5} rows  "
              f"{len({r['tariff_code'] for r in got}):3} codes  {path}")
        rows += got
    common.write(SLUG, rows)
    schema.write_metering(f"history_{SLUG}", METERING)
    print(f"metering: {len(METERING)} rows")
    for line in UNREAD:
        print(f"not read: {line}")


if __name__ == "__main__":
    main()
