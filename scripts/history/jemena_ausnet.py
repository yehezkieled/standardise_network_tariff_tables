#!/usr/bin/env python
"""Historical network tariffs of Jemena (predecessors Solaris Power, AGL Electricity, Alinta AE) and AusNet Services
(predecessors Eastern Energy, TXU Networks, SPI Electricity / SP AusNet) for the pricing years before 2023-24.

Run from the repository root:  .venv/bin/python scripts/history/jemena_ausnet.py
Writes out/history/jemena_ausnet.csv (scripts/history/CONTRACT.md).

One function per document layout; every value is read from the page's words (pdfplumber) and located by the printed
labels around it: tariff code or name lines, component labels, units and column headings.
"""
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tariffdb"))
import common  # noqa: E402
import locators  # noqa: E402

import pdfplumber  # noqa: E402

SLUG = "jemena_ausnet"
BEFORE_GST = "before GST"
NUM = r"-?\d[\d,]*(?:\.\d+)?"
NUM_RE = re.compile(rf"^{NUM}$")


# ------------------------------------------------------------------------------------------------ helpers
def lines_of(page, x_min=None, x_max=None, tol=2.5, words=None):
    """Words of a page grouped into lines by their top, left to right: [(top, [word])]."""
    ws = words if words is not None else page.extract_words(keep_blank_chars=False, x_tolerance=1.5)
    ws = [w for w in ws if (x_min is None or w["x0"] >= x_min) and (x_max is None or w["x0"] < x_max)]
    out = []
    for w in sorted(ws, key=lambda w: (round(w["top"]), w["x0"])):
        if out and abs(w["top"] - out[-1][0]) <= tol:
            out[-1][1].append(w)
        else:
            out.append((w["top"], [w]))
    return [(top, sorted(ws, key=lambda w: w["x0"])) for top, ws in out]


def text(ws):
    return " ".join(w["text"] for w in ws)


def clean_num(s):
    """The number as printed without '$', thousands separators or a glued unit; None when it is not a number."""
    s = s.strip().replace("$", "").replace(",", "").strip()
    return s if NUM_RE.match(s) else None


def check_value(path, locator, value):
    ok, why = locators.verify(path, locator, value)
    if not ok:
        raise SystemExit(f"{path} {locator}: {why}")


def _ocr_phrases(image, k):
    """rapidocr phrases of an image as [(x0, top, x1, bottom, text)] in PDF points (k: points per pixel); phrases
    without a word or a number (stray rule fragments read as '- -') left out."""
    result, _ = locators._ocr_engine()(image)
    out = []
    for box, phrase, _score in result or []:
        if not re.search(r"[A-Za-z]{2}|\d", phrase):
            continue
        out.append((min(p[0] for p in box) * k, min(p[1] for p in box) * k, max(p[0] for p in box) * k,
                    max(p[1] for p in box) * k, phrase))
    return out


