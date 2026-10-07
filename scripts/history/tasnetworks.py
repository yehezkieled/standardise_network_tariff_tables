#!/usr/bin/env python
"""TasNetworks (Aurora Energy before 1 July 2014) network tariffs, pricing years 2007 to 2022-23
-> out/history/tasnetworks.csv (+ out/dnsp_metering/history_tasnetworks.csv).

Contract: scripts/history/CONTRACT.md. One document per pricing year (FILES); three layouts:

  blocks    2007 to 2021-22: one block per tariff code ('N01 2008 Tariff', 'Aurora Code - N01',
            'TasNetworks code - TAS31 2017-18 tariff'), then sections 'DUoS Charge', 'TUoS Charge', 'Meter Charge',
            'Connection', 'Total Charge (NUoS)' / 'NUoS Charges', each a list of '<label> <value> <unit>' or
            '<label> (<unit>) <value>' lines; proposals print last period's price and the change after it.
  columns   2013-14 approved pricing proposal: Tables 7 and 8 (DUoS) and 9 (TUoS), one row per code, prices in
            columns under 'Daily charge', 'ToU energy rate Peak / Shoulder / Off-peak', 'Step energy rates Step 1 /
            Remaining', 'Demand rates', 'Capacity charges Specified / Excess' (and 'Connection charge').
  dtn       2022-23 price guide: 'Table N: Tariff prices for <name> (<code>) for 2022-23', columns
            'Unit | DUoS charge | TUoS charge | NUoS charge'.

A block or table that prints the total (NUoS) gives NUoS rows only; one that prints only parts (2012-13 to
2014-15, the >2 MVA specified demand tariffs N15/TAS15 and the 2007 specifically calculated customers) gives
DUoS / TUoS rows (connection charges as DUoS, noted), which the build does not store. A per-tariff 'Meter Charge'
(2007 to 2011-12; part of the printed total) goes to the metering side output.

Run from the repo root:  .venv/bin/python scripts/history/tasnetworks.py
"""
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import schema  # noqa: E402

sys.path.insert(0, os.path.join(common.ROOT, "scripts", "tariffdb"))
import locators  # noqa: E402
import pdfplumber  # noqa: E402

SLUG = "tasnetworks"
A = "sources/archive/tasnetworks"

# pricing year -> (document, layout)
FILES = {
    "2007": (f"{A}/2007/network_tariffs_2007.pdf", "blocks"),
    "2008-H1": (f"{A}/2008-H1/NW-271201-v5-Submission_to_the_Regulator_-_Network_Tariff_Pricing_Proposal_2008_as_at_"
                "2_Jan_2008.pdf", "blocks"),
    "2008-09": (f"{A}/2008-09/Aurora_Network_Pricing_Proposal_2008-09.pdf", "blocks"),
    "2009-10": (f"{A}/2009-10/Network_Pricing_Proposal_-_Version1.5.pdf", "blocks"),
    "2010-11": (f"{A}/2010-11/published_network_tariffs.pdf", "blocks"),
    "2011-12": (f"{A}/2011-12/P5_published_network_tariffs.pdf", "blocks"),
    "2012-13": (f"{A}/2012-13/IPP005-NetworkTariffPriceGuide.pdf", "blocks"),
    "2013-14": (f"{A}/2013-14/Annual-Pricing_Proposal-2013-14-Approved.pdf", "columns"),
    "2014-15": (f"{A}/2014-15/Network-Tariff-Application-and-Price-Guide.pdf", "blocks"),
    "2015-16": (f"{A}/2015-16/Network-Tariff-Application-and-Price-Guide-2015-16.pdf", "blocks"),
    "2016-17": (f"{A}/2016-17/PP002-Network-Tariff-Application-and-Price-Guide-_Approved__2.pdf", "blocks"),
    "2017-18": (f"{A}/2017-18/2017-18-Network-Tariff-Application-and-Price-Guide-APPROVED.pdf", "blocks"),
    "2018-19": (f"{A}/2018-19/TN-Network-Tariff-Application-and-Price-Guide-2018-19-APPROVED.pdf", "blocks"),
    "2019-20": (f"{A}/2019-20/2019-20-network-tariff-application-and-price-guide_2.pdf", "blocks"),
    "2020-21": (f"{A}/2020-21/2020-21-network-tariff-application-and-price-guide_2.pdf", "blocks"),
    "2021-22": (f"{A}/2021-22/2021-22-network-tariff-application-and-price-guide.pdf", "blocks"),
    "2022-23": (f"{A}/2022-23/2022-23-network-tariff-application-and-price-guide.pdf", "dtn"),
}

