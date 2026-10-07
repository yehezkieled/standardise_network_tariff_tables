#!/usr/bin/env python
"""Essential Energy (Country Energy until 2011) and Evoenergy (ActewAGL until 2017) network price lists for the pricing
years before 2023-24, read into out/history/essential_evoenergy.csv (contract: scripts/history/CONTRACT.md).

One document per distributor-year: the distributor's own published network price list (DOCS). Layouts:

  booklet      Country Energy "Network Price List" booklets 2001-02 to 2006-07: one ruled two-column table per tariff
               ("Tariff Code", Description, Type, ..., "Unit | Rate", then one row per charge with the unit in the
               label, e.g. "Network Access Charge $ / Day | 0.24609"). GST exclusive (stated on the cover / clause 1).
  wide         Country Energy / Essential Energy price sheets 2007-08 to 2022-23 (wide_obsolete: the separate
               obsolete-tariff lists of 2007-08 and 2008-09): ruled wide tables, one row per
               tariff code, one column per charge whose heading carries the unit ("Network Access $/Day",
               "Energy Peak c/kWh", "Peak Demand $/kVA/M"). From 2011-12 every table is printed GST exclusive and
               again GST inclusive: only tables titled "(Excluding GST)" are read. Codes in the "Obsolete tariffs on
               same rate" column are priced by the same row.
  evo_brochure ActewAGL / Evoenergy "Electricity network charges" schedules 2006-07 to 2018-19: one or two text
               columns per page; a bold "<code> <name>" heading, then bullet items "a network access charge per day
               12.50c 13.75c" with the GST exclusive rate first and the GST inclusive rate second.
  evo_table    Evoenergy schedules 2019-20 to 2022-23: "Network Use of System charges (excluding GST)" tables with
               metering capital / non-capital, fixed, energy, demand and capacity columns; an XMC code printed under
               its base code takes the base prices without the metering capital charge. The metering columns are
               per-tariff metering charges: written to the metering side output. The feed-in codes at the end of
               the schedule are read as evo_brochure (one text column).

Run from the repository root:  .venv/bin/python scripts/history/essential_evoenergy.py
"""
import functools
import os
import re
import sys
from collections import defaultdict

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402
sys.path.insert(0, os.path.join(common.ROOT, "scripts", "tariffdb"))
import locators  # noqa: E402
import schema  # noqa: E402

SLUG = "essential_evoenergy"
A = "sources/archive/"
REPORT = []  # values printed but not emitted, with the reason (printed at the end of a run)


def report(doc, locator, what):
    REPORT.append(f"{doc['local_path']} {locator}: {what}")