def ocr_words(path, page_no):
    """Words of a page whose text layer cannot be read (fonts without a character map: every glyph comes out as
    '(cid:N)'), from rapidocr on the rendered page at the validator's 288 dpi. That reading drops an occasional label
    or tariff name (italic on grey); a 432 dpi reading (which misreads more letters, 'Encrgy') fills in only the
    places where the 288 dpi one found nothing. Every price kept is re-found by the validator in its own 288 dpi
    reading of the page (check_value). OCR returns phrases; each is split into words placed by their share of the
    phrase's characters."""
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_no - 1]
        base = _ocr_phrases(page.to_image(resolution=288).original, 72 / 288)
        fine = _ocr_phrases(page.to_image(resolution=432).original, 72 / 432)

    def overlaps(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    phrases = base + [f for f in fine if not any(overlaps(f, b) for b in base)]
    words = []
    for x0, top, x1, bottom, phrase in phrases:
        phrase = re.sub(r"^S(?= ?\d[\d,]*\.\d+$)", "$", phrase)  # '$ 5.64' read as 'S 5.64'
        per = (x1 - x0) / max(len(phrase), 1)
        for m in re.finditer(r"\S+", phrase):
            words.append({"text": re.sub(r"^Spm$", "8pm", m.group(0)),  # '4pm to Spm AEST': an 8 read as S
                          "x0": x0 + m.start() * per, "x1": x0 + m.end() * per, "top": top, "bottom": bottom})
    return words


class Doc:
    """One archived document: emits parser rows for it."""

    def __init__(self, path, note="", ocr=False):
        self.path = path
        self.ocr = ocr
        self.doc = common.document(path)
        self.rows = []
        self.note = note
        self.before_gst = self.doc["pricing_year"] in ("1996-97", "1997-98", "1998-99", "1999-00")

    def add(self, code, component, value, unit, page_no, *, name="", customer_class="", note="", basis="NUoS",
            time_band=None, season=None, charge_type=None):
        notes = [n for n in (note, self.note, BEFORE_GST if self.before_gst else "") if n]
        if time_band is None and re.match(r"^(unit (charge|rate)|energy|energy charge)$", component.strip(), re.I):
            time_band = "anytime"  # the single rate of a flat tariff
        self.rows.append(common.row(self.doc, code, component, value, unit, locators.pdf(page_no, self.ocr), name=name,
                                    customer_class=customer_class, basis=basis, gst="excl", note="; ".join(notes),
                                    time_band=time_band, season=season, charge_type=charge_type))


def block_band(label):
    """'Energy - First 1020/Quarter' / 'Energy - Balance' style block labels."""
    l = label.lower()
    if re.search(r"\bfirst\b", l):
        return "block1"
    if re.search(r"\bbalance\b|\bnext\b|\bremaining\b|\bthereafter\b", l):
        return "block2"
    return None


# ----------------------------------------------------------------- 1996-97, 1997-98: all five distributors
def vic_combined(path, column):
    """ORG 'Network tariffs for use of each distributor's distribution system' (nt9697, nt9798): one table, one
    column per distributor (Eastern = AusNet, Solaris = Jemena). Tariff names, no codes."""
    d = Doc(path, note="no code printed")
    with pdfplumber.open(path) as pdf:
        heads = None
        cls, name, pending = "", "", None
        for page_no, page in enumerate(pdf.pages, 1):
            for top, ws in lines_of(page):
                t = text(ws)
                if re.search(r"\bEastern\b.*\bSolaris\b|\bSolaris\b.*\bEastern\b", t):
                    heads = [(w["text"], w["x1"]) for w in ws if w["text"] not in ("units", "Units")]
                    continue
                if re.match(r"^(LOW|HIGH) VOLTAGE TARIFFS|^SUBTRANSMISSION TARIFFS", t):
                    cls = t.title() if t.isupper() else t
                    continue
                if heads is None or re.match(r"^(NETWORK|FOR THE|These prices|http|\*|#|Minimum|demand\b|\d+ ?kW)",
                                             t):
                    continue
                vals = []
                for w in reversed(ws):
                    if clean_num(w["text"]) is not None or w["text"] == "-":
                        vals.insert(0, w)
                    else:
                        break
                body = ws[:len(ws) - len(vals)]
                if body and not t.startswith("-"):
                    vals, body = [], ws
                if not body and vals and pending:  # wrapped values of the previous component line
                    pending[2].extend(vals)
                    continue
                if pending:
                    emit_combined(d, heads, column, *pending)
                    pending = None
                if t.startswith("-"):
                    label = text(body).lstrip("-").strip()
                    m = re.match(r"^(.*?)\s+(\$pa|c/kWh\]?|\$/kW pa|\$/kVA pa)$", label)
                    if not m:
                        raise SystemExit(f"{path} p{page_no}: unreadable component line {t!r}")
                    pending = [page_no, (cls, name, m.group(1), m.group(2).rstrip("]")), vals]
                    continue
                if vals:
                    raise SystemExit(f"{path} p{page_no}: numbers on a line that is not a component: {t!r}")
                if name.endswith("-"):
                    name = f"{name} {t}"
                else:
                    name = t
            if pending:
                emit_combined(d, heads, column, *pending)
                pending = None
    return d.rows


def emit_combined(d, heads, column, page_no, what, vals):
    cls, name, label, unit = what
    if len(vals) == len(heads):
        picked = vals[[h for h, _ in heads].index(column)]
    else:  # a wrapped line: by the right edge of the column heading
        x1 = dict(heads)[column]
        near = [v for v in vals if abs(v["x1"] - x1) < 6]
        if len(near) != 1:
            raise SystemExit(f"{d.path} p{page_no}: cannot place {[v['text'] for v in vals]} under {column}")
        picked = near[0]
    if picked["text"] == "-":
        return
    code = name.rstrip("#*").strip()
    d.add(code, label, clean_num(picked["text"]), unit, page_no, name=code, customer_class=cls)


# ------------------------------------------------------------------------------- 1998-99 Eastern Energy
def eastern_9899(path):
    """'Eastern Energy's 1998/99 network tariffs approved by the Office': category / tariff name / components /
    units / price columns. No codes."""
    d = Doc(path, note="no code printed")
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            ws_all = page.extract_words(x_tolerance=1.5)
            unit_x = min(w["x0"] for w in ws_all if w["text"] == "Units")
            price_x = min(w["x0"] for w in ws_all if w["text"] == "Price")
            comp_x = min(w["x0"] for w in ws_all if w["text"] == "Components")
            name, cont = "", False
            for top, ws in lines_of(page, x_min=comp_x - 10, words=ws_all):
                t = text(ws)
                if "Components" in t or "NETWORK TARIFFS" in t or "APPROVED" in t:
                    continue
                unit = [w for w in ws if unit_x - 5 <= w["x0"] < price_x - 5]
                price = [w for w in ws if w["x0"] >= price_x - 5]
                label = [w for w in ws if w["x0"] < unit_x - 5]
                if unit and price:
                    value = clean_num(text(price))
                    if value is None:
                        raise SystemExit(f"{path} p{page_no}: {t!r}")
                    comp = text(label).rstrip(":").strip()
                    u = text(unit).replace("`", "").replace("  ", " ")
                    d.add(name, comp, value, u, page_no, name=name)
                    cont = False
                    continue
                if label and label[0]["x0"] < comp_x + 8 and not price:
                    n = text(label).replace("³", "≥")
                    name = f"{name} {n}" if cont else n
                    cont = True
                else:
                    cont = False
    return d.rows


# ------------------------------------------------------------------------- 1999-00 Eastern Energy (NEE codes)
EE_COMPONENT = re.compile(rf"^(?P<label>[A-Za-z][A-Za-z \-–]*?)\s+\$?\s?(?P<value>{NUM})\s*"
                          rf"(?P<unit>c\s*/\s*kWh|/\s*year"
                          r"|/kVA/yr)$")


def eastern_two_column(path):
    """Eastern Energy 'Schedule of network use of system tariffs' 1999-00: two columns of 'NEE10 - Name' blocks with
    'Standing Charge $60.00 / year', 'Peak Energy 7.202c / kWh', 'Contract Demand $50.67/kVA/yr' lines."""
    d = Doc(path)
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            ws_all = page.extract_words(x_tolerance=1.5)
            codes_x = sorted({round(w["x0"]) for w in ws_all if re.match(r"^NEE\d+$", w["text"])})
            split = codes_x[-1] - 5
            code = name = cls = ""
            for lo, hi in ((None, split), (split, None)):
                for top, ws in lines_of(page, x_min=lo, x_max=hi, words=ws_all):
                    t = text(ws)
                    m = re.match(r"^(NEE\d+)\s*[-–]\s*(.+)$", t)
                    if m:
                        code, name = m.group(1), m.group(2)
                        continue
                    if re.match(r"^(Small|Medium|Large) Customer Tariffs$|^High Voltage Tariffs$|^Subtransmission "
                                r"Tariffs$", t):
                        cls = t
                        continue
                    m = EE_COMPONENT.match(t)
                    if m and code:
                        unit = re.sub(r"\s+", "", m.group("unit"))
                        unit = "$/year" if unit == "/year" else ("$/kVA/yr" if unit == "/kVA/yr" else unit)
                        label = m.group("label").strip()
                        d.add(code, label, m.group("value"), unit, page_no, name=name, customer_class=cls)
    return d.rows


# --------------------------------------------------------------------------------- 1999-00 AGL Electricity
def agl_9900(path):
    """'AGL Network Tariffs for 1999/2000': class headings (Residential, Small Business, Large Business), tariff names,
    '- Standing charge $/customer pa $57.292' lines. No codes: the class and tariff name as printed."""
    d = Doc(path, note="no code printed")
    with pdfplumber.open(path) as pdf:
        cls = sub = name = ""
        for page_no, page in enumerate(pdf.pages, 1):
            ws_all = page.extract_words(x_tolerance=1.5)
            for top, ws in lines_of(page, words=ws_all):
                t = text(ws)
                x0 = ws[0]["x0"]
                if t.startswith("-") and len(ws) > 2:
                    if ws[-1]["text"].startswith(".") and ws[-2]["text"].isdigit():  # '0 .654': one number, two words
                        ws = ws[:-2] + [dict(ws[-2], text=ws[-2]["text"] + ws[-1]["text"])]
                    value = clean_num(ws[-1]["text"])
                    label_ws = [w for w in ws[1:-1] if w["x0"] < 210]
                    unit_ws = [w for w in ws[1:-1] if w["x0"] >= 210]
                    if value is None or not unit_ws:
                        raise SystemExit(f"{path} p{page_no}: {t!r}")
                    d.add(f"{cls} - {name}", text(label_ws), value, text(unit_ws), page_no, name=name,
                          customer_class=" ".join(x for x in (cls, sub) if x))
                    continue
                if t.startswith(("ATTACHMENT", "AGL Network", "Page ", "Units", "Minimum")):
                    continue
                if x0 < 65:
                    if re.match(r"^(Residential|Small Business|Large Business)\b", t):
                        cls, sub = re.split(r"\s+-\s+", t)[0], ""
                    else:
                        sub = t
                elif 69 <= x0 < 72 and not t.startswith("Peak:"):
                    name = t
    return d.rows


# ------------------------------------------------------------------ 2004-2016 AusNet (TXU, SPI, SP AusNet) list
AUSNET_CODE = re.compile(r"^([A-Z]{3}\d[0-9A-Z])(?:\s+|(?=[A-Z][a-z]))(.+)$")  # 'NEE71Large demand': glued
UNIT_START = re.compile(r"(\$/customer|\$/kVA|\$/kW|c/\)?kWh)")
EXPORT_LABEL = re.compile(r"generation|feed[- ]in|solar credit", re.I)
SEASON_HEAD = re.compile(r"^(Summer|Winter)\s*\(")


def value_of(words):
    """A price printed over one or more words ('$ 45.00', '9 .265', '(4.109)'): the number as printed, negative when
    printed in parentheses; None for a dash or no number."""
    s = "".join(w["text"] for w in words).replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if not NUM_RE.match(s):
        return None
    return ("-" + s) if neg and not s.startswith("-") else s


def ausnet_list(path, heading=None, ocr=False):
    """SPI Electricity / TXU Networks / SP AusNet 'Schedule of Network Use of System Tariffs' (2004-2016): customer
    class headings, 'NEE11 Small Residential single rate' code lines, component lines 'Standing Charge
    $/customer pa $ 45.00', 'Energy - First 1020/Quarter c/kWh 5.064', 'Contract Demand $/kVA pa $57.35'; feed-in
    payments printed in parentheses. heading: only pages whose heading names the network (NUoS) schedule (documents
    that also print the distribution and transmission schedules). ocr: the 2010 and 2011 schedules print in fonts
    without a character map; their words come from OCR of the rendered page (pdf-ocr locators)."""
    d = Doc(path, ocr=ocr)
    with pdfplumber.open(path) as pdf:
        code = name = cls = season = ""
        for page_no, page in enumerate(pdf.pages, 1):
            ws_all = ocr_words(path, page_no) if ocr else page.extract_words(x_tolerance=1.5, y_tolerance=1)
            if heading:
                head = " ".join(text(ws) for _, ws in lines_of(page, words=ws_all)[:8]).lower()
                if not re.search(heading, head):
                    continue
            lines = []
            for top, ws in lines_of(page, words=ws_all, tol=3 if ocr else 2):
                # SP AusNet 2006: a '1' printed at the start of every line of some sections ('1NEE81 High Voltage')
                if ws[0]["text"] == "1" and (len(ws) == 1 or ws[1]["x0"] - ws[0]["x1"] > 20):
                    ws = ws[1:]
                elif re.match(r"^1[A-Z]{3}\d", ws[0]["text"]):
                    ws = [dict(ws[0], text=ws[0]["text"][1:])] + ws[1:]
                for n in range(len(ws) - 1, 0, -1):  # 'ADSTc/)' 'kWh': a label's ')' printed over the unit
                    if re.search(r"c/\)?$", ws[n - 1]["text"]) and ws[n]["text"] == "kWh":
                        ws = ws[:n - 1] + [dict(ws[n - 1], text=ws[n - 1]["text"] + "kWh", x1=ws[n]["x1"])] + ws[n + 1:]
                if ws:
                    lines.append((top, ws))
            pending = []  # label words of a wrapped component label printed above its unit and price
            skip = set()
            for i, (top, ws) in enumerate(lines):
                if i in skip:
                    continue
                t = text(ws)
                if re.fullmatch(r"[A-Z]{3}\d[0-9A-Z]", t) and i + 1 < len(lines) and lines[i + 1][0] - top < 4:
                    t = f"{t} {text(lines[i + 1][1])}"  # the code a little above its name
                    skip.add(i + 1)
                m = AUSNET_CODE.match(t)
                if m:
                    code, name, season, pending = m.group(1), m.group(2), "", []
                    continue
                if re.match(r"^(Small|Medium|Large) Customer Tariffs$|^High Voltage Tariffs$|^Subtransmission Tariffs$",
                            t):
                    cls, code, pending = t, "", []
                    continue
                if not code:
                    continue
                k = next((j for j, w in enumerate(ws) if UNIT_START.search(w["text"])), None)
                info = re.match(r"^(Franchise Tariffs|Peak Times|Shoulder Times|Off Peak –|Off Peak - All"
                                r"|Minimum Demand"
                                r"|Summer demand|Date of Application|Applies to)", t)
                if k is not None and info and not value_of(ws[-1:]):
                    k = None  # a unit printed on a time-window line with no price (SP AusNet 2012-2016)
                if k is None:
                    if SEASON_HEAD.match(t):
                        season, pending = SEASON_HEAD.match(t).group(1).lower(), []
                    elif info or t.isdigit():
                        pending = []
                    else:
                        pending = ws
                    continue
                unit_ws, j = [ws[k]], k + 1
                while j < len(ws) and not re.match(r"^[\$(\-\d.]", ws[j]["text"]):
                    unit_ws.append(ws[j])
                    j += 1
                label_ws = [w for w in ws[:k] if not (NUM_RE.match(w["text"]) and w["x0"] < 60)]  # margin artefact
                vws = ws[j:]
                if not vws and i + 1 < len(lines) and lines[i + 1][0] - top < 10 and \
                        all(w["x0"] > ws[k]["x1"] + 30 for w in lines[i + 1][1]):
                    vws = lines[i + 1][1]  # the price printed a little below its label and unit
                    skip.add(i + 1)
                value = value_of(vws)
                if not vws or (value is None and text(vws) not in ("-", "$ -", "$-")):
                    raise SystemExit(f"{path} p{page_no}: unreadable price in {t!r}")
                head_word = ws[k]["text"]
                lead = head_word[:UNIT_START.search(head_word).start()]
                unit = UNIT_START.search(head_word).group(1) + head_word[UNIT_START.search(head_word).end():]
                label = text(label_ws) + ((" " + lead) if lead else "")
                if unit.startswith("c/)"):  # 'ADSTc/)kWh': the label's closing parenthesis printed over the unit
                    unit, label = "c/kWh", label + ")"
                unit = " ".join([unit] + [w["text"] for w in unit_ws[1:]])
                if not label_ws and not lead:
                    nxt = lines[i + 1][1] if i + 1 < len(lines) and i + 1 not in skip else []
                    gap = lines[i + 1][0] - top if nxt else 99
                    nxt_label = nxt and not any(UNIT_START.search(w["text"]) for w in nxt) and \
                        not AUSNET_CODE.match(text(nxt)) and nxt[-1]["x1"] < ws[k]["x0"]
                    if nxt_label and gap < 4:  # the label printed a little below its unit and price
                        label = text(nxt)
                        skip.add(i + 1)
                    else:  # a label wrapped around its unit and price
                        label = text(pending)
                        if nxt_label and gap < 12:
                            label = f"{label} {text(nxt)}"
                            skip.add(i + 1)
                n = i + 1
                while label.count("(") > label.count(")") or (n > i + 1 and n < len(lines)
                                                               and text(lines[n][1]).startswith("(")):
                    # 'Shoulder (7:00am to 3:00pm & 9:00pm to 10:00pm ADST' / 'Mon – Fri)' / '(... Weekends)'
                    nxt = lines[n][1] if n < len(lines) else []
                    if not nxt or any(UNIT_START.search(w["text"]) for w in nxt) or AUSNET_CODE.match(text(nxt)) \
                            or SEASON_HEAD.match(text(nxt)) or lines[n][0] - lines[n - 1][0] > 16:
                        break
                    label = f"{label} {text(nxt)}"
                    skip.add(n)
                    n += 1
                if not label.strip():
                    raise SystemExit(f"{path} p{page_no}: no label for {t!r}")
                pending = []
                if value is None:
                    continue
                kind = "export" if EXPORT_LABEL.search(label) else None
                d.add(code, label.strip(), value, unit, page_no, name=name, customer_class=cls, charge_type=kind,
                      season=season or None, time_band=block_band(label))
    return d.rows


# ------------------------------------------------------------------ 2017-2022-23 AusNet wide schedule of tariffs
WIDE_CODE = re.compile(r"^[A-Z]{3,4}\d[0-9A-Z]{1,2}$")
WIDE_UNIT = re.compile(r"^(\$/|c/|c$|/$)")


def rule_columns(page, min_height=300):
    """x of the vertical rules that run through a table (merged within 2pt; short boxes elsewhere left out)."""
    xs = {}
    for e in page.edges:
        if e["orientation"] == "v":
            k = next((x for x in xs if abs(x - e["x0"]) <= 2), e["x0"])
            xs[k] = xs.get(k, 0) + e["height"]
    return sorted(x for x, h in xs.items() if h >= min_height)


def wide_label(words):
    """A column heading printed over several lines; letter-spaced words ('A L L Y E A R') joined back."""
    out = " ".join(w["text"] for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])))
    out = out.replace("SHOULDE R", "SHOULDER").replace("Dedicate d", "Dedicated")
    out = re.sub(r"\b([A-Za-z]) (?=[A-Za-z]\b)", r"\1", out)
    return out.replace("ALLYEAR", "ALL YEAR")