NUM = r"-?\$?\d[\d,]*\.\d+|-?\$?\d[\d,]*"
UNIT = r"c/lamp(?: watt/day)?|(?:c|\$)?/[A-Za-z][\w/]*"


def num(text):
    """The number as printed, without '$' and thousands separators."""
    return text.replace("$", "").replace(",", "")


class Line(str):
    """A text line of a page (pdfplumber extract_text, the stream locators.verify re-reads) that knows the x position
    of each printed character: Line.x(i) is the left edge of the character at index i."""

    def __new__(cls, text, xs=(), top=None):
        obj = super().__new__(cls, text)
        obj.xs, obj.top = list(xs), top
        return obj

    def tokens(self):
        """[(token, x centre)] of the space-separated tokens."""
        return [(m.group(), (self.xs[m.start()] + self.xs[m.end() - 1]) / 2) for m in re.finditer(r"\S+", self)]

    def x(self, i):
        return self.xs[i] if i < len(self.xs) else None


def pages(path):
    """1-based page number -> [Line]."""
    out = {}
    with pdfplumber.open(os.path.join(common.ROOT, path)) as pdf:
        for n, page in enumerate(pdf.pages, 1):
            lines = []
            for ln in page.extract_text_lines(return_chars=True):
                chars = iter(c for c in ln["chars"] if c["text"].strip())
                xs = [None if ch == " " else next(chars)["x0"] for ch in ln["text"]]
                text = ln["text"].strip()
                lead = len(ln["text"]) - len(ln["text"].lstrip())
                lines.append(Line(text, xs[lead:lead + len(text)], ln["top"]))
            out[n] = lines
    return out


# ---------------------------------------------------------------------------------------------- shared row helpers
def band(component, unit, code_bands):
    """(charge_type, time_band) for a label. Step energy and demand ladders ('First 500kWh per Quarter', 'Next
    1000kWh', 'Remaining Consumption'; 'First 250kW Demand', 'Additional Demand') are numbered blocks in printed order;
    'Day'/'Night' energy is the peak/off-peak pair of the irrigation tariff ('Daily charge with peak and off-peak
    energy charge', 2008-09 proposal Table 1 p9); a lamp-watt price is the street lighting fixed charge."""
    low = component.lower()
    if "lamp" in unit:
        return "fixed", "anytime"
    ct = schema.charge_type_from_label(component, unit)
    if re.match(r"(first|next|remaining|additional)\b", low):
        code_bands[ct] = code_bands.get(ct, 0) + 1
        return ct, f"block{code_bands[ct]}"
    if low.startswith("day "):
        return ct, "peak"
    if low.startswith("night "):
        return ct, "offpeak"
    return ct, None