@functools.lru_cache(maxsize=None)
def tables_of(path):
    """[(page number, [rows of cell text])] of every ruled table pdfplumber finds."""
    out = []
    with pdfplumber.open(os.path.join(common.ROOT, path)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            for t in page.find_tables():
                out.append((i, [[c if c is not None else None for c in r] for r in t.extract()]))
    return out


def flat(s):
    return re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip()


NUM = re.compile(r"^-?\$?-?\d[\d,]*(?:\.\d+)?$")


def number(cell):
    """The printed number of a cell ('0 .4526' -> '0.4526', '$5.552' -> '5.552', '- 20.0000' -> '-20.0000'), or None."""
    s = re.sub(r"\s+", "", cell or "")
    if not s or not NUM.match(s):
        return None
    neg = s.startswith("-") or s.startswith("$-")
    s = s.replace("$", "").replace("-", "").replace(",", "")
    return ("-" if neg else "") + s


CODE = re.compile(r"^[A-Z][A-Z0-9]{0,7}$")


# ---------------------------------------------------------------------------------------------------------------
# Country Energy booklets 2001-02 .. 2006-07
# ---------------------------------------------------------------------------------------------------------------
def booklet_codes(text):
    """(codes, also-obsolete codes, closed) from a 'Tariff Code' cell: 'BLNN1CU & BLNN2CU (Obsolete)',
    'BLND3AO\\n(Also Obsolete Tariff BLND3NO & BLND3NU)'."""
    t = flat(text)
    closed = bool(re.search(r"\(Obsolete\)", t))
    also = []
    m = re.search(r"\((?:also|Also)\s+Obsolete\s+Tariffs?\s+([^)]*)\)", t)
    if m:
        also = [c for c in re.split(r"[\s,&]+", m.group(1)) if CODE.match(c) and len(c) >= 5]
        t = t[:m.start()] + t[m.end():]
    t = re.sub(r"\(Obsolete\)", "", t)
    codes = [c for c in re.split(r"[\s,&]+", t) if CODE.match(c) and len(c) >= 5]
    return codes, also, closed


UNIT_IN_LABEL = re.compile(r"(\$\s*/\s*kVA\s*/\s*Month|/\s*kVA\s*/\s*Month|\$\s*/\s*(?:Day|day|Month|kWh)|c\s*/\s*kWh|%)")


def one_table_per_code(doc, rows):
    """A code printed in several tariff tables (an obsolete code listed beside an urban and a rural tariff): kept once
    when every table prints the same price, left out (and reported) when the tables disagree."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["tariff_code"], r["component"], r["time_band"], r["unit"], r["note"])].append(r)
    by_code = defaultdict(list)
    for k, g in groups.items():
        by_code[k[0]].append(g)
    out = []
    for code, gs in by_code.items():
        clash = [g for g in gs if len({r["value"] for r in g}) > 1]
        if clash:
            report(doc, ",".join(sorted({r["locator"] for g in clash for r in g})),
                   f"{code} printed in several tariff tables with different prices "
                   f"({'; '.join(g[0]['component'] + ' ' + '/'.join(r['value'] for r in g) for g in clash)}), left out")
            continue
        out += [g[0] for g in gs]
    return out


def booklet(doc):
    rows = []
    for page, table in tables_of(doc["local_path"]):
        head = next((i for i, r in enumerate(table) if flat(r[0]).startswith("Tariff Code")), None)
        if head is None:
            continue
        cells = [flat(c) for c in table[head][1:] if c]
        codes, also, closed = booklet_codes(" ".join(cells))
        if not codes:
            continue
        info = {flat(r[0]).rstrip(":"): flat(" ".join(c for c in r[1:] if c)) for r in table if r[0]}
        name, cls = info.get("Description", ""), info.get("Type", "")
        export = "export" in name.lower()
        start = next((i for i, r in enumerate(table) if any(flat(c) in ("Unit Rate",) for c in r if c)), None)
        if start is None:
            report(doc, locators.pdf(page), f"{codes}: no 'Unit Rate' heading")
            continue
        zone = ""
        for r in table[start + 1:]:
            label_raw = r[0] or ""
            values = [c for c in r[1:] if c and flat(c)]
            if not flat(label_raw):
                if values and "Zone" in values[0]:
                    zone = flat(values[0])
                continue
            label = flat(label_raw)
            if label.startswith("Distribution Loss Factor") or not values:
                continue
            value = number(values[-1])
            if value is None:  # 'Negotiated Tariff Refer to Customer'
                continue
            dollar = "$" in values[-1]
            m = UNIT_IN_LABEL.search(label)
            unit = re.sub(r"\s+", "", m.group(1)) if m else ""
            component = flat((label[:m.start()] + " " + label[m.end():]) if m else label)
            component = component.replace("$/", "").strip()
            if unit.startswith("/"):
                unit = ("$" if dollar else "") + unit
            if unit == "$/kWh" and "Access" in component:
                report(doc, locators.pdf(page), f"{codes} '{label}' {values[-1]}: a network access charge printed per "
                                                f"kWh, left out")
                continue
            if not unit:
                report(doc, locators.pdf(page), f"{codes} '{label}' {values[-1]}: no unit, left out")
                continue
            if unit.lower() == "$/day" or unit.lower() == "$/month":
                unit = "$/" + unit.split("/")[1].capitalize()
            tb, note = None, []
            low = component.lower()
            m4 = re.match(r"Energy Block (\d+)", component)
            if m4 and int(m4.group(1)) > 3:
                report(doc, locators.pdf(page), f"{codes} '{label}' {values[-1]}: a fourth energy block, which the tariff "
                                                f"database cannot hold (three blocks at most), left out")
                continue
            if "kWh" in unit:
                if "peak & shoulder" in low:
                    tb = "peak"
                    note.append("one rate for the peak and shoulder periods")
                elif re.search(r"<\s*[\d,]+\s*kWh per month", component):
                    tb = "block1"
                elif re.search(r">\s*[\d,]+\s*kWh per month", component):
                    tb = "block2"
                elif "all periods" in low or "flat rate" in low:
                    tb = "anytime"
            charge = "export" if export and "kWh" in unit else None
            if zone:
                note.append(f"zone: {zone}")
            if closed:
                note.append("closed to new customers")
            for code in codes + also:
                n = list(note)
                if code in also:
                    n.append(f"obsolete code printed with {codes[0]}")
                rows.append(common.row(doc, code, component, value, unit, locators.pdf(page), name=name,
                                       customer_class=cls, note="; ".join(n), charge_type=charge, time_band=tb))
    return one_table_per_code(doc, rows)


# ---------------------------------------------------------------------------------------------------------------
# Country Energy / Essential Energy price sheets 2007-08 .. 2022-23
# ---------------------------------------------------------------------------------------------------------------
HEAD_UNIT = re.compile(r"(\$/day|\$/Day|c/kWh|\$/kVA/Mth|\$/kVA/M)\b")
EXPORT_NAME = re.compile(r"export|solar|feed|avoided tuos|generat", re.I)


def wide_column(head):
    """(component, unit) of a price column heading such as 'Energy\\nPeak\\nc/kWh' or 'Network\\nAccess\\n$/Day';
    None for other columns."""
    h = flat(head)
    h = re.sub(r"\b(Excluding GST|GST Inclusive)\b", "", h).strip()
    m = HEAD_UNIT.search(h)
    if not m:
        return None
    unit = m.group(1)
    unit = {"$/day": "$/Day", "$/kVA/Mth": "$/kVA/Month", "$/kVA/M": "$/kVA/Month"}.get(unit, unit)
    component = flat(h[:m.start()] + " " + h[m.end():])
    return component, unit


GST_INCL = re.compile(r"Including GST|GST Inclusive|GST-inclusive", re.I)
GST_EXCL = re.compile(r"Excluding GST|GST exclusive|GST-exclusive", re.I)


@functools.lru_cache(maxsize=None)
def page_texts(path):
    with pdfplumber.open(os.path.join(common.ROOT, path)) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


def codes_in(text):
    toks = [t for t in re.split(r"[\s,]+", flat(text)) if t]
    if toks and all(CODE.match(t) for t in toks):
        return toks
    return []


def wide_header(r):
    """The column layout a heading row prints: {index: (component, unit)}, the 'Obsolete tariffs on same rate' column,
    whether it heads obsolete tariffs, and the column labels in order."""
    cols = {j: wide_column(c) for j, c in enumerate(r) if c and wide_column(c)}
    return {"cols": cols, "same": next((j for j, c in enumerate(r) if c and "same rate" in flat(c)), None),
            "obsolete": any(c and "Obsolete" in flat(c) and "same rate" not in flat(c) for c in r),
            "sequential": False}


def wide(doc):
    """Wide price tables: the heading row names each column; a title row '(Excluding GST)' / '(Including GST)' (or the
    page heading) says which copy follows. An 'Obsolete Tariffs' section printed under the current-tariff heading (no
    'Demand Charge' column) takes the document's obsolete-tariff heading, its values read in column order."""
    found = []  # (code, component, unit, value, page, name, note, export, own row)
    texts = page_texts(doc["local_path"])
    obsolete_head = None
    for page, table in tables_of(doc["local_path"]):
        for r in table:
            if sum(1 for c in r if c and wide_column(c)) >= 1 and any(c and "Obsolete" in flat(c) and "same rate"
                                                                      not in flat(c) for c in r):
                obsolete_head = obsolete_head or wide_header(r)
    last_page, head, gst = None, None, None
    for page, table in tables_of(doc["local_path"]):
        if page != last_page:
            last_page, head = page, None
            m = re.search(f"{GST_INCL.pattern}|{GST_EXCL.pattern}", texts[page - 1], re.I)
            gst = None if not m else ("incl" if GST_INCL.search(m.group(0)) else "excl")
        for r in table:
            text = " ".join(flat(c) for c in r if c)
            if sum(1 for c in r if c and wide_column(c)) >= 1:
                head = wide_header(r)
                if GST_INCL.search(text):
                    gst = "incl"
                elif GST_EXCL.search(text):
                    gst = "excl"
                continue
            if not any(codes_in(c) for c in r[:3] if c):
                if GST_INCL.search(text):
                    gst = "incl"
                elif GST_EXCL.search(text):
                    gst = "excl"
                if head and text.startswith("Obsolete Tariffs") and not head["obsolete"]:
                    if not any("Demand Charge" in c for c, _ in head["cols"].values()) and obsolete_head:
                        head = dict(obsolete_head, sequential=True)
                continue
            if head is None:
                continue
            if gst != "excl":
                if gst is None:
                    report(doc, locators.pdf(page), "table without a GST statement, left out")
                continue
            first = min(head["cols"])
            ci = next((j for j, c in enumerate(r) if c and flat(c)), None)
            codes = codes_in(r[ci])
            if not codes:
                continue
            same_col = head["same"]
            name_j = next((j for j, c in enumerate(r) if j > ci and (head["sequential"] or j < first) and j != same_col
                           and c and flat(c) and flat(c) not in ("Yes", "No", "N/A")
                           and number(c) is None), None)
            name = flat(r[name_j]) if name_j is not None else ""
            same = codes_in(r[same_col]) if same_col is not None and same_col < len(r) and r[same_col] else []
            if head["sequential"]:  # values in column order after the description
                cells = [c for c in r[(name_j if name_j is not None else ci) + 1:] if c is not None]
                pairs = list(zip(sorted(head["cols"]), cells))
            else:
                pairs = [(j, r[j]) for j in sorted(head["cols"]) if j < len(r)]
            export = bool(EXPORT_NAME.search(name)) or any(c.startswith("BLNE") for c in codes)
            closed = head["obsolete"] or head["sequential"]
            for j, cell in pairs:
                component, unit = head["cols"][j]
                value = number(cell)
                if value is None:
                    continue
                for code in codes + same:
                    note = []
                    if closed:
                        note.append("closed to new customers")
                    if code in same:
                        note.append(f"obsolete code billed at the rate of {codes[0]}")
                    if len(codes) > 1:
                        note.append(f"one row for {' '.join(codes)}")
                    found.append((code, component, unit, value, page, name, "; ".join(note), export, code not in same))
    rows, keep = [], {}
    for f in found:  # one price per code and column: the code's own row wins over an 'on same rate' listing
        key = f[:3]
        if key not in keep or (f[8] and not keep[key][8]):
            keep[key] = f
        elif f[8] == keep[key][8] and f[3] != keep[key][3]:
            report(doc, locators.pdf(f[4]), f"{f[0]} {f[1]} printed twice ({keep[key][3]} p{keep[key][4]}, {f[3]})")
    energy = defaultdict(int)
    for f in keep.values():
        energy[f[0]] += f[2] == "c/kWh"
    for code, component, unit, value, page, name, note, export, _ in keep.values():
        tb = None
        if export and unit == "c/kWh" and energy[code] == 1 and "All" not in component:
            # a solar scheme rate printed in one time-of-use column of a table without an 'All' column
            tb = "anytime"
            note = "; ".join(x for x in (note, f"the only energy rate printed, in the '{component}' column") if x)
        if tb is None and re.search(r"\bAll\b", component):
            tb = "anytime"
        rows.append(common.row(doc, code, component, value, unit, locators.pdf(page), name=name, note=note,
                               charge_type="export" if export and unit == "c/kWh" else None, time_band=tb))
    return rows


# ---------------------------------------------------------------------------------------------------------------
# ActewAGL / Evoenergy schedules of network charges 2006-07 .. 2018-19
# ---------------------------------------------------------------------------------------------------------------
PRICE_TOKEN = re.compile(r"^(-?)(\$)?(-?\d[\d,]*\.\d+)(¢|c)?(/kWh|/kVA|/kW|/day|/W)?$")
UNIT_WORDS = {"per", "kWh", "kVA", "KVA", "kW", "day", "W"}
BULLETS = ("•", "(cid:131)", "\uf0b7")
HEADER = re.compile(r"^(\d{3,4})\s+(\S.*)$")
SECTION_STOP = re.compile(r"^(Metering charges|Miscellaneous charges|Loss factors|MP\d\b|Metering services|"
                          r"Alternative control services|Application of rates)")


def brochure_lines(page, x0, x1):
    """Lines (top, [words]) of the words whose x0 lies in [x0, x1), top to bottom."""
    words = [w for w in page.extract_words(keep_blank_chars=False) if x0 <= w["x0"] < x1]
    words.sort(key=lambda w: (round(w["top"]), w["x0"]))
    lines = []
    for w in words:
        if lines and abs(lines[-1][0] - w["top"]) <= 3.5:  # a code printed 3pt above or below its name
            lines[-1][1].append(w)
        else:
            lines.append((w["top"], [w]))
    out = []
    for t, ws in lines:
        ws = sorted(ws, key=lambda w: w["x0"])
        for k in range(len(ws) - 2, -1, -1):  # a price printed with a space after its point (2020-21 '-36. 900c')
            if re.fullmatch(r"-?\$?\d[\d,]*\.", ws[k]["text"]) and re.fullmatch(r"\d+(¢|c)?", ws[k + 1]["text"]):
                ws[k:k + 2] = [{**ws[k], "text": ws[k]["text"] + ws[k + 1]["text"], "x1": ws[k + 1]["x1"]}]
        out.append((t, ws))
    return out


def brochure_band(label):
    l = label.lower()
    if re.search(r"\b(at|at controlled) (max|business|peak)\b|\b(max|business) times", l):
        return "peak"
    if re.search(r"\b(at|at controlled) (mid|evening)\b|\b(mid|evening) times", l):
        return "shoulder"
    if re.search(r"\b(at|at controlled) (economy|off-peak)\b|\beconomy times", l):
        return "offpeak"
    tb = schema.time_band_from_label(label)
    if tb:
        return tb
    if re.search(r"(first|to) \d+ ?kwh", l):
        return "block1"
    if re.search(r"(above|greater than) \d+ ?kwh", l):
        return "block2"
    if re.search(r"\b(all|energy) consumption\b|renewable energy", l) or l.strip() in ("consumption", "energy consumption"):
        return "anytime"
    return ""


def brochure_unit(doc, page, code, label, token, units):
    """(unit as printed, charge type) of one GST-exclusive price, or None (reported)."""
    neg, dollar, number_, cent, suffix = token
    words = " ".join(units)
    l = label.lower()
    per = suffix or ""
    for u in ("kWh", "kVA", "KVA", "kW", "W"):
        if re.search(rf"\bper {u}\b", words) and not per:
            per = "/" + u
    money = "$" if dollar else "c"
    if per == "/W":
        return None
    if per == "/kWh":
        return f"{money}/kWh", "export" if "renewable energy" in l or "feed-in" in l or "export" in l else "energy"
    per = per.replace("KVA", "kVA")
    if per in ("/kVA", "/kW"):
        if "per day" not in l and "day" not in words:
            return None
        return f"{money}{per}/day", "capacity" if "capacity" in l else "demand"
    if per == "/day" or "per day" in l or "network access" in l:
        if "access" in l or per == "/day" or "supply" in l:
            return f"{money}/day", "fixed"
    return None


def evo_brochure(doc, two_columns=True, start=None):
    """start: read only from the first page whose text matches it."""
    rows = []
    with pdfplumber.open(os.path.join(common.ROOT, doc["local_path"])) as pdf:
        state = {"code": None, "name": "", "skip": True}
        texts = page_texts(os.path.join(common.ROOT, doc["local_path"]))
        first = next((i for i, t in enumerate(texts) if start.search(t)), len(texts)) + 1 if start else 1
        for pno, page in enumerate(pdf.pages, 1):
            if pno < first:
                continue
            # the right column starts at mid-page; a code printed a fraction left of it (2012-13 p5 '1008') belongs there
            mid = min([page.width / 2] + [w["x0"] for w in page.extract_words()
                                          if page.width / 2 - 3 <= w["x0"] < page.width / 2
                                          and re.fullmatch(r"\d{3,4}", w["text"])])
            spans = [(0, mid), (mid, page.width)] if two_columns else [(0, page.width)]
            for x0, x1 in spans:
                items, item, prev_text, just_header, heading = [], None, "", False, ""
                lines = brochure_lines(page, x0, x1)
                code_x = min((ws[0]["x0"] for _, ws in lines if re.fullmatch(r"\d{3,4}", ws[0]["text"])
                              and (len(ws) == 1 or ws[1]["text"][:1].isupper())), default=0)
                for top, ws in lines:
                    at_code = abs(ws[0]["x0"] - code_x) < 5
                    texts = [w["text"] for w in ws]
                    pi = next((i for i, t in enumerate(texts) if PRICE_TOKEN.match(t)), None)
                    label_words = texts if pi is None else texts[:pi]
                    after = [] if pi is None else texts[pi:]
                    # unit words of the price line above, printed under it ('per kWh per kWh')
                    if pi is None and item and item["prices"] and not texts[0] in BULLETS \
                            and top - item["prices"][-1]["top"] < 14:
                        px, ix = item["prices"][-1]["x0"], item["prices"][-1]["ix"]
                        tail = [w for w in ws if w["x0"] >= px - 15 and w["text"] in UNIT_WORDS]
                        item["prices"][-1]["units"] += [w["text"] for w in tail if w["x0"] < ix - 15]
                        label_words = [w["text"] for w in ws if w not in tail]
                    text = " ".join(label_words).strip()
                    bullet = next((b for b in BULLETS if text.startswith(b)), None)
                    m = HEADER.match(text) if at_code else None
                    if SECTION_STOP.match(text):
                        item = None
                        state.update(code=None, skip=True)
                    elif just_header and not after and (text == "(obsolete)" or (
                            not bullet and text[:1].isalnum() and not text.isdigit() and not re.match(r"[Tt]he ", text)
                            and len(text) < 40)):
                        state["name"] += " " + text  # the rest of a tariff name
                    elif at_code and re.fullmatch(r"\d{3,4}", text) and not after:  # code printed under its name
                        item = None
                        code = text
                        state.update(code=code, name=prev_text, skip=500 <= int(code) < 600)
                        just_header = True
                    elif m and m.group(2)[:1].isupper() and not after:
                        item = None
                        code = m.group(1)
                        state.update(code=code, name=m.group(2), skip=500 <= int(code) < 600)
                        just_header = True
                        text = ""
                    elif m and int(m.group(1)) < 500 and not m.group(2)[:1].isupper():  # small unmetered loads
                        name = heading if "unmetered" in heading.lower() else m.group(2)
                        state.update(code=m.group(1), name=name, skip=False)
                        item = {"label": [m.group(2)], "prices": [], "code": m.group(1), "name": name, "top": top}
                        items.append(item)
                        just_header = False
                    elif bullet and item and not item["prices"] and text[len(bullet):].strip().startswith("("):
                        # a bullet glyph printed a line low, beside the item's '(as defined)' continuation
                        item["label"].append(text[len(bullet):].strip())
                        item["top"] = top
                    elif bullet:
                        item = {"label": [text[len(bullet):].strip()], "prices": [], "code": state["code"],
                                "name": state["name"], "top": top, "tx": ws[1]["x0"] if len(ws) > 1 else 0,
                                "bx": ws[0]["x0"]}
                        items.append(item)
                        just_header = False
                    elif text and item is not None and (abs(ws[0]["x0"] - item.get("tx", -99)) < 3
                                                        and top - item["top"] <= 14
                                                        or abs(ws[0]["x0"] - item.get("bx", -99)) < 3
                                                        and top - item["top"] <= 11):
                        # a wrapped label line, even when it starts upper case (2017-18 p4 'NMI per day')
                        item["label"].append(text)
                        item["top"] = top
                    elif text[:1].isupper() and not text.startswith("("):
                        just_header = False
                        if not after:
                            heading = text
                        item = None
                    elif re.search(r"\b(shall|will) be:$", text) and not after:
                        just_header = False
                        item = None
                    elif text:
                        just_header = False
                        if item is not None and top - item["top"] > 20:  # a page footer, not a continuation
                            item = None
                            continue
                        if item is None:
                            item = {"label": [], "prices": [], "code": state["code"], "name": state["name"],
                                    "tx": ws[0]["x0"]}
                            items.append(item)
                        item["label"].append(text)
                        item["top"] = top
                    if after and not state["skip"] and state["code"]:
                        if item is None:
                            item = {"label": [], "prices": [], "code": state["code"], "name": state["name"]}
                            items.append(item)
                        item["top"] = top
                        pw = next(w for w in ws if PRICE_TOKEN.match(w["text"]))
                        rest = after[1:]
                        units = []
                        for t in rest:
                            if PRICE_TOKEN.match(t):
                                break
                            units.append(t)
                        if not item["prices"]:
                            item["prices"].append({"token": PRICE_TOKEN.match(after[0]).groups(), "units": units,
                                                   "x0": pw["x0"], "raw": after[0], "top": top,
                                                   "ix": next((w["x0"] for w in ws if w["x0"] > pw["x0"]
                                                               and PRICE_TOKEN.match(w["text"])), 10 ** 6),
                                                   "incl": next((w["text"] for w in ws if w["x0"] > pw["x0"]
                                                                 and PRICE_TOKEN.match(w["text"])), None)})
                    if item is not None and state["skip"]:
                        item["skip"] = True
                    prev_text = text if text and not bullet else prev_text
                for it in items:
                    if it.get("skip") or not it["prices"] or not it["code"]:
                        continue
                    label = re.sub(r"(\w)- (\w)", r"\1-\2", flat(" ".join(it["label"])))
                    # 2017-18/2018-19 print the first letter of some labels as a separate glyph ("f or", "a ll")
                    label = re.sub(r"^(?:f (?=or\b)|e (?=nergy\b)|a (?=ll\b))", lambda m: m.group(0)[0], label)
                    p = it["prices"][0]
                    got = brochure_unit(doc, pno, it["code"], label, p["token"], p["units"])
                    if got is None:
                        report(doc, locators.pdf(pno), f"{it['code']} '{label}' {p['raw']}: unit not stated per kWh, "
                                                       f"per day or per kVA/kW per day, left out")
                        continue
                    unit, charge = got
                    neg, _, value, _, _ = p["token"]
                    if p["incl"]:  # the GST-inclusive price beside it: 1.1 times this one
                        n2 = PRICE_TOKEN.match(p["incl"]).groups()
                        excl, incl = float(neg + value.replace(",", "")), float(n2[0] + n2[2].replace(",", ""))
                        if excl and abs(incl / excl - 1.1) > 0.002:
                            report(doc, locators.pdf(pno), f"{it['code']} '{label}' {p['raw']}: the price beside it "
                                                           f"({p['incl']}) is not 1.1 times it, kept as printed")
                    name = re.sub(r"\*+(?=\s|$)", "", flat(it["name"])).strip()  # "*" marks a footnote ("XMC*")
                    note = "closed to new customers" if "obsolete" in name.lower() else ""
                    rows.append(common.row(doc, it["code"], label, neg + value.replace(",", ""), unit,
                                           locators.pdf(pno), name=name, note=note, charge_type=charge,
                                           time_band=brochure_band(label) if charge in ("energy", "export")
                                           or re.search(r"\bat \w+ times", label) else ""))
    return rows


# ---------------------------------------------------------------------------------------------------------------
# Evoenergy 2019-20 to 2022-23: "Network Use of System charges (excluding GST)" tables. One row per tariff; each
# price sits under a first header row (Metering / Fixed charge / Energy consumption / Peak maximum demand / Capacity
# ...), a second one (Capital, Less than threshold, Max, Winter ...) and a 'Unit' row, read through the cell boxes
# because a header or unit cell spans several price columns. An XMC code printed under its base code has no prices of
# its own. The feed-in combination codes (201-1006) follow at the end of the schedule in the brochure layout.
TABLE_TITLE = re.compile(r"Network Use of System charges \(excluding GST\):\s*([^\n]+)")
TABLE_CODE = re.compile(r"^(\d{3})(\*{0,3})$")
TABLE_VALUE = re.compile(r"^-?\d[\d,]*\.\d+$")
TABLE_BAND = {"less than threshold": "block1", "greater than threshold": "block2", "max": "peak", "business": "peak",
              "mid": "shoulder", "evening": "shoulder", "economy": "offpeak", "off-peak": "offpeak",
              "solar sponge": "solar_soak"}
SEASON_WORDS = ("winter", "spring", "summer", "autumn")
SCHEMA_SEASONS = {w: w for w in SEASON_WORDS}  # spec.SEASONS has all four


def table_tariffs(page):
    """[(code, xmc codes, name, [(value, unit, head, sub)])] of the price tables on one page."""
    out = []
    for table in page.find_tables():
        cells = [r.cells for r in table.rows]
        tx = [[flat(re.sub(r"(\w)-\s+(\w)", r"\1-\2", c or "")) for c in r] for r in table.extract()]
        ui = next((i for i, r in enumerate(tx) if r and r[0] == "Unit"), None)
        cp = next((i for i, r in enumerate(tx) if r and r[0] == "Charging parameter"), None)
        if ui is None or cp is None:
            continue

        def at(i, x):
            for j, c in enumerate(cells[i]):
                if c is not None and tx[i][j] and c[0] - 0.5 <= x <= c[2] + 0.5:
                    return tx[i][j]
            return ""
        cur = None
        for i in range(ui + 1, len(tx)):
            r = tx[i]
            ci = next((j for j, t in enumerate(r) if TABLE_CODE.match(t)), None)
            vals = [j for j, t in enumerate(r) if j != ci and (TABLE_VALUE.match(t) or re.fullmatch(r"\*+", t))]
            words = [t for j, t in enumerate(r) if t and j != ci and j not in vals]
            if ci is None:
                if words and words[0].startswith("Tariffs for"):
                    cur = None
                elif cur and words and not vals:  # the rest of a tariff name ('Night', 'Customer LV')
                    cur[2].append(" ".join(words))
                continue
            code = TABLE_CODE.match(r[ci]).group(1)
            if not vals:
                if cur:
                    cur[1].append(code)  # the XMC version, printed under its base code
                continue
            prices = []
            for j in vals:
                x = (cells[i][j][0] + cells[i][j][2]) / 2
                head = at(0, x)  # a header cell spanning both header rows is read once
                subs = [t for t in dict.fromkeys(at(h, x) for h in range(1, cp)) if t != head]
                prices.append((r[j], at(ui, x), head, flat(" ".join(subs))))
            cur = (code, [], words, prices)
            out.append(cur)
    return [(c, x, flat(" ".join(n)).rstrip("*").strip(), p) for c, x, n, p in out]  # '*' marks a footnote


def evo_table(doc):
    rows, metering = [], []
    path = os.path.join(common.ROOT, doc["local_path"])
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            m = TABLE_TITLE.search(page_texts(path)[pno - 1])
            if not m:
                continue
            title = flat(m.group(0))
            cls = flat(m.group(1))
            loc = locators.pdf(pno)
            for code, xmc, name, prices in table_tariffs(page):
                for c in [code] + xmc:
                    is_xmc = c != code
                    note = title + (f"; XMC version of {code} (prices printed on the {code} row; XMC tariffs "
                                    f"exclude metering capital charges)" if is_xmc else "")
                    cname = name + (" XMC" if is_xmc else "")
                    for value, unit, head, sub in prices:
                        h, lsub = head.lower(), sub.lower()
                        what = f"{c} {head} {sub} {value}"
                        if h.startswith("metering"):
                            if is_xmc and "non-capital" not in lsub:
                                continue
                            metering.append({
                                "distributor": common.NAMES[doc["distributor_id"]], "fin_year": doc["pricing_year"],
                                "tariff_code": c, "meter_class": cname,
                                "component": "Metering " + (sub or "capital").lower() + " charge",
                                "unit": table_unit(unit), "value": value.replace(",", ""), "gst": "excl",
                                "source_file": doc["local_path"], "locator": loc, "note": note})
                            continue
                        if not TABLE_VALUE.match(value):
                            report(doc, loc, f"{what}: no number printed (footnote: the rate depends on the "
                                             f"designated connection point)")
                            continue
                        if not unit:
                            report(doc, loc, f"{what}: no unit printed for the column, left out")
                            continue
                        season = next((w for w in SEASON_WORDS if w in lsub), "")
                        if season and season not in SCHEMA_SEASONS:
                            report(doc, loc, f"{what}: season '{sub}' has no value in the schema season list, left out")
                            continue
                        band = ""
                        if h.startswith("fixed"):
                            charge, component = "fixed", head
                        elif h.startswith("energy export") or "export" in h:
                            charge, component = "export", flat(f"{head} {sub}")
                            band = "critical_peak" if "critical" in h else ""
                        elif h.startswith("energy consumption") or h.startswith("net energy"):
                            charge, component = "energy", flat(f"{head} {sub}")
                            band = TABLE_BAND.get(lsub, "anytime" if not sub else None)
                            if band is None:
                                report(doc, loc, f"{what}: energy column '{sub}' not understood, left out")
                                continue
                        elif "demand" in h:
                            charge, component = "demand", flat(f"{head} {sub}")
                            # page 'Charges': the kW demand tariffs measure demand in their peak / business times
                            band = "peak" if "/kW/" in table_unit(unit) else ""
                        elif h.startswith("capacity"):
                            charge, component = "capacity", head
                        else:
                            report(doc, loc, f"{what}: column '{head}' not understood, left out")
                            continue
                        rows.append(common.row(doc, c, component, value.replace(",", ""), table_unit(unit), loc,
                                               name=cname, customer_class=cls, note=note, charge_type=charge,
                                               time_band=band, season=SCHEMA_SEASONS.get(season, "")))
    rows += evo_brochure(doc, two_columns=False, start=re.compile(r"payments \(negative charges\)"))
    return rows, metering


def table_unit(text):
    """The column unit as printed, in the schema's case (2022-23 prints 'c/kwh', 'c/kw/day')."""
    return re.sub(r"kwh|kvah|kw(?=/)|kva(?=/)", lambda m: {"kwh": "kWh", "kvah": "kVAh", "kw": "kW",
                                                          "kva": "kVA"}[m.group(0).lower()], text, flags=re.I)


def wide_obsolete(doc):
    """A separate "Obsolete network charges" list (2007-08, 2008-09) in the wide layout: "No new customers can be
    connected to these tariffs" (p1)."""
    rows = wide(doc)
    for r in rows:
        r["note"] = "; ".join(x for x in ("closed to new customers (obsolete network charges list)", r["note"]) if x)
    return rows


# ---------------------------------------------------------------------------------------------------------------
DOCS = [
    # Essential Energy: Country Energy booklets
    (A + "essential/2001-02/network_price_list_2001.pdf", booklet),
    (A + "essential/2002-03/network_price_list_2002.pdf", booklet),
    (A + "essential/2003-04/network_price_list_2003.pdf", booklet),
    (A + "essential/2004-05/CE_Network_Pricing_Eff_1_July_04.pdf", booklet),
    (A + "essential/2005-06/CE_Network_Pricing_Eff_1_July_05.pdf", booklet),
    (A + "essential/2006-07/ce_network_pricing_effective_1_july_2006.pdf", booklet),
    # Country Energy / Essential Energy price sheets
    (A + "essential/2007-08/CE_Network_Pricing_Eff_1_July_07.pdf", wide),
    (A + "essential/2007-08/CE_Network_Pricing_Obsolete_Tariffs_Eff_1_July_07.pdf", wide_obsolete),
    (A + "essential/2008-09/CE_Network_Pricing_Eff_1_July_08.pdf", wide),
    (A + "essential/2008-09/CE_Network_Pricing_Obsolete_Tariffs_Eff_1_July_08.pdf", wide_obsolete),
    (A + "essential/2009-10/Network_Price_List_2009_12.pdf", wide),
    (A + "essential/2010-11/NetworkPriceList_ExplanatoryNotes1011.pdf", wide),
    (A + "essential/2011-12/NetworkPriceList_ExplanatoryNotes1112.pdf", wide),
    (A + "essential/2012-13/NetworkPriceList_ExplanatoryNotes1213.pdf", wide),
    (A + "essential/2013-14/PriceList_ExplanatoryNotes_1314_V1.pdf", wide),
    (A + "essential/2014-15/NetworkPriceListExplanatoryNotes1415.pdf", wide),
    (A + "essential/2015-16/Price_List_and_Explanatory_Notes_2015-16_2.pdf", wide),
    (A + "essential/2015-16/Price_List_and_Explanatory_Notes_2015-16.pdf", wide),
    (A + "essential/2016-17/PriceListAndExplanatoryNotes2016-17.pdf", wide),
    (A + "essential/2017-18/PriceListAndExplanatoryNotes2017-18.pdf", wide),
    (A + "essential/2018-19/PriceListAndExplanatoryNotes2018-19.pdf", wide),
    (A + "essential/2019-20/EssentialEnergyPriceListAndExplanatoryNotes2019-20.pdf", wide),
    (A + "essential/2020-21/NetworkPriceListandExplanatoryNotes202021.pdf", wide),
    (A + "essential/2021-22/NetworkPriceListandExplanatoryNotes202122.pdf", wide),
    (A + "essential/2022-23/NetworkPriceListandExplanatoryNotes202223.pdf", wide),
    # ActewAGL / Evoenergy schedules
    (A + "evoenergy/2006-07/NetworkChargesPrintableform2006.pdf", evo_brochure),
    (A + "evoenergy/2007-08/NetworkChargesPrintableform2007.pdf", evo_brochure),
    (A + "evoenergy/2008-09/NetworkChargesPrintableform2008.pdf", evo_brochure),
    (A + "evoenergy/2010-11/Electricity_Networks_charges2010.pdf", functools.partial(evo_brochure, two_columns=False)),
    (A + "evoenergy/2011-12/Electricity-networks-2011-12-Use-of-Network-charges.ashx.pdf",
     functools.partial(evo_brochure, two_columns=False)),
    (A + "evoenergy/2012-13/Electricity-network-prices-2012-13.ashx.pdf", evo_brochure),
    (A + "evoenergy/2013-14/Electricity-Network-prices-2013-14.ashx.pdf", evo_brochure),
    (A + "evoenergy/2014-15/Electricity-network-prices-2014-15.ashx.pdf", evo_brochure),
    (A + "evoenergy/2015-16/Electricity-network-prices-2015-16.ashx.pdf", evo_brochure),
    (A + "evoenergy/2016-17/Electricity-network-prices-2016-17.ashx.pdf", evo_brochure),
    (A + "evoenergy/2017-18/Evoenergy-electricity-schedule-of-charges-2017-18.pdf", evo_brochure),
    (A + "evoenergy/2018-19/evoenergy-electricity-schedule-of-charges-2018-19.pdf", evo_brochure),
    (A + "evoenergy/2019-20/evoenergy-electricity-schedule-of-charges-2019-20.pdf", evo_table),
    (A + "evoenergy/2020-21/evoenergy-electricity-schedule-of-charges-2020-21.pdf", evo_table),
    (A + "evoenergy/2021-22/evoenergy-electricity-schedule-of-charges-2021-22.pdf", evo_table),
    (A + "evoenergy/2022-23/evoenergy-electricity-schedule-of-charges-2022-23.pdf", evo_table),
]


def main(argv):
    """With arguments (substrings of document paths): parse only those documents and print the rows, writing nothing."""
    rows, metering = [], []
    for path, layout in DOCS:
        if argv and not any(a in path for a in argv):
            continue
        doc = common.document(path)
        got = layout(doc)
        if isinstance(got, tuple):
            got, meter = got
            metering += meter
        lname = getattr(layout, "__name__", None) or layout.func.__name__
        print(f"{doc['pricing_year']} {lname:12} {len(got):5} rows {len({r['tariff_code'] for r in got}):4} "
              f"codes  {path}")
        rows += got
    if argv:
        for r in rows + metering:
            print("  ", r.get("fin_year"), r["tariff_code"], "|", r.get("tariff_name", r.get("meter_class")), "|",
                  r["component"], r.get("time_band", ""), r.get("season", ""), r.get("charge_type", ""), "|", r["value"],
                  r["unit"], "|", r["locator"], "|", r["note"])
    else:
        common.write(SLUG, rows)
        schema.write_metering(f"history_{SLUG}", metering)
    for line in REPORT:
        print("left out:", line)


if __name__ == "__main__":
    main(sys.argv[1:])