def body_words(page):
    """Words of the page without superscripts (footnote marks, tariff-structure numbers glued to a code), which are
    printed in a smaller font than the table body, and without text printed in the colour of the filled cell it sits
    on (a spreadsheet's column-index row, invisible on the page)."""
    sizes = [c["size"] for c in page.chars if c["text"].strip()]
    if not sizes:
        return []
    body = statistics.median(sizes)
    fills = [r for r in page.rects if r["fill"] and r["width"] > 5 and r["height"] > 3]

    def hidden(c):
        mid = ((c["x0"] + c["x1"]) / 2, (c["top"] + c["bottom"]) / 2)
        under = [r for r in fills if r["x0"] <= mid[0] <= r["x1"] and r["top"] <= mid[1] <= r["bottom"]]
        cell = min(under, key=lambda r: r["width"] * r["height"], default=None)  # the innermost filled cell
        return cell is not None and cell["non_stroking_color"] == c["non_stroking_color"]

    page = page.filter(lambda o: o.get("object_type") != "char" or (o["size"] > 0.75 * body and not hidden(o)))
    return page.extract_words(x_tolerance=2, y_tolerance=1)


WIDE_CLASSES = ("Residential", "Business", "Small Business", "Medium", "Large", "High Voltage", "Sub transmission",
                "Subtransmission", "Unmetered", "Large Business")