# ---------------------------------------------------------------------------------------------- layout: blocks
HEADER_RES = (
    re.compile(r"^(?:Aurora|TasNetworks) [Cc]ode\s*[-–]\s*(?P<code>[A-Z]{1,4}\w*(?:\s*/\s*N\w+)?)\b"),
    re.compile(r"^(?P<code>N\d\d[a-z]?) (?:2007|2008) Tariff\b"),
    re.compile(r"^(?P<code>N\d\d[a-z]?) Tariff Charge$"),
    # proposals: 'N01 Tariff Charge Period 2 Period 1 %Change', new tariffs 'N13r Period 3' (also the caption of the
    # forecast table that follows, which has no DUoS/TUoS/NUoS sections and so gives no rows)
    re.compile(r"^(?P<code>N\d\d[a-z]?) (?:Network )?(?:Tariff (?:Charge|Rates) )?Period \d(?: Period \d %\s?Change)?$"),
)
# the 2007 list's specifically calculated customers: no code, a 'Preferred Tariff / Fall-back Tariff' pair
SCC_RE = re.compile(r"^(?:2007 )?Preferred Tariff Fall-back Tariff$")
SECTIONS = (
    (re.compile(r"^DUoS(?: Charge)?(?: Jul – Nov)?(?: Dec – Jun)?$"), "DUoS"),
    (re.compile(r"^TUoS Charge$|^Transmission Charge\b"), "TUoS"),
    (re.compile(r"^Meter Charge\b"), "Meter"),
    (re.compile(r"^Connection(?: Charge)?$"), "Connection"),
    (re.compile(r"^Total Charge(?: \(NUoS\))?$|^NUoS Charges?$"), "NUoS"),
)
END_RE = re.compile(r"^(Table \d|Page \d|Page [ivx]+\b|.*\bPage \d+( of \d+)?$|Aurora Energy Pty Ltd|© |Note:|Forecast|"
                    r"\d+(\.\d+)*\.? +[A-Z][a-z]|UNCONTROLLED)")
# '<label> [(<unit>)[footnote]] <value> [<unit>] [<previous value> [<unit>] [<change %>]]'
PRICE_RE = re.compile(
    rf"^(?P<label>[A-Za-z][^()]*?)(?:\s*\((?P<u1>[^)]*/[^)]*)\)(?P<fn>\d{{1,2}})?)?\s+(?P<val>{NUM})"
    rf"(?:\s*(?P<u2>{UNIT}))?(?P<rest>(?:\s+(?:{NUM})(?:\s*(?:{UNIT}))?|\s+-?[\d.]+%)*)\s*$")
VAL_RE = re.compile(rf"(?P<val>{NUM})(?![\d.,%])(?:\s*(?P<unit>{UNIT}))?")
LAMP_RE = re.compile(rf"^(?P<val>{NUM}) (?P<unit>c/lamp)$")  # 2010-11: value, label and 'watt/day' on three lines
NAME_RE = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+)(?P<name>[A-Z].+?)$")
NAME_STOP = re.compile(r"^(Rates|Terms and Conditions|Network tariff prices|Variations|Future Variations|"
                       r"General conditions|Tariff Rates|.*\.{4,}.*)", re.I)
TABLE_NAME_RE = re.compile(r"^Table \d+: Tariff prices for (?P<name>.+?)(?: \([A-Z0-9]+\))?(?: for \d{4}-\d\d)?$")


def split_codes(code):
    """'N13b/N13c', 'N08a /N08b': one table priced for both codes."""
    return [c.strip() for c in code.split("/") if c.strip()]


def blocks(path):
    """[(page, code, name, scc, {section: [(label, value, unit, unit_note)]})] in printed order."""
    out, name, by_code, head_code, head_depth = [], "", {}, None, 0  # by_code: proposals' headings name the code ('3.9 HV Demand kW (N11) – Obsolete')
    for page, lines in pages(path).items():
        cur, section = None, None
        i = 0
        while i < len(lines):
            ln = lines[i]
            i += 1
            m = next((r.match(ln) for r in HEADER_RES if r.match(ln)), None)
            if m or SCC_RE.match(ln):
                code = m.group("code") if m else name
                # proposals print this period's price, then the previous period's: the x of each column heading
                heads = [ln.x(h.start()) for h in re.finditer(r"\b(?:2008|2007|Period) ", ln)] if m else []
                cur = {"page": page, "code": code, "name": by_code.get(code, name), "scc": not m,
                       "sections": defaultdict(list), "cols": heads if len(heads) == 2 else None,
                       "head_code": head_code}
                out.append(cur)
                section = None
                continue
            t = TABLE_NAME_RE.match(ln)
            h = NAME_RE.match(ln)
            if t:
                name = t.group("name")
            elif h and not NAME_STOP.match(h.group("name")):
                c = re.search(r"\s*\((?P<c>(?:N|TAS)\d\w*)\)", h.group("name"))
                name = (h.group("name")[:c.start()] + h.group("name")[c.end():]).strip() if c else h.group("name")
                depth = len(re.match(r"^[\d.]+", ln).group().strip(".").split("."))
                if c:
                    head_code, head_depth = c.group("c"), depth
                elif head_code and depth <= head_depth:  # a sub-heading ('20.1 Time of use') keeps the code
                    head_code = None
                if c:
                    by_code[c.group("c")] = name
            if END_RE.match(ln):  # a heading, table caption, footnote or page footer closes the block
                cur, section = None, None
            if cur is None:
                continue
            s = next((sec for r, sec in SECTIONS if r.match(ln)), None)
            if s and "Dec – Jun" in ln:  # 2018-19: TAS87/TAS88 'Jul – Nov' and 'Dec – Jun' columns, TAS97/TAS98 'Dec – Jun'
                cur["months"] = ln[ln.index("Charge") + 7:]
            if s:
                section = s
                continue
            if section is None:
                continue
            lm = LAMP_RE.match(ln)
            if lm and i + 1 < len(lines) and lines[i + 1] == "watt/day":
                cur["sections"][section].append((lines[i], num(lm.group("val")), "c/lamp watt/day", ""))
                i += 2
                continue
            p = PRICE_RE.match(ln)
            if p:
                vals = list(VAL_RE.finditer(ln, p.start("val")))
                if cur["cols"]:  # keep the value under this period's heading; none there: only last period's printed
                    now, before = cur["cols"]
                    vals = [v for v in vals if abs(ln.x(v.start()) - now) < abs(ln.x(v.start()) - before)]
                if not vals:
                    continue
                v = vals[0]
                unit = (p.group("u1") or v.group("unit") or "").strip()
                if v.group("val").startswith("$") and unit.startswith("/"):
                    unit = "$" + unit  # 2007: '$90.686 /kW/annum'
                extra = ""
                if cur.get("months") == "Jul – Nov Dec – Jun":
                    extra = "'Jul – Nov' column"
                    if len(vals) > 1 and num(vals[1].group("val")) != num(v.group("val")):
                        extra += (f"; the 'Dec – Jun' column prints {num(vals[1].group('val'))} (off-peak demand "
                                  "incentive from 1 December 2018)")
                elif cur.get("months") == "Dec – Jun":
                    extra = "'Dec – Jun' column: the tariff is available from 1 December 2018"
                cur["sections"][section].append((p.group("label").strip(), num(v.group("val")), unit, extra))
    return out