def ausnet_wide(path, heading=r"networkuseofsystem|networktariffschedule"):
    """AusNet Services 'Schedule of Network Use of System Tariffs' (2017 to 2022-23): one row per tariff code, one
    ruled column per charging parameter (Standing charge $/year, block, peak, shoulder, off peak, dedicated circuit
    c/kWh, feed-in, capacity and critical peak demand $/kVA/year, monthly demand $/kW/month). Only the pages titled as
    the network (NUoS) schedule; the distribution, transmission and jurisdictional schedules follow it. The customer
    class is a heading row (to 2021-H1) or a merged cell left of the codes between full-width rules (2021-22 on);
    a description that wraps is printed above and below its code's line, within the row."""
    d = Doc(path)
    network = False
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            if "Standing" not in (page.extract_text() or ""):  # no schedule table on this page
                network = False
                continue
            ws_all = body_words(page)
            lines = lines_of(page, words=ws_all, tol=2)
            head = next((i for i, (_, ws) in enumerate(lines)
                         if any(w["text"] == "Standing" for w in ws) and "Tariff" in text(ws)), None)
            title = "".join(text(ws) for _, ws in lines[:head if head is not None else 14]).replace(" ", "").lower()
            if re.search(r"(useofsystem|tariff)(tariffs?)?schedule|scheduleof\w*useofsystem", title):
                network = bool(re.search(heading, title))  # a new schedule starts on this page
            elif head is None:
                network = False
            if not network or head is None:  # a schedule's later page repeats only the column headings
                continue
            hw = lines[head][1]
            std_x = next(w["x0"] for w in hw if w["text"] == "Standing")
            desc_x = next(w["x0"] for w in hw if w["text"] == "Description")
            name_end = next(w["x0"] for w in hw if w["x0"] > desc_x + 20)  # 'Closed to New Entrants' or 'Standing'
            codes = [(i, next(w for w in ws if w["x1"] < desc_x + 2 and WIDE_CODE.match(w["text"])))
                     for i, (_, ws) in enumerate(lines) if i > head and
                     any(w["x1"] < desc_x + 2 and WIDE_CODE.match(w["text"]) for w in ws)]
            if not codes:
                raise SystemExit(f"{path} p{page_no}: no tariff codes")
            first = codes[0][0]
            code_x = min(c["x0"] for _, c in codes)
            rules = rule_columns(page)
            cells = [(a, b) for a, b in zip(rules, rules[1:]) if b > std_x + 2]
            # (the class heading row above the first code carries a spreadsheet column-index row, '3.00 4.0000 ...
            # #REF!', in the colour of its shading: invisible on the page)
            hdr = [w for _, ws in lines[head:first] for w in ws if w["x0"] >= cells[0][0] - 1 and not
                   (re.match(r"^\d+\.\d+$", w["text"]) or w["text"].startswith("#"))]
            cols = []
            for a, b in cells:
                inside = [w for w in hdr if a - 1 <= (w["x0"] + w["x1"]) / 2 < b + 1]
                units = [w for w in inside if WIDE_UNIT.match(w["text"]) or re.match(r"^[KkWh]$", w["text"])]
                label = wide_label([w for w in inside if w not in units])
                unit = re.sub(r"\d+$", "", "".join(w["text"] for w in sorted(units, key=lambda w: w["x0"])))
                unit = unit + "r" if unit.endswith("/yea") else unit
                if not label and not unit:
                    cols.append((a, b, None, None))  # a double rule at the table edge
                    continue
                if not label or not unit:
                    raise SystemExit(f"{path} p{page_no}: column {a:.0f}-{b:.0f} label {label!r} unit {unit!r}")
                cols.append((a, b, label, unit))
            # rows: each code line, with the band halfway to its neighbours (a wrapped description sits in it)
            tops = [lines[i][0] for i, _ in codes]
            step = statistics.median([b - a for a, b in zip(tops, tops[1:])] or [10])
            bands = [((tops[k - 1] + t) / 2 if k else t - step / 2, (t + tops[k + 1]) / 2 if k + 1 < len(tops)
                      else t + step / 2) for k, t in enumerate(tops)]
            # a class cell's top and bottom: the stroked rules that also cross the class column
            full = sorted(e["top"] for e in page.lines if e["top"] == e["bottom"] and e["x0"] < code_x - 20 and
                          e["x1"] > std_x)
            class_words = [w for w in ws_all if w["x1"] < code_x - 1 and w["top"] > lines[head][0] + 15]
            cls = ""
            for k, (i, code_w) in enumerate(codes):
                lo, hi = bands[k]
                if class_words:  # merged class cell between full-width rules
                    top = lines[i][0]
                    above = max((y for y in full if y <= top), default=0)
                    below = min((y for y in full if y > top), default=page.height)
                    cls = text(sorted((w for w in class_words if above < w["top"] < below),
                                      key=lambda w: (round(w["top"]), w["x0"])))
                else:  # heading rows since the previous code
                    prev = codes[k - 1][0] if k else head
                    for _, ws in lines[prev + 1:i]:
                        label = text(w for w in ws if not (NUM_RE.match(w["text"]) or w["text"].startswith("#")))
                        if label in WIDE_CLASSES:
                            cls = label
                name_ws = [w for _, ws in lines if lo <= _ < hi for w in ws
                           if w["x0"] >= desc_x - 3 and w["x1"] <= name_end + 3]
                name = text(sorted(name_ws, key=lambda w: (round(w["top"]), w["x0"])))
                for a, b, label, unit in cols:
                    vs = [w for w in lines[i][1] if a - 1 <= (w["x0"] + w["x1"]) / 2 < b + 1]
                    if not vs:
                        continue
                    if label is None:
                        raise SystemExit(f"{path} p{page_no}: {code_w['text']}: {text(vs)!r} under no heading")
                    value = clean_num("".join(w["text"] for w in vs))
                    if value is None:
                        raise SystemExit(f"{path} p{page_no}: {code_w['text']} {label}: {text(vs)!r}")
                    kind = "export" if re.search(r"export|feed ?in", label, re.I) else None
                    d.add(code_w["text"], label, value, unit, page_no, name=name, customer_class=cls,
                          charge_type=kind)
    return d.rows