def block_rows(year, path):
    doc = common.document(path)
    rows, metering, seen = [], [], {}
    found = blocks(path)
    doc_units = {}  # label -> unit printed in the document's other tariff tables
    for b in found:
        for sec in b["sections"].values():
            for lab, _, u, _ in sec:
                if u:
                    doc_units.setdefault(lab, u)
    for b in found:
        secs, page = b["sections"], b["page"]
        if not secs:
            continue
        loc = locators.pdf(page)
        if b["scc"]:
            codes, base_note = [b["code"]], "no code printed; site-specific (specifically calculated customer)"
        else:
            codes = split_codes(b["code"])
            base_note = f"printed jointly as '{b['code']}'" if len(codes) > 1 else ""
        printed = {lab: u for sec in secs.values() for lab, _, u, _ in sec if u}
        parts = ["NUoS"] if secs.get("NUoS") else [s for s in ("DUoS", "Connection", "TUoS") if secs.get(s)]
        for code in codes:
            key = (code, tuple((lab, v) for lab, v, *_ in secs.get("NUoS", [])))
            repeated = key in seen and secs.get("NUoS")
            seen.setdefault(key, page)
            for part in parts:
                code_bands = {}
                for label, value, unit, extra in secs[part]:
                    note = [base_note] if base_note else []
                    if extra:
                        note.append(extra)
                    if not unit:  # 2010-11 N06a 'All Energy 0.941': the unit of that label in the block's other rows
                        unit = printed.get(label, "")
                        if unit:
                            note.append(f"unit not printed on this line; '{unit}' as the same label above")
                        elif not any(u for sec in secs.values() for _, _, u, _ in sec) and doc_units.get(label):
                            # 2008-09 N02b prints no unit at all: the unit of that label in the other tariff tables
                            unit = doc_units[label]
                            note.append(f"unit not printed in this table; '{unit}' as the same label in the "
                                        f"document's other tariff tables")
                        else:
                            continue  # not a price: 'Excess Demand Charge charge in Table 13' (nodal TUoS)
                    if "energy" in label.lower() and unit == "c/day":  # 2011-12 N06a NUoS 'All Energy 1.102 c/day'
                        note.append("unit printed as 'c/day', read as 'c/kWh': an energy charge, the DUoS and TUoS "
                                    "rows of the same label are c/kWh")
                        unit = "c/kWh"
                    if b["head_code"] and b["head_code"] not in codes and not b["scc"]:
                        note.append(f"the section heading names it '{b['head_code']}'")
                    if re.match(r"^NO\d", code):
                        note.append("code printed with the letter O ('N02' in other years)")
                    if part == "Connection":
                        note.append("connection charge (part of the distribution charge)")
                    if part in ("DUoS", "TUoS", "Connection") and not b["scc"]:
                        note.append("no total (NUoS) printed")
                    if b["scc"] and part != "DUoS":
                        note.append("Fall-back Tariff column")
                    if repeated:
                        note.append(f"{schema.REPEATED_PRINTING} (same code and prices on p{seen[key]})")
                    note.append(f"p{page}")
                    ct, tb = band(label, unit, code_bands)
                    rows.append(common.row(doc, code, label, value, unit, loc, name=b["name"],
                                           basis="DUoS" if part == "Connection" else part, note="; ".join(note),
                                           charge_type=ct, time_band=tb))
            if parts == ["NUoS"] and not repeated:
                for label, value, unit, _ in secs.get("Meter", []):
                    if re.search(r"/k(W|VA)\b", unit):
                        continue  # 2007 N10/N11 meter demand charge ($/kW/annum): metering holds c/day and c/kWh only
                    note = "per-tariff meter charge, included in the printed Total Charge (NUoS)"
                    if not unit and doc_units.get(label):
                        unit = doc_units[label]
                        note += f"; unit not printed in this table; '{unit}' as the same label in the other tables"
                    metering.append({"distributor": common.NAMES[doc["distributor_id"]], "fin_year": year,
                                     "tariff_code": code, "meter_class": b["name"], "component": f"Meter Charge - {label}",
                                     "unit": unit, "value": value, "gst": "excl", "source_file": path, "locator": loc,
                                     "note": f"{note}; p{page}"})
    return rows, metering


# ---------------------------------------------------------------------------------------------- layout: dtn (2022-23)
DTN_TABLE_RE = re.compile(r"^Table \d+: Tariff prices for (?P<name>.+) \((?P<code>TAS\w+)\) for 2022-23$")
# '<label>[footnote] <unit> <DUoS> <TUoS> [<NUoS>]'; TAS15 prints 'As per nodal charge in section 18' for TUoS
DTN_ROW_RE = re.compile(rf"^(?P<label>[A-Za-z][A-Za-z \-–]*?[a-z])\d*\s+(?P<unit>c/\S+)\s+(?P<v>(?:{NUM}|-)(?:\s+.*)?)$")


def dtn_rows(year, path):
    doc = common.document(path)
    rows = []
    for page, lines in pages(path).items():
        i = 0
        while i < len(lines):
            m = DTN_TABLE_RE.match(lines[i])
            i += 1
            if not m:
                continue
            code, name = m.group("code"), m.group("name")
            head = " ".join(lines[i:i + 3])
            cols = ["DUoS", "TUoS", "NUoS"] if "NUoS" in head else ["DUoS", "TUoS"]
            code_bands = {}
            while i < len(lines) and not lines[i].startswith(("Network tariff application", "Table ", "Note:")):
                ln, i = lines[i], i + 1
                if ln == "c/lamp" and i + 1 < len(lines) and lines[i + 1] == "watt/day":
                    lab, *vals = lines[i].rsplit(" ", 3)
                    ln, i = f"{lab} c/lamp_watt/day {' '.join(vals)}", i + 2
                r = DTN_ROW_RE.match(ln)
                if not r:
                    continue
                unit = r.group("unit").replace("_", " ")
                vals = []
                for tok in r.group("v").split():
                    # prices print decimals; '5 times nodal charge' is text
                    if not re.fullmatch(r"\d[\d,]*\.\d+|-", tok) or len(vals) == len(cols):
                        break
                    vals.append(tok)
                parts = dict(zip(cols, vals))
                use = ["NUoS"] if "NUoS" in cols else [c for c in cols if parts.get(c, "-") != "-"]
                for part in use:
                    v = parts.get(part, "-")
                    if v == "-":
                        continue
                    label = r.group("label").strip()
                    ct, tb = band(label, unit, code_bands) if part == use[0] else band(label, unit, {})
                    note = [f"Table {m.group(0).split(":")[0][6:]} p{page}"]
                    if part != "NUoS":
                        note.append("no total (NUoS) printed")
                    if "connection" in label.lower():
                        note.append("connection charge (part of the distribution charge)")
                    rows.append(common.row(doc, code, label, num(v), unit, locators.pdf(page), name=name, basis=part,
                                           note="; ".join(note), charge_type=ct, time_band=tb))
    return rows


# ---------------------------------------------------------------------------------------------- layout: columns
# table -> (basis, [(header word, its occurrence on the header lines, label, unit, charge_type, time_band)])
CAPACITY = [("Specified", "Capacity charge Specified", "c/kVA/day", "demand", None),
            ("Excess", "Capacity charge Excess", "c/kVA/day", "demand", None)]
COLUMNS_TABLES = {
    "7": ("DUoS", [("c/day", "Daily charge", "c/day", "fixed", None),
                   ("Peak", "ToU energy rate Peak", "c/kWh", "energy", "peak"),
                   ("Shoulder", "ToU energy rate Shoulder", "c/kWh", "energy", "shoulder"),
                   ("Off-peak", "ToU energy rate Off-peak", "c/kWh", "energy", "offpeak"),
                   ("Step", "Step energy rate Step 1", "c/kWh", "energy", "block1"),
                   ("Remaining", "Step energy rate Remaining", "c/kWh", "energy", None),
                   ("c/kVA(kW)", "Demand rate", "c/kVA/day", "demand", "anytime")] + CAPACITY),
    "8": ("DUoS", [("c/day", "Daily charge", "c/day", "fixed", None),
                   ("Peak", "Energy rate Peak", "c/kWh", "energy", "peak"),
                   ("Shoulder", "Energy rate Shoulder", "c/kWh", "energy", "shoulder"),
                   ("Off-peak", "Energy rate Off-peak", "c/kWh", "energy", "offpeak"),
                   ("All", "Energy rate All energy", "c/kWh", "energy", "anytime"),
                   ("Specified", "Connection charge Specified", "c/kVA/day", "demand", None),
                   ("Excess", "Connection charge Excess", "c/kVA/day", "demand", None)] + CAPACITY),
}
COLUMNS_TABLES["9"] = ("TUoS", COLUMNS_TABLES["7"][1])
COLUMNS_TITLE_RE = re.compile(r"^Table (?P<t>[789]): Proposed tariffs for (?:DUoS|TUoS)\b")
COLUMNS_CODE_RE = re.compile(r"^(?:N\d\d[a-z]?|ITC)$")  # ITC: individual tariffs, confidential / locational