# ---------------------------------------- 2003 to 2022-23 AGL Electricity / Alinta AE / Jemena (JEN) network tariffs
AGL_CODE = re.compile(r"^[A-Z][0-9A-Z]{3}$")
AGL_CLASSES = ("Residential", "Small Business", "Large Business", "Unmetered Supply", "Public Lighting")


def agl_words(page):
    """Words without doubled glyphs (a bold effect that prints every character twice) and without superscript footnote
    marks ('F100a', 'embedded generationc'), which are printed smaller than the body text."""
    page = page.dedupe_chars()
    sizes = [c["size"] for c in page.chars if c["text"].strip()]
    body = statistics.median(sizes) if sizes else 0
    page = page.filter(lambda o: o.get("object_type") != "char" or o["size"] > 0.75 * body)
    return page, page.extract_words(x_tolerance=1.5)


SYMBOL_GLYPHS = {"\uf0b3": "≥", "³": "≥", "‡": "≥", "\uf0a3": "≤", "£": "≤"}


def agl_name(name):
    """A name with its Symbol-font comparison signs read back: the glyph comes out as '³'/'\uf0b3'/'‡' for ≥ and
    '£'/'\uf0a3'/'(cid:31)' for ≤ (the same tariffs print '≥ 55 GWh' and '≤ 0.8 GWh' in other years), sometimes
    twice."""
    name = re.sub(r"(\S)([£‡³\uf0a3\uf0b3])\s+\2", r"\1 \2", name)
    name = re.sub(r"[£‡³\uf0a3\uf0b3]", lambda m: SYMBOL_GLYPHS[m.group(0)], name).replace("(cid:31)", "≤")
    return re.sub(r"\b(LV|HV)(MS|EN|RF)\b|\b(Subtransmission)(MA|EG)\b",
                  lambda m: " ".join(g for g in m.groups() if g), name)


def agl_codes(ws):
    """('A10I / F10I* Time of Use ...' words) -> (['A10I', 'F10I'], name words); [] when the line has no code."""
    codes, i = [], 0
    while i < len(ws):
        t = ws[i]["text"].rstrip("*")
        glued = re.match(r"^([A-Z][0-9A-Z]{3})\*(\S+)$", ws[i]["text"])  # 'F210*Time'
        if glued:
            return codes + [glued.group(1)], [dict(ws[i], text=glued.group(2))] + ws[i + 1:]
        if not AGL_CODE.match(t):
            break
        codes.append(t)
        i += 1
        if i < len(ws) and ws[i]["text"] == "/":
            i += 1
            continue
        break
    return codes, (ws[i:] if codes else ws)


def agl_list(path):
    """'Network Tariffs for the <year> Calendar Year (Exclusive of GST)' (AGL Electricity 2003-2006, Alinta AE 2007,
    Jemena 2008 on; 'Network Tariffs Effective 1 July 2021' from 2021-H1): columns Customer Class, Code, Tariff Name,
    Units, Rate. A tariff line carries its code or codes ('A10I / F10I*', 'A100 / F100a / T100b': one tariff priced
    the same under each code) and name; its charges follow as '- Standing charge  $/customer pa  $24.600' lines,
    under 'Summer rates' / 'Non-summer rates' sub-headings for seasonal tariffs. Pages titled 'Distribution
    Tariffs', 'Transmission Tariffs' and the like (the DUoS, TUoS and other parts of the same tariffs, and metering)
    are left out."""
    d = Doc(path)
    network = False
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            page, words = agl_words(page)
            lines = lines_of(page, tol=3, words=words)  # a symbol-font glyph (≥, ≤) sits up to 2.5pt higher
            hi = next((i for i, (_, ws) in enumerate(lines) if "Units" in text(ws).split() and
                       "Rate" in text(ws).split() and "Code" in text(ws)), None)
            if hi is None:  # 2010 prints the headings on the first page only
                if not network or not any(ws[0]["text"] == "-" for _, ws in lines):
                    network = False
                    continue
                hi = -1
            else:
                title = " ".join(text(ws) for _, ws in lines[:hi])
                network = "Network Tariffs" in title
                if not network:
                    if not re.search(r"(Distribution|Transmission|Metering Service|Jurisdictional( Scheme)?"
                                     r"|TUOS and JS) Tariffs", title):
                        raise SystemExit(f"{path} p{page_no}: unknown schedule {title!r}")
                    continue
                hw = lines[hi][1]
                units_x = next(w["x0"] for w in hw if w["text"] == "Units")
                rate_x = next(w["x0"] for w in hw if w["text"] == "Rate")
                name_x = next(w["x0"] for w in hw if w["text"] == "Tariff" and w["x0"] > 60)
            # the codes' column (the heading reads 'Customer ClasCode' in 2008)
            code_x = statistics.median(ws[0]["x0"] for _, ws in lines[hi + 1:] if agl_codes(ws)[0])
            for k in range(hi + 1, len(lines)):
                top, ws = lines[k]
                first = ws[0]
                if first["x0"] < code_x - 5 and not re.match(r"^(Available|Only|[a-z]\))", first["text"]):
                    label = text(ws)
                    if label.startswith(AGL_CLASSES):
                        cls = sub = label
                    elif re.search(r"Tariffs \(", label) and " - " not in cls:
                        sub = cls + " - " + label.split(" (")[0]
                    continue
                found, name_ws = agl_codes(ws)
                if found and abs(first["x0"] - code_x) < 8:
                    codes, name, season = found, agl_name(text(name_ws)), None
                    nxt = lines[k + 1] if k + 1 < len(lines) else None
                    if nxt and nxt[0] - top < 9 and len(nxt[1]) <= 2 and nxt[1][0]["x0"] > name_x and \
                            all(re.match(r"^[A-Z]{2,3}$", w["text"]) for w in nxt[1]):
                        name += " " + text(nxt[1])  # 'A30M LV 0.4 - 0.8 GWh' over 'MS' (multiple supply)
                    continue
                m = re.match(r"^(Summer|Non-summer|Winter) rates$", text(ws))
                if m:
                    season = {"Summer": "summer", "Non-summer": "non_summer", "Winter": "winter"}[m.group(1)]
                    continue
                if first["text"] != "-":
                    continue
                n = max(n for n in (1, 2, 3) if len(ws) > n and ws[-n]["x0"] > units_x + 30 and
                        clean_num("".join(w["text"] for w in ws[-n:])) is not None) if \
                    clean_num(ws[-1]["text"]) is not None or len(ws) > 2 else 0
                if not n:
                    raise SystemExit(f"{path} p{page_no}: {codes} no rate in {text(ws)!r}")
                value = clean_num("".join(w["text"] for w in ws[-n:]))
                label = text(w for w in ws[1:-n] if w["x1"] < units_x - 2)
                unit = text(w for w in ws[1:-n] if w["x1"] >= units_x - 2).rstrip("*")
                for code in codes:
                    d.add(code, label, value, unit, page_no, name=name, customer_class=sub, season=season,
                          time_band=block_band(label))
    return d.rows