def columns_rows(year, path):
    """Tables 7 to 9 of the 2013-14 approved pricing proposal: each price sits under its column heading (matched by
    x position); a code's name and prices can print a line above or below the code, so lines go to the code nearest
    in height. 'Remaining' is the second step of the one stepped tariff (N02a; 'First 500 kWh per Quarter' in the
    2012-13 and 2014-15 guides) or the single energy rate of every other tariff; the demand column is c/kW for the
    kW demand tariff (N03) and c/kVA otherwise ('c/kVA(kW)/day'); street lighting (N20) prints its c/lamp watt/day
    price in the demand column."""
    doc = common.document(path)
    rows = []
    for page, lines in pages(path).items():
        start = next((i for i, ln in enumerate(lines) if COLUMNS_TITLE_RE.match(ln) and not re.search(r"\.{3} *\d+$", ln)), None)
        if start is None:  # (the list of tables names them with dot leaders)
            continue
        title = COLUMNS_TITLE_RE.match(lines[start])
        basis, spec = COLUMNS_TABLES[title.group("t")]
        end = next(i for i, ln in enumerate(lines) if i > start and COLUMNS_CODE_RE.match(ln.split()[0]))
        heads = [tok for ln in lines[start:end] for tok in ln.tokens()]
        cols, seen = [], defaultdict(int)
        for word, *rest in spec:
            xs = [x for t, x in heads if t == word]
            cols.append((xs[seen[word]], *rest))
            seen[word] += 1
        body = [ln for ln in lines[end:] if ln.top < max(l.top for l in lines if l.startswith(("Page", "©"))) - 1
                and not re.match(r"^\d+ \w", ln)]  # footnotes ('1 There are no charges for this network tariff.')
        codes = [ln for ln in body if COLUMNS_CODE_RE.match(ln.split()[0])]
        own = defaultdict(list)
        for ln in body:
            own[min(codes, key=lambda c: abs(c.top - ln.top)).split()[0]].append(ln)
        for code in (c.split()[0] for c in codes if c.split()[0] != "ITC"):
            name = " ".join(t for ln in own[code] for t, x in ln.tokens() if 125 < x < 285 and t != code)
            for ln in own[code]:
                for tok, x in ln.tokens():
                    if not re.fullmatch(r"\d[\d,]*\.\d+", tok):
                        continue
                    _, label, unit, ct, tb = min(cols, key=lambda c: abs(c[0] - x))
                    note = [f"Table {title.group('t')} p{page}", "no total (NUoS) printed"]
                    if label == "Demand rate":
                        if code == "N20":
                            label, unit, ct, tb = "Street lighting", "c/lamp watt/day", "fixed", "anytime"
                            note.append("printed in the demand rates column; c/lamp watt/day as in the 2012-13 and "
                                        "2014-15 guides")
                        elif re.search(r"\bkW Demand\b", name):
                            unit = "c/kW/day"
                            note.append("column printed as c/kVA(kW)/day; kW for this kW demand tariff")
                    if label.endswith("Remaining"):
                        tb = "block2" if any(c[1].endswith("Step 1") and abs(c[0] - x2) < 30 for c in cols
                                             for ln2 in own[code] for t2, x2 in ln2.tokens()
                                             if re.fullmatch(r"\d[\d,]*\.\d+", t2)) else "anytime"
                    if label.startswith("Connection"):
                        note.append("connection charge (part of the distribution charge)")
                    rows.append(common.row(doc, code, label, num(tok), unit, locators.pdf(page), name=name,
                                           basis=basis, note="; ".join(note), charge_type=ct, time_band=tb))
    return rows


def main():
    rows, metering = [], []
    for year, (path, layout) in FILES.items():
        if layout == "blocks":
            r, m = block_rows(year, path)
            metering += m
        elif layout == "dtn":
            r = dtn_rows(year, path)
        else:
            r = columns_rows(year, path)
        rows += r
        print(f"{year}: {len(r)} rows from {os.path.basename(path)}")
    common.write(SLUG, rows)
    schema.write_metering(f"history_{SLUG}", metering)
    print(f"out/dnsp_metering/history_{SLUG}.csv: {len(metering)} rows")


if __name__ == "__main__":
    main()