# ------------------------------------------------------------------------------------------------ plan
A = "sources/archive/ausnet/"
J = "sources/archive/jemena/"
# (pricing year, layout, path, args): the one document per distributor-year that customers were billed on (or the
# provisional one when no final document is held); see the module docstring of each layout
NUOS_HEAD = r"schedule of network use of system"  # the NUoS pages of a report that also prints DUoS and TUoS
PLAN = [
    ("1996-97", vic_combined, A + "1996-97/nt9697.pdf", ("Eastern",)),
    ("1997-98", vic_combined, A + "1997-98/nt9798.pdf", ("Eastern",)),
    ("1998-99", eastern_9899, A + "1998-99/ee9899v2.pdf", ()),
    ("1999-00", eastern_two_column, A + "1999-00/ent9900.pdf", ()),
    ("2004", ausnet_list, A + "2004/TXU2004NetworkTariffs29Dec03_All.pdf", (NUOS_HEAD,)),
    ("2005", ausnet_list, A + "2005/SPI_ElectricityNetworkTariffReport2005.pdf", (NUOS_HEAD,)),
    ("2006", ausnet_list, A + "2006/SP_AusNet_2006_NUoS_Schedule.pdf", ()),
    ("2007", ausnet_list, A + "2007/Annual_Tariff_Report_Electricity_2007.pdf", (NUOS_HEAD,)),
    ("2008", ausnet_list, A + "2008/NUoS_Schedule_Elec.pdf", ()),
    ("2009", ausnet_list, A + "2009/SPAusNetNetworkTariffSchedule2009.pdf", ()),
    ("2010", ausnet_list, A + "2010/SP_AusNet_Network_Tariff_Schedule_-_2010_0.pdf", (None, True)),
    ("2011", ausnet_list, A + "2011/SP_AusNet_Schedule_of_Network_Tariffs_2011_V2.pdf", (None, True)),
    ("2012", ausnet_list, A + "2012/Schedule_of_Network_Use_of_System_Tariffs2012.pdf", ()),
    ("2013", ausnet_list, A + "2013/Schedule_of_Network_Use_of_System_Tariffs_2013.pdf", ()),
    ("2014", ausnet_list, A + "2014/Schedule_of_Network_Use_of_System_Tariffs_2014.pdf", ()),
    ("2015", ausnet_list, A + "2015/AusNet_Services_Schedule_of_Network_Use_of_System_Tariffs.pdf", ()),
    ("2016", ausnet_list, A + "2016/Network_Schedule.pdf", ()),
    ("2017", ausnet_wide, A + "2017/Schedule_of_Tariffs.pdf", ()),
    ("2018", ausnet_wide, A + "2018/AER_approved_-_AusNet_Services_-_Schedule_of_Tariffs_-_23_October_2017.pdf", ()),
    ("2019", ausnet_wide, A + "2019/Annual-Tariff-Report-2019.ashx.pdf", ()),
    ("2020", ausnet_wide, A + "2020/AusNet-Services---Schedule-of-Tariffs-2020.ashx.pdf", ()),
    ("2021-H1", ausnet_wide, A + "2021-H1/Attachment_2_-_AusNet_Services_-_Schedule_of_Tariffs_HY2021_v1.1_0.pdf", ()),
    ("2021-22", ausnet_wide, A + "2021-22/AusNet-Services---Schedule-of-Tariffs-2021-22.ashx.pdf", ()),
    ("2022-23", ausnet_wide,
     A + "2022-23/Attachment_4_-_AusNet_Services_-_Schedule_of_Tariffs_2022-23-_6_April_2022.pdf", ()),
    ("1996-97", vic_combined, J + "1996-97/nt9697.pdf", ("Solaris",)),
    ("1997-98", vic_combined, J + "1997-98/nt9798.pdf", ("Solaris",)),
    ("1999-00", agl_9900, J + "1999-00/agl992000.pdf", ()),
    ("2003", agl_list, J + "2003/AGLE2003NetworkTariffSchedule.pdf", ()),
    ("2004", agl_list, J + "2004/AGL2004_NetworkTariffs_24Dec03.pdf", ()),
    ("2005", agl_list, J + "2005/AGL_2005NetworkTariffsCalendarYear.pdf", ()),
    ("2006", agl_list, J + "2006/AGLE_2006tariffschedule.pdf", ()),
    ("2007", agl_list, J + "2007/070109AlintaAEapproved2007tariffs.pdf", ()),
    ("2008", agl_list, J + "2008/071220Jemena2008TariffsSchedule.pdf", ()),
    ("2009", agl_list, J + "2009/2009JENNetworkTariffsSchedule.pdf", ()),
    ("2010", agl_list, J + "2010/JEN_2010_Tariff_Schedule.pdf", ()),
    ("2011", agl_list, J + "2011/Network_Tariff_Schedule_2011.pdf", ()),
    ("2012", agl_list, J + "2012/2012_Tariff_Schedule.pdf", ()),
    ("2013", agl_list, J + "2013/2013_Tariff_Schedule.pdf", ()),
    ("2014", agl_list, J + "2014/2014_Tariff_Schedule.pdf", ()),
    ("2015", agl_list, J + "2015/2015_tariff_schedule.pdf", ()),
    ("2016", agl_list, J + "2016/2016-Tariff-Schedule.aspx.pdf", ()),
    ("2017", agl_list, J + "2017/2017-Tariff-Schedule.aspx.pdf", ()),
    ("2018", agl_list, J + "2018/AER_approved_-_Jemena_-_2018_Pricing_Proposal_-_13_October_2017.pdf", ()),
    ("2019", agl_list, J + "2019/AER_approved_-_Jemena_-_Attachment_2_Tariff_schedule_2019_-_29_October_2018.pdf", ()),
    ("2020", agl_list, J + "2020/2020-Tariff-Schedule.aspx.pdf", ()),
    ("2021-H1", agl_list, J + "2021-H1/2021-tariff-schedule.aspx.pdf", ()),
    ("2021-22", agl_list, J + "2021-22/2022-tariff-schedule.aspx.pdf", ()),
    ("2022-23", agl_list,
     J + "2022-23/Attachment_1_-_Jemena_2022-23_network_tariff_schedule_-_updated_13_May_2022.pdf", ()),
]


def main():
    rows = []
    for year, layout, path, args in PLAN:
        got = layout(path, *args)
        if not got:
            raise SystemExit(f"{path}: no prices read")
        if common.document(path)["pricing_year"] != year:
            raise SystemExit(f"{path}: filed under another pricing year than {year}")
        print(f"  {year:8} {len(got):4} rows  {len({r['tariff_code'] for r in got}):3} tariffs  {path}")
        rows += got
    for r in rows:
        check_value(os.path.join(common.ROOT, r["source_file"]), r["locator"], r["value"])
    common.write(SLUG, rows)


if __name__ == "__main__":
    main()
