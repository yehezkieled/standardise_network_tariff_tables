"""Historical network tariffs (pricing years before 2023-24) of SA Power Networks (ETSA Utilities before 2012) and
Power and Water Corporation (Power and Water Authority, PAWA, before 2002), read from the archived documents
(sources/archive/, contract scripts/history/CONTRACT.md) into out/history/sapn_pwc.csv.

One function per document layout; DOCUMENTS lists, per distributor and pricing year, the one document read for 1 July
(or the first day of the year's prices) and any mid-year re-issue (its first day is the inventory's effective_from).

  .venv/bin/python scripts/history/sapn_pwc.py
"""
import difflib
import functools
import os
import re
import sys
from statistics import median

import pdfplumber

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

sys.path.insert(0, os.path.join(common.ROOT, "scripts", "tariffdb"))
import locators  # noqa: E402

SLUG = "sapn_pwc"
NUM = r"-?\d[\d,]*\.?\d*|-?\.\d+"


# ---------------------------------------------------------------------------------------------------- page text
class Line:
    """One printed line rebuilt from the page's glyphs: tokens split on gaps, so a number whose glyphs touch is one
    token even where the text layer inserts stray spaces ('2 .936' is printed as 2.936)."""

    def __init__(self, chars):
        self.chars = sorted(chars, key=lambda c: c["x0"])
        self.top = min(c["top"] for c in chars)
        size = median(c["size"] for c in self.chars)
        words, cur, prev = [], [], None
        for c in self.chars:
            if c["text"] == " ":  # a printed space glyph (kept by lines(..., spaces=True)) ends a word
                if cur:
                    words.append(cur)
                cur, prev = [], None
                continue
            if prev is not None and c["x0"] - prev["x1"] > max(1.0, 0.18 * size) and cur:
                words.append(cur)
                cur = []
            cur.append(c)
            prev = c
        if cur:
            words.append(cur)
        self.words = [("".join(c["text"] for c in w), w[0]["x0"], w[-1]["x1"]) for w in words]
        self.text = " ".join(w[0] for w in self.words)

    def __repr__(self):
        return self.text


@functools.lru_cache(maxsize=64)
def lines(path, page_no, ytol=2.0, upright_only=True, spaces=False):
    """Lines of a page (1-based), top to bottom; space glyphs, repeated glyphs (overprinted bold) and, with
    upright_only, rotated glyphs (the 'Uncontrolled document' watermark of the 2013-14 and 2014-15 SA Power Networks
    manuals) dropped. upright_only=False for the Excel exports whose table glyphs are flagged non-upright; spaces=True
    splits words at their printed space glyphs too (their name glyphs touch across word spaces)."""
    with pdfplumber.open(os.path.join(common.ROOT, path)) as doc:
        chars = [c for c in doc.pages[page_no - 1].chars if (c["text"].strip() or spaces and c["text"] == " ") and
                 (c.get("upright", True) or not upright_only)]
    seen, uniq = set(), []
    for c in chars:
        k = (c["text"], round(c["x0"]), round(c["top"]))
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    uniq.sort(key=lambda c: (c["top"], c["x0"]))
    groups = []
    for c in uniq:
        if c["text"] == " " and not groups:
            continue
        if groups and abs(groups[-1][0]["top"] - c["top"]) <= ytol:
            groups[-1].append(c)
        else:
            groups.append([c])
    return [Line(g) for g in groups]


@functools.lru_cache(maxsize=None)
def n_pages(path):
    with pdfplumber.open(os.path.join(common.ROOT, path)) as doc:
        return len(doc.pages)


@functools.lru_cache(maxsize=None)
def _ocr():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


@functools.lru_cache(maxsize=64)
def ocr_raw(path, page_no, resolution=288):
    """RapidOCR's result for a page rendered at the resolution locators.ocr_text re-reads it with, so every value
    taken from a box is in the text check.py verifies against."""
    with pdfplumber.open(os.path.join(common.ROOT, path)) as doc:
        result, _ = _ocr()(doc.pages[page_no - 1].to_image(resolution=resolution).original)
    return result or []


def ocr_boxes(path, page_no, resolution=288):
    """Recognised text boxes of an image-only page as (text, x0, y_mid, confidence)."""
    return [(t, min(p[0] for p in box), (box[0][1] + box[2][1]) / 2, conf)
            for box, t, conf in ocr_raw(path, page_no, resolution)]


def ocr_lines(path, page_no, ytol=14):
    """The OCR boxes of a page as Line-like rows: .words [(text, x0, x1)] left to right, .text, .top (y)."""
    rows = []
    for box, t, _ in sorted(ocr_raw(path, page_no), key=lambda r: (r[0][0][1] + r[0][2][1]) / 2):
        y = (box[0][1] + box[2][1]) / 2
        w = (t, min(q[0] for q in box), max(q[0] for q in box))
        if rows and abs(rows[-1][0] - y) < ytol:
            rows[-1][1].append(w)
        else:
            rows.append([y, [w]])
    out = []
    for y, ws in rows:
        ln = Line.__new__(Line)
        ln.words, ln.top = sorted(ws, key=lambda w: w[1]), y
        ln.text = " ".join(w[0] for w in ln.words)
        out.append(ln)
    return out


def nums(text):
    """Numbers printed in a piece of text, as printed ('$1,234.50' -> '1234.50')."""
    return [m.replace(",", "") for m in re.findall(r"(?<![\w.])\$?\s?(" + NUM + r")(?![\w])", text)]


def tail_nums(text):
    """The numbers at the end of a line, after its label, as printed."""
    m = re.search(r"((?:\s*\$?\s?(?:" + NUM + r"))+)\s*$", text)
    return nums(m.group(1)) if m else []


# ---------------------------------------------------------------------------------------------------- Power and Water
PWC_ZONE = {"northern grid": "Northern Grid", "alice springs": "Alice Springs", "tennant creek": "Tennant Creek"}


def pwc_1999(doc):
    """UC approval 24 March 2000, 1 April-30 June 2000: one c/kWh rate per customer group and network (p1 table)."""
    p, out = 1, []
    groups = [(">4GWh per year, supplied and metered at high voltage", "high 4.20"),
              (">4GWh per year, supplied and metered at low voltage", "low 4.40"),
              ("<4 GWh per year", "<4 GWh per year")]
    text = [ln.text for ln in lines(doc["local_path"], p)]
    assert any("Usage Darwin Katherine" in t for t in text), "1999-00 table header"
    for name, key in groups:
        t = next(t for t in text if key.replace(" ", "") in t.replace(" ", ""))
        vals = re.findall(r"(\d+\.\d+) cents per kWh", t)
        for zone, v in zip(("Darwin", "Katherine"), vals):
            out.append(common.row(doc, name, "Usage", v, "cents per kWh", locators.pdf(p), name=name,
                                  charge_type="energy", time_band="anytime",
                                  note=f"zone: {zone}; no code printed; before GST; network access tariffs 1 April "
                                       f"to 30 June 2000 (p1)"))
        if "not applicable" in t:
            assert len(vals) == 1
    return out


def pwc_uc_schedule(doc):
    """Utilities Commission approval instrument with schedules 'A - For Customers ... above 750 MWh' (contestable,
    demand + 7-step energy blocks, peak/off-peak) and 'B - ... below 750 MWh' (system availability charge per day and
    two energy blocks), one page per network (Northern Grid, Alice Springs, Tennant Creek) plus the Northern Grid DKTL
    common service amounts (2002-03 on). 2000-01 to 2008-09."""
    path, out = doc["local_path"], []
    for p in range(1, n_pages(path) + 1):
        ls = lines(path, p)
        head = " ".join(ln.text for ln in ls[:3])
        m = re.search(r"(Schedule\s*\d+\s*\(?\w?\)?\s*-?\s*|PAWA Network Charges\s*)(Northern Grid|Alice Springs|"
                      r"Tennant Creek)(\s*-\s*DKTL)?", head)
        if not m:
            continue
        zone, dktl = PWC_ZONE[m.group(2).lower()], bool(m.group(3))
        gst = "incl" if re.search(r"INCLUDING\s*GST", head) else "excl"
        assert gst == "incl" or re.search(r"EXCLUDING\s*GST", head), (path, p, head)
        sect, pending = None, None
        for i, ln in enumerate(ls):
            t = ln.text
            ms = re.match(r"^([AB])\s*-", t)
            if ms:
                sect = ms.group(1)
                continue
            if sect is None:
                continue
            note = f"zone: {zone}" + ("; DKTL common service amount added to the Northern Grid rates" if dktl else "")
            if gst == "incl":
                note += "; schedule printed INCLUDING GST only"

            def add(component, value, unit, charge, band, cls=""):
                if value.lower() == "nil":
                    return
                comp = ("DKTL " if dktl else "") + component
                out.append(common.row(doc, sect, comp, value, unit, locators.pdf(p), customer_class=cls,
                                      name=("Customers above 750 MWh per year" if sect == "A"
                                            else "Customers below 750 MWh per year"),
                                      gst=gst, charge_type=charge, time_band=band, note=note))
            vals = tail_nums(t)
            if re.match(r"^Dollars per month", t) and sect == "A":
                v = re.search(r"(\$\s*[\d.,]+|Nil)\s*$", t).group(1).replace("$", "").replace(" ", "")
                add("System Availability Charge", v, "$ per month", "fixed", "")
            elif re.match(r"^(First|Next|Any further|For any) [\d, ]*kVA per month", t):
                v = re.findall(r"\$\s?[\d.]+|Nil", t)
                assert len(v) == 2, (path, p, t)
                label = re.match(r"^(.*?kVA per month)", t).group(1)
                add(f"Demand peak: {label}", v[0].replace("$", "").strip(), "$/kVA per month", "demand", "peak")
                add(f"Demand off peak: {label}", v[1].replace("$", "").strip(), "$/kVA per month", "demand", "offpeak")
            elif sect == "A" and re.match(r"^(First|Next|Any further|For any) .*(kWh|energy) per month", t):
                label = re.match(r"^(.*?(kWh|energy) per month)", t).group(1)
                assert len(vals) == 2, (path, p, t)
                unit = "¢/kWh" if "¢/kWh" in " ".join(x.text for x in ls[:12]) else "c/kWh"
                add(f"Energy peak: {label}", vals[0], unit, "energy", "peak")
                add(f"Energy off peak: {label}", vals[1], unit, "energy", "offpeak")
            elif sect == "B" and re.match(r"^(Commercial|Domestic): cents per day", t):
                cls = t.split(":")[0]
                v = re.search(r"([\d.]+|Nil)\s*$", t).group(1)
                add(f"System Availability Charge - {cls}", v, "cents per day", "fixed", "", cls)
            elif sect == "B" and re.match(r"^(First 1,000 kWh|Energy used above 1,000 kWh|For any kWh per month)", t):
                label = re.match(r"^(First 1,000 kWh per month|Energy used above 1,000 kWh per month|For any kWh per "
                                 r"month)", t).group(1)
                v = vals or tail_nums(ls[i + 1].text)  # value printed on the label's continuation line
                assert len(v) == 1 and not re.search(r"\d", t[:len(label)].replace("1,000", "")), (path, p, t)
                band = "block1" if label.startswith("First") else "block2" if label.startswith("Energy") else "anytime"
                add(f"Energy: {label}", v[0], "c/kWh", "energy", band)
            elif sect == "B" and t.startswith("Street lighting"):
                v = vals or pending
                assert v and len(v) == 1, (path, p, t)
                add("Energy: Street lighting" + (" and other unmetered supplies" if "unmetered" in t else ""), v[0],
                    "c/kWh", "energy", "anytime")
            pending = vals if re.fullmatch(NUM, t.strip()) else None
    return out


SKIPPED = []  # (path, page, what): printed values that could not be read
METERING = []  # per-tariff metering charges printed in a price table (schema.METERING_COLUMNS)


def page_text(path, p, ocr=False):
    """The page as one whitespace-normalised string: glyph lines (numbers whose glyphs touch kept whole), or the OCR
    text of an image-only page."""
    t = locators.ocr_text(os.path.join(common.ROOT, path), p) if ocr else " ".join(ln.text for ln in lines(path, p))
    return re.sub(r"\s+", " ", t)


def L(text):
    """Regex for a printed phrase whose word spaces the text layer may drop."""
    return re.escape(text).replace(r"\ ", r"\s?")


def spaced(label):
    """A printed label with the word spaces the text layer drops ('kVAper month', 'Energy usedabove') restored."""
    return re.sub(r"\s+", " ", re.sub(r"(kWh|kVA|per|month|Energy|used|above|usage|First|Next)", r" \1 ", label)).strip()


def pwc_all_regions(doc, pages, ocr=False):
    """Power Networks 'Electricity Network Tariffs and Charges' Appendix B (2014-15 to 2018-19): 'Schedule A - All
    Regions' (HV > 750 MWh), 'Schedule B' (LV > 750 MWh): system availability charge per month, 5-step peak/off-peak
    demand ($/kVA per month) and energy (c/kWh); 'Schedule C' (< 750 MWh): system availability charge per day for
    Domestic and Commercial, Domestic/Commercial energy blocks or 'Energy usage', Unmetered street lighting and
    traffic lights. `pages`: the schedule pages to read (the GST-exclusive copy where the document prints both)."""
    path, out = doc["local_path"], []
    for p in pages:
        t = page_text(path, p, ocr)
        heads = list(re.finditer(r"Schedule ([ABC]) - All ?Regions (\d{4}/\d{2}) (INCLUDING|Including|EXCLUDING) GST", t))
        assert heads and len(heads) == len(re.findall(r"Schedule [ABC] -", t)), (path, p)
        for k, h in enumerate(heads):
            code, gst = h.group(1), "excl" if h.group(3) == "EXCLUDING" else "incl"
            body = t[h.end():heads[k + 1].start() if k + 1 < len(heads) else len(t)]
            name = re.match(r"\s*" + code + r" - For (.*?) (?:Reference|System Availability)", body).group(1).strip()
            loc = locators.pdf(p, ocr=ocr)
            note = f"Schedule {code} - All Regions, {'INCLUDING GST only' if gst == 'incl' else 'EXCLUDING GST'} (p{p})"
            if ocr:
                note += "; read by OCR"

            def add(component, value, unit, charge, band, cls=""):
                value = value.replace("$", "").replace(",", "").strip()
                if not re.fullmatch(r"\d+\.\d+", value):
                    SKIPPED.append((path, p, f"{code} {component}: OCR read {value!r}"))
                    return
                out.append(common.row(doc, code, component, value, unit, loc, name=name, customer_class=cls, gst=gst,
                                      charge_type=charge, time_band=band, note=note))

            if code in "AB":
                m = re.search(L("Dollars per month per meter ") + r"\$?\s?([\d.,]+)", body)
                add("System Availability Charge", m.group(1), "$ per month per meter", "fixed", "")
                rows_d = re.findall(r"((?:First|Next|Any further) (?:[\d,]+ ?)?kVA ?per ?month) (\$?[\d.,]+) (\$?[\d.,]+)",
                                    body)
                rows_e = re.findall(r"((?:First|Next|Any further) (?:[\d,]+ ?)?kWh ?per ?month) (\$?[\d.,]+) (\$?[\d.,]+)",
                                    body)
                assert len(rows_d) == 5 and len(rows_e) == 5, (path, p, code, rows_d, rows_e)
                rows_d = [(spaced(lb), a, b) for lb, a, b in rows_d]
                rows_e = [(spaced(lb), a, b) for lb, a, b in rows_e]
                for label, pk, op in rows_d:
                    add(f"Demand peak: {label}", pk, "$/kVA per month", "demand", "peak")
                    add(f"Demand off peak: {label}", op, "$/kVA per month", "demand", "offpeak")
                for label, pk, op in rows_e:
                    add(f"Energy peak: {label}", pk, "¢/kWh", "energy", "peak")
                    add(f"Energy off peak: {label}", op, "¢/kWh", "energy", "offpeak")
            else:
                for cls, v in re.findall(L("Cents per day per meter - ") + r"(Domestic|Commercial) ([\d.]+)", body):
                    add(f"System Availability Charge - {cls}", v, "cents per day per meter", "fixed", "", cls)
                energy = body[re.search(L("metered anytime"), body).start():]
                parts = re.split(r" (Domestic|Commercial|Unmetered) (?=First|Energy|Street)", energy)
                n = 0
                for cls, seg in zip(parts[1::2], parts[2::2]):
                    if cls == "Unmetered":
                        for label, rx in (("Street lighting and similar consumption profiled unmetered supplies",
                                           r"Street lighting and similar consumption profiled ([\d.]+) unmetered"),
                                          ("Traffic lights and similar unmetered 24 hour supplies",
                                           r"Traffic lights and similar unmetered 24 hour ([\d.]+) supplies")):
                            add(f"Unmetered: {label}", re.search(rx.replace(" ", " ?"), seg).group(1), "¢/kWh", "energy", "anytime",
                                "Unmetered")
                            n += 1
                        continue
                    steps = re.findall(r"((?:First|Next) ?[\d,]+ ?kWh ?per ?month|Energy ?used ?above ?[\d,]+ ?kWh ?per ?month|"
                                       r"Energy ?usage) ([\d.]+)", seg)
                    steps = [(spaced(lb), v) for lb, v in steps]
                    assert steps, (path, p, cls, seg)
                    for i, (label, v) in enumerate(steps, 1):
                        band = "anytime" if label == "Energy usage" else f"block{i}"
                        add(f"{cls}: {label}", v, "¢/kWh", "energy", band, cls)
                        n += 1
                assert n >= 4, (path, p, energy)
    return out


def pwc_charging_parameter(doc, page, title, gst_note):
    """AER-approved / proposed Network Pricing Proposal 2019-20 to 2022-23: 'tariffs by charging parameter' table,
    Tariff 1-7 with SAC ($/NMI/day), Anytime Energy Charge ($/kWh) and Demand ($/kVA/month); '-' where not charged."""
    path, out = doc["local_path"], []
    ls = [ln.text for ln in lines(path, page)]
    i = next(i for i, t in enumerate(ls) if re.search(title, t))
    hdr = " ".join(ls[i + 1:i + 4])
    assert re.search(r"SAC Anytime Energy Charge Demand", hdr) and re.search(r"\$/NMI/day \$/kWh \$/kVA/month", hdr), hdr
    note = f"{ls[i].strip()} (p{page}); {gst_note}"
    if doc["price_status"] != "approved":
        note += "; proposed prices"
    for t in ls[i + 4:]:
        m = re.match(r"^(Tariff \d+): (.+?) (-|[\d.]+) (-|[\d.]+) (-|[\d.]+)$", t)
        if not m:
            if out:
                break
            continue
        code, name, sac, energy, demand = m.groups()
        name = re.sub(r" Tariff$", "", name)
        for comp, v, unit, charge, band, extra in (
                ("SAC", sac, "$/NMI/day", "fixed", "", ""),
                ("Anytime Energy Charge", energy, "$/kWh", "energy", "anytime", ""),
                ("Demand", demand, "$/kVA/month", "demand", "",
                 "; demand charged on the monthly peak-period demand (table footnote / chapter 5)")):
            if v == "-":
                continue
            out.append(common.row(doc, code, comp, v, unit, locators.pdf(page), name=name, charge_type=charge,
                                  time_band=band, note=note + extra))
    assert len({r["tariff_code"] for r in out}) == 7, (path, page)
    return out


# ---------------------------------------------------------------------------------------------------- SA Power Networks
ETSA_UNIT = re.compile(r"(\$/k(?:VA|W)\s?p\.a\.|\$\s?p\.a\.|c/kWh|\$/kWh|\$/k(?:VA|W)/(?:day|mth)|\$/day)")
ETSA_LABELS = ("Supply Rate", "Anytime Usage Rate")  # the labels a '(Cust ...)' billing alias is printed over


def subsequence(small, big):
    it = iter(big)
    return all(ch in it for ch in small)


def etsa_band(label):
    lab = re.sub(r"^controlled load ", "", label.lower())
    if "demand" in lab:  # 'Summer Peak Monthly Demand Rate', 'Year Shoulder ...', 'Off-Peak Year ...' (2015-16)
        band = common.schema.time_band_from_label(label)
        return "demand", "" if "block" in band else band
    if "supply" in lab:
        return "fixed", ""
    m = re.search(r"block (\d)", lab)
    if m:
        return "energy", ("peak_block" if lab.startswith("peak") else "block") + m.group(1)
    if lab.startswith("off-peak"):
        return "energy", "offpeak"
    if lab.startswith("peak"):
        return "energy", "peak"
    if lab.startswith("anytime") or lab == "usage rate":
        return "energy", "anytime"
    raise ValueError(f"component {label!r}")


DB_BLOCK_BANDS = {"block1", "block2", "block3", "peak_block1", "peak_block2"}  # the blocks the database holds


def band_fit(band, notes):
    """A block band the database has no band for (block 4, peak block 3 and 4 of the ladders from 2009-10) is stored
    without one, the component label naming it."""
    if "block" in band and band not in DB_BLOCK_BANDS:
        notes.append(f"{band.replace('_', ' ').replace('block', 'block ')} of the printed ladder: the database has "
                     f"no band for it")
        return ""
    return band


def etsa_list(doc):
    """ETSA Utilities distribution/network tariff lists 2000-01 to 2005-06: per page 'APPLIES TO USAGE FROM <date>',
    a tariff name line (2005: then a code line), then one line per price component with its unit and a row of price
    columns (TUOS, DUOS, totals incl/excl GST, rebates and pass-throughs, 'as billed'). The total network price
    excluding GST is the rightmost 'Total excl GST' column (after any rebate or pass-through), or, where the list
    prints no such column (January and July 2004), the 'As billed on New Billing System (CIS O/V) excl GST' rate."""
    path, out = doc["local_path"], []
    names_seen = {}
    pending = []
    for p in range(1, n_pages(path) + 1):
        ls = lines(path, p)
        top = " ".join(ln.text for ln in ls[:4])
        if "APPLIES TO USAGE FROM" not in top or "INDIVIDUAL LOCATIONAL" in top:
            continue
        start = next(i for i, ln in enumerate(ls) if "APPLIES TO USAGE FROM" in ln.text)
        if start and "Negotiated Services" in ls[start - 1].text:
            continue  # negotiated distribution services (2015-16), not standard control tariffs
        header = [w for ln in ls[start + 1:start + 6] for w in ln.words]
        target, cis = None, False
        for i in range(len(header) - 1):
            if header[i][0] == "Total" and header[i + 1][0] == "excl":
                gst = i + 2 < len(header) and header[i + 2][0] == "GST" and 0 <= header[i + 2][1] - header[i + 1][2] < 6
                target = (header[i][1], header[i + 2 if gst else i + 1][2])
        if target is None and any(w[0] == "(CIS" for w in header):
            i = next(i for i in range(len(header) - 2) if header[i][0] == "(CIS" and header[i + 2][0] == "excl")
            target, cis = (header[i][1], header[i + 3][2]), True
        if target is None:  # a continuation page without the column headings (2011-12): the previous page's columns
            target, cis, meter = columns
        else:
            meter = next(((w[1], w[2]) for w in header if w[0] == "METER"), None)  # metering column (from 2010-11)
        columns = (target, cis, meter)
        obsolete = False
        tariff = code = None
        instance = len(pending) + 1000 * p
        body = [ln for ln in ls[start + 1:] if not re.match(r"^(TUOS|Customer|GST|Units Rate|-?\d+\.\d+%|nil|As billed"
                                                          r"|Network Tariffs v)", ln.text)]
        for ln in body:
            t = ln.text
            if "Alternative Control Metering" in t:
                break  # a separate metering price list (2015-16): reported, not parsed
            if t.startswith("OBSOLETE TARIFFS"):
                obsolete = True
                continue
            mu = ETSA_UNIT.search(t)
            if not mu:
                if re.fullmatch(r"[A-Z0-9]{2,}", t):
                    code = t
                elif re.fullmatch(r"\(Cust[^)]*\)", t) or re.search(r"\d+\.\d{2}", t):
                    continue
                elif t.startswith("TUoS Supply Charge") or re.match(r"min [\d,.]+ ?K(VA|W)\b", t):
                    continue  # the minimum demand printed under a stepped demand tariff's name
                else:
                    tariff, code = re.sub(r"\s+", " ", t).strip(), None
                    tariff = re.sub(r"Rate ?Type", "Rate Type", tariff)
                    # word spaces the glyph lines lose ('SportsgroundsStepped', 'Business- 2 Rate' in 2014-15)
                    tariff = re.sub(r"(?<=[a-z]{2})(?=[A-Z])", " ", re.sub(r"(?<=\w)- (?=\w)", " - ", tariff))
                    instance += 1
                continue
            if "See Individual Locational" in t or re.search(r"\bNMI\b", tariff):
                continue  # per-connection-point (NMI) prices: individually set, not a published tariff
            label = t[:mu.start()].strip()
            if label.startswith("(Cust"):
                label = max((x for x in ETSA_LABELS if subsequence(x.replace(" ", ""), label[5:].replace(" ", ""))),
                            key=len)
            vals = [w for w in ln.words if re.fullmatch(r"-?[\d,]*\.\d+|-?\d[\d,]*", w[0])
                    and target[0] - 2 <= w[2] <= target[1] + 15 and w[1] >= target[0] - 25]
            if not re.search(r"\d\.\d", t[mu.end():]):
                continue  # no price printed (the Supply Rate of a stepped demand tariff)
            if not vals:  # the total column is blank (Subtransmission (KVA) Locational supply rate 2005-06)
                print(f"  left out, no total excl GST printed: {path} p{p} {tariff}: {t!r}")
                continue
            assert len(vals) == 1, (path, p, t, vals)
            value = vals[0][0].replace(",", "")
            if cis:
                unit = ln.words[ln.words.index(vals[0]) - 1][0]
                assert re.fullmatch(r"\$/(day|kWh|k(VA|W)/mth)", unit), (path, p, t)
            else:
                unit = mu.group(1)
            charge, band = etsa_band(label)
            mv = [w for w in ln.words if meter and re.fullmatch(r"\d*\.\d+", w[0])
                  and meter[0] - 2 <= w[2] <= meter[1] + 15 and w[1] >= meter[0] - 25]
            pending.append((instance, tariff, code, label, value, unit, charge, band, p, obsolete, cis,
                            mv[0][0] if mv else None))
    # a name printed twice (the kVA and kW versions of a demand tariff before 2003) gets its demand unit added
    demand_unit, kinds = {}, {}
    for instance, tariff, code, _, _, unit, charge, *_ in pending:
        if charge == "demand":
            demand_unit[instance] = "KVA" if "kVA" in unit else "KW"
            kinds.setdefault(tariff, set()).add(demand_unit[instance])
    for instance, tariff, code, label, value, unit, charge, band, p, obsolete, cis, meter_value in pending:
        notes = ["'As billed on New Billing System (CIS O/V) excl GST' column" if cis else
                 "rightmost 'Total excl GST' column (after any TUoS rebate or pass-through)"]
        tcode = code or tariff
        if not code and len(kinds.get(tariff, ())) > 1:
            tcode = f"{tariff} ({demand_unit[instance]})"
            notes.append(f"'({demand_unit[instance]})' added to the printed name from its demand unit: the list prints "
                         f"the name for both its kVA and kW versions")
        if not code:
            notes.append("no code printed")
        if obsolete:
            notes.append("closed to new customers (printed under 'OBSOLETE TARIFFS')")
        if "Locational" in tariff:
            notes.append("site-specific locational TUoS supply charge not included (individual locational TUoS "
                         "charges)")
        if meter_value is not None:
            notes.append(f"the total includes the METER column ({meter_value}, metering side output)")
            METERING.append({"distributor": common.NAMES[doc["distributor_id"]], "fin_year": doc["pricing_year"], "tariff_code": tcode,
                             "meter_class": tariff, "component": f"METER - {label}", "unit": unit,
                             "value": meter_value, "gst": "excl", "source_file": path, "locator": locators.pdf(p),
                             "note": "'METER excl GST' column of the network tariff list"})
        band = band_fit(band, notes)
        out.append(common.row(doc, tcode, label, value, unit, locators.pdf(p), name=tariff, charge_type=charge,
                              time_band=band, note="; ".join(notes)))
    return out


def compact(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


# the component labels of the scanned schedules, matched on letters and digits only (OCR drops word spaces)
ETSA_OCR_LABELS = {compact(x): x for x in (
    "Supply Rate", "Anytime Usage Rate", "Peak Usage Rate", "Off-Peak Usage Rate", "Annual Demand Rate",
    "Additional Demand", "TUoS Supply Charge", *(f"{k}Block {n} {u}" for n in range(1, 5)
                                                 for k, u in (("", "Usage Rate"), ("Peak ", "Usage Rate"),
                                                              ("Controlled Load ", "Usage Rate"),
                                                              ("Annual ", "Demand Rate"))))}


@functools.lru_cache(maxsize=None)
def etsa_2005_tariffs():
    """{compact name: name} and {code with O read as 0: code} of the 2005-06 text list, to restore the spacing and
    letters OCR loses in the same tariffs' names and codes."""
    rows = etsa_list(common.document(A + "sapn/2005-06/tariffs0506jul2005.pdf"))
    return ({compact(r["tariff_name"]): r["tariff_name"] for r in rows},
            {r["tariff_code"].replace("O", "0"): r["tariff_code"] for r in rows if " " not in r["tariff_code"]})


def ocr_fix(name):
    """Letters OCR misreads in the schedules' tariff names: 'o' for '0' in a number, 'f'/'l' for 't'."""
    s = name.replace("（", "(").replace("）", ")")
    for _ in range(3):
        s = re.sub(r"(?<=[\d,])[oO]|[oO](?=\d)", "0", s)
    for wrong, right in (("Volfage", "Voltage"), ("Vollage", "Voltage"), ("Locafional", "Locational"),
                         ("Localional", "Locational"), ("Gwh", "GWh"), ("(Kw)", "(KW)"), ("NMl", "NMI")):
        s = s.replace(wrong, right)
    return s


def respace(name):
    """A tariff name OCR printed without its word spaces, spaced the way the lists print it."""
    s = re.sub(r"(?<=\d)(?=Rate)|(?<=[a-z])(?=with(?:\b|[A-Z]))|(?<=MW)(?=and)|(?<=GWh)(?=pa)|(?<=[\w)])(?=<)", " ", name)
    s = re.sub(r"(?<=[a-z]{2})(?=[A-Z(])|(?<=\))(?=\()|(?<=[a-z])(?=\d)|(?<=\d)(?=or\b)|(?<=\bor)(?=\d)", " ", s)
    s = re.sub(r"(?<=[a-z])\s*-\s*(?=[A-Z0-9])", " - ", s)
    return re.sub(r"\s+", " ", s).strip()


def etsa_ocr(doc, pages, column):
    """ETSA Utilities network tariff schedules printed as scanned images (2006-07 to 2009-10), read by OCR: the
    ESCOSA-approved schedule enclosed with the 2007-08 approval letter (columns TUOS excl GST, DUOS excl GST, Total
    excl GST, Total incl GST) and the Government Gazette notices (one 'Total incl GST' column). A tariff name line (to
    2007-08 then a code line), then per component its label, unit, minimum quantity and price columns.

    column 'excl': the Total excl GST price, kept only where a second reading agrees (TUOS + DUOS = total, or total x
    1.1 = the printed incl-GST price, both to the printed 6 decimals). column 'incl': the only column printed."""
    path, out = doc["local_path"], []
    names, codes = etsa_2005_tariffs()
    loc_note = "read by OCR"
    pending = []  # (tariff, code, label, value, unit, page, obsolete, checked)
    tariff = code = None  # a tariff continues across a page break
    obsolete = skipping = False
    for p in pages:
        boxes = ocr_boxes(path, p)
        head = {}
        for t, x, y, _ in boxes:
            c = compact(t)
            for key, pat in (("cat", "customercategory"), ("units", "units"), ("minq", "minqty"),
                             ("excl", "totalexcl"), ("incl", "totalinc")):
                if c.startswith(pat) and key not in head:
                    head[key] = (x, y)
        if "cat" not in head:
            continue
        cx, ux, top = head["cat"][0], head["units"][0], head["cat"][1] + 25
        tx = head[column][0]
        # a scan can be skewed (the 2007-08 schedule's rows climb about 20 px across the price columns): level the
        # rows with the median slope between each component label and its unit
        lab = [b for b in boxes if b[2] > top and cx + 120 <= b[1] < ux - 90]
        slopes = sorted((u[2] - lb[2]) / (u[1] - lb[1]) for u in boxes if ux - 90 <= u[1] <= ux + 60 and u[2] > top
                        for lb in lab if abs(u[2] - lb[2]) < 15)
        slope = slopes[len(slopes) // 2] if slopes else 0.0
        boxes = [(t, x, y - slope * (x - cx), conf) for t, x, y, conf in boxes]
        body = sorted((b for b in boxes if b[2] > top), key=lambda b: b[2])
        # price cells: right of the minimum-quantity column (a misread cell such as "669699'6" is kept, then reported)
        nums_ = [b for b in body if re.fullmatch(r"[\d,]*\d\.\d+|[\d,']+\d", b[0]) and b[1] > head["minq"][0] + 150]
        units_ = [b for b in body if ux - 90 <= b[1] <= ux + 60]
        rows, items = [], []
        for b in body:
            if b[1] < cx + 120:
                items.append(("left", b))
            elif cx + 120 <= b[1] < ux - 90 and not re.fullmatch(NUM, b[0]):
                items.append(("label", b))
        # a label OCR splits over two lines ('Controlled Load Block 1 Usage' / 'Rate') is joined
        merged = []
        for kind, b in items:
            if kind == "label" and compact(b[0]) == "rate" and merged and merged[-1][0] == "label":
                prev = merged.pop()[1]
                b = (prev[0] + " " + b[0], prev[1], b[2], min(prev[3], b[3]))
            merged.append((kind, b))
        labels = [b for kind, b in merged if kind == "label"]

        def nearest(box):
            best = min(labels, key=lambda lb: abs(lb[2] - box[2]), default=None)
            return best if best is not None and abs(best[2] - box[2]) <= 24 else None

        cells = {id(lb): [] for lb in labels}
        unit_of = {}
        for b in nums_:
            lb = nearest(b)
            if lb is not None:
                cells[id(lb)].append(b)
        for b in units_:
            lb = nearest(b)
            if lb is not None:
                unit_of.setdefault(id(lb), b[0])
        for kind, b in merged:
            t = b[0]
            c = compact(t)
            if c.startswith("notesaccompanying"):
                break  # the notes after the schedule
            if c.startswith("obsoletetariffs"):
                obsolete, skipping = True, False
                continue
            if c.startswith("individuallocational"):
                skipping = True  # the per-NMI locational TUoS list, up to 'OBSOLETE TARIFFS'
                continue
            if kind == "left":
                if skipping:
                    continue
                elif re.fullmatch(r"[A-Z0-9]{2,12}", t) and not re.fullmatch(r"\d+", t):
                    code = codes.get(t.replace("O", "0"), t)
                elif not re.fullmatch(r"[\d,.]+|SA+\w*", t):
                    raw = re.split(r"\s*-\s*see (?:above|below)", t, flags=re.I)[0]
                    raw = ocr_fix(raw)
                    tariff = names.get(compact(raw)) or respace(raw)
                    code = None
                continue
            if skipping or re.search(r"NM[Ii1l]", tariff):
                continue  # per-connection-point (NMI) prices
            if "See" in t or c.startswith("tuossupplycharge"):
                continue  # 'See Individual Locational TUoS Charges Below'
            key = re.sub(r"block[il|]", "block1", c.replace("rale", "rate").replace("rafe", "rate"))
            key = key.replace("lsage", "usage").replace("dermand", "demand")
            label = ETSA_OCR_LABELS.get(key)
            if label is None:  # a misread letter ('Confrolled'): the closest label with the same digits
                same = [k for k in ETSA_OCR_LABELS if re.sub(r"\D", "", k) == re.sub(r"\D", "", key)]
                close = difflib.get_close_matches(key, same, n=1, cutoff=0.85)
                label = ETSA_OCR_LABELS[close[0]] if close else None
            if label is None:
                raise ValueError(f"{path} p{p}: component {t!r}")
            got = sorted(cells[id(b)], key=lambda v: v[1])
            target = [v for v in got if tx - 15 <= v[1] <= tx + 120]
            if not target:
                if got:
                    raise ValueError(f"{path} p{p}: {tariff} {label}: no {column} value among {got}")
                continue  # no price printed (Supply Rate of the stepped demand tariffs)
            assert len(target) == 1, (path, p, tariff, label, got)
            value = target[0][0].replace(",", "")
            checked = None
            if not re.fullmatch(r"\d+\.\d{6}", value):
                SKIPPED.append((path, p, f"{tariff} {label}: OCR read {target[0][0]!r}"))
                continue
            if column == "excl":
                parts = [v for v in got if v[1] < tx - 15]
                incl = [v for v in got if v[1] > tx + 120]
                ok_sum = parts and all(re.fullmatch(r"[\d,]+\.\d{6}", v[0]) for v in parts) and \
                    abs(sum(float(v[0].replace(",", "")) for v in parts) - float(value)) < 1.5e-6
                ok_gst = incl and re.fullmatch(r"[\d,]+\.\d{6}", incl[0][0]) and \
                    abs(round(float(value) * 1.1, 6) - float(incl[0][0].replace(",", ""))) < 1.5e-6
                if not (ok_sum or ok_gst):
                    SKIPPED.append((path, p, f"{tariff} {label}: OCR read {value} but TUOS + DUOS "
                                    f"{[v[0] for v in parts]} and incl GST {[v[0] for v in incl]} do not agree"))
                    continue
                checked = "TUOS + DUOS = total" if ok_sum else "total x 1.1 = incl GST price"
            unit_raw = unit_of.get(id(b), "")
            if label.endswith("Supply Rate"):
                unit = "$/day"
            elif "Usage" in label:
                unit = "$/kWh"
            elif "kva" in unit_raw.lower() or (not re.search(r"kw/", unit_raw, re.I) and "(KVA)" in tariff):
                unit = "$/kVA/mth"
            else:
                unit = "$/kW/mth"
            pending.append((tariff, code, label, value, unit, p, obsolete, checked))
    for tariff, code, label, value, unit, p, obsolete, checked in pending:
        charge, band = etsa_band(label)
        notes = ["'Total excl GST' column" if column == "excl" else "'Total incl GST' column, the only one printed",
                 loc_note + (f" and checked ({checked})" if checked else "")]
        if not code:
            notes.append("no code printed")
        if obsolete:
            notes.append("closed to new customers (printed under 'OBSOLETE TARIFFS')")
        if "Locational" in tariff:
            notes.append("site-specific locational TUoS supply charge not included (individual locational TUoS "
                         "charges)")
        band = band_fit(band, notes)
        out.append(common.row(doc, code or tariff, label, value, unit, locators.pdf(p, ocr=True), name=tariff,
                              charge_type=charge, time_band=band, gst=column, note="; ".join(notes)))
    return out


SAPN_2013_TEXT = "sapn/2013-14/SA_Power_Networks_-_2013-14_Annual_pricing_proposal_-_Appendix_A_2013-14_NUoS_Tariffs_and_" \
                 "notes_-_revised_24_May_2013.pdf"
PRICE6 = r"\d[\d,]*\.\d{6}"


def ocr_rows(path, p, ytol=12):
    """The OCR boxes of a page as rows (y, [(text, x)] left to right)."""
    rows = []
    for t, x, y, _ in sorted(ocr_boxes(path, p), key=lambda b: b[2]):
        if rows and abs(rows[-1][0] - y) < ytol:
            rows[-1][1].append((t, x))
        else:
            rows.append([y, [(t, x)]])
    return [(y, sorted(bs, key=lambda b: b[1])) for y, bs in rows]


def sapn_2013_ocr(doc, pages):
    """SA Power Networks network tariff list 2013-14 (Network Tariff & Negotiated Services Manual No. 18, pages
    83-86), a scan read by OCR: per tariff a name line, then per component its label, unit, minimum quantity and the
    price columns DUOS, METER (not on the back-up supply page), Total SA-PN, TUOS, PV JSO, Total excl GST, Total incl
    GST. Each page's price columns are found from the x positions of its numbers. The Total excl GST price is kept only
    where a second reading agrees: Total SA-PN + TUOS + PV JSO = total, or total x 1.1 = the incl-GST price, both to
    the printed 6 decimals."""
    path, out = doc["local_path"], []
    held = len(METERING)
    names = {compact(r["tariff_name"]): r["tariff_name"] for r in etsa_list(common.document(A + SAPN_2013_TEXT))}
    del METERING[held:]  # the text copy is read for its tariff names only
    tariff, obsolete, seen = None, False, set()
    for p in pages:
        rows = [r for r in ocr_rows(path, p) if r[0] > 250]
        xs = sorted(x for _, bs in rows for t, x in bs if re.fullmatch(r"[\d,.' ]*\d[\d,.' ]*|locational ?", t) and x > 950)
        cols = [[xs[0]]]
        for x in xs[1:]:
            if x - cols[-1][-1] > 60:
                cols.append([])
            cols[-1].append(x)
        centres = [median(c) for c in cols if len(c) >= 3]
        assert len(centres) in (6, 7), (path, p, centres)
        # component labels sit about 45 px right of the tariff names
        label_x = median(bs[0][1] for _, bs in rows if any(x > 950 and re.search(r"\d\.\d", t) for t, x in bs))
        for y, bs in rows:
            first = compact(bs[0][0])
            if first.startswith(("backupsupplytariffs", "issue", "notesaccompanying")):
                obsolete = False  # the back-up supply tariffs (next page) are not under 'OBSOLETE TARIFFS'
                break
            if first.startswith("obsoletetariffs"):
                obsolete = True
                continue
            if bs[0][1] < label_x - 25:  # a tariff name ('min 80 KVA' and other minimum quantities sit further right)
                raw = ocr_fix(bs[0][0])
                close = difflib.get_close_matches(compact(raw), list(names), n=1, cutoff=0.93)
                tariff, seen = names[close[0]] if close else respace(raw), set()
                continue
            cells = {}
            for t, x in bs:
                if x > 950 and re.fullmatch(r"[\d,.' ]*\d[\d,.' ]*|locational ?", t):
                    cells[min(range(len(centres)), key=lambda i: abs(centres[i] - x))] = t.strip()
            if not cells:
                continue  # no price printed (the Supply Rate of the stepped demand and locational tariffs)
            text = bs[0][0]
            if re.search(r"\d", compact(text)) and not re.search(r"(?i)block|rate", text) or bs[0][1] > 500:
                SKIPPED.append((path, p, f"{tariff}: a row of prices whose component label OCR did not read "
                                         f"({' | '.join(t for t, _ in bs)})"))
                continue
            label_raw = re.split(r"\s*(?:\$|S/|S?K[Ww]h)", text)[0]
            key = compact(label_raw).replace("blcck", "block").replace("blocck", "block").replace("rale", "rate")
            same = [k for k in ETSA_OCR_LABELS if re.sub(r"\D", "", k) == re.sub(r"\D", "", key)]
            close = difflib.get_close_matches(key, same, n=1, cutoff=0.8)
            if not close:
                raise ValueError(f"{path} p{p}: component {text!r}")
            label = ETSA_OCR_LABELS[close[0]]
            if label in seen or tariff is None:  # a component printed twice: OCR lost the next tariff's name
                if tariff is not None:
                    SKIPPED.append((path, p, f"the tariff after {tariff!r}: OCR did not read its name (white on blue); "
                                             f"its prices are left out"))
                tariff = None
                continue
            seen.add(label)
            n = len(centres)
            excl, incl = cells.get(n - 2), cells.get(n - 1)
            if label == "TUoS Supply Charge" or "locational" in (excl or "") + (incl or ""):
                continue  # a site-specific (locational) price: 'locational' printed in the price columns
            if excl is None or not re.fullmatch(PRICE6, excl):
                SKIPPED.append((path, p, f"{tariff} {label}: Total excl GST OCR read {excl!r}"))
                continue
            value = excl.replace(",", "")
            parts = [cells.get(i) for i in (n - 5, n - 4, n - 3)]
            ok_sum = all(v and re.fullmatch(PRICE6, v) for v in parts) and \
                abs(sum(float(v.replace(",", "")) for v in parts) - float(value)) < 1.5e-6
            ok_gst = incl is not None and re.fullmatch(PRICE6, incl) and \
                abs(round(float(value) * 1.1, 6) - float(incl.replace(",", ""))) < 1.5e-6
            if not (ok_sum or ok_gst):
                SKIPPED.append((path, p, f"{tariff} {label}: OCR read {value} but Total SA-PN + TUOS + PV JSO {parts} "
                                         f"and incl GST {incl!r} do not agree"))
                continue
            notes = ["'Total excl GST' column", "read by OCR and checked (" +
                     ("Total SA-PN + TUOS + PV JSO = total" if ok_sum else "total x 1.1 = incl GST price") + ")",
                     "no code printed"]
            if n == 7 and cells.get(1):
                meter = cells[1]
                if re.fullmatch(PRICE6, meter):
                    notes.append(f"the total includes the METER column ({meter}, metering side output)")
                    METERING.append({"distributor": common.NAMES[doc["distributor_id"]],
                                     "fin_year": doc["pricing_year"], "tariff_code": tariff, "meter_class": tariff,
                                     "component": f"METER - {label}", "unit": "$/day", "value": meter, "gst": "excl",
                                     "source_file": path, "locator": locators.pdf(p, ocr=True),
                                     "note": "'METER excl GST' column of the network tariff list; read by OCR"})
                else:
                    notes.append("the total includes the METER column (OCR could not read it)")
                    SKIPPED.append((path, p, f"{tariff} {label}: METER column OCR read {meter!r} (metering side "
                                             f"output)"))
            if obsolete:
                notes.append("closed to new customers (printed under 'OBSOLETE TARIFFS')")
            if "Locational" in tariff:
                notes.append("site-specific locational TUoS supply charge not included (individual locational TUoS "
                             "charges)")
            unit_raw = " ".join(t for t, _ in bs)
            if label.endswith("Supply Rate"):
                unit = "$/day"
            elif "Usage" in label:
                unit = "$/kWh"
            elif re.search(r"(?i)kv?A|kVA", unit_raw) or "(KVA)" in tariff:
                unit = "$/kVA/mth"
            else:
                unit = "$/kW/mth"
            charge, band = etsa_band(label)
            band = band_fit(band, notes)
            out.append(common.row(doc, tariff, label, value, unit, locators.pdf(p, ocr=True), name=tariff,
                                  charge_type=charge, time_band=band, note="; ".join(notes)))
    return out


SCHED_UNIT = re.compile(r"\$/?k?(?:VA|W)?/day|\$/kWh")
SCHED_CODE = re.compile(r"[A-Z][A-Z0-9/+]*\d*[A-Z0-9+]*")
CLASS_HEAD = re.compile(r"^(Residential|Small|Large|Major|High Voltage|HV|Generation|Trial|Unmetered)\b")


def split_units(words):
    """Unit tokens of a unit row; a run the text layer prints without a gap ('$/kVA/day$/kVA/day') is split, its x
    range shared out by character count."""
    out = []
    for t, x0, x1 in words:
        ms = list(SCHED_UNIT.finditer(t))
        if not ms or "".join(m.group(0) for m in ms) != t:
            continue
        step = (x1 - x0) / len(t)
        out += [(m.group(0), x0 + m.start() * step, x0 + m.end() * step) for m in ms]
    return out


def sched_cells(words):
    """Price cells of a schedule row as (value as printed without '$' or ',', x centre): a '$' or '-$' opens a cell
    and the number fragments after it ('0', '.4658') are joined; '$ -' (no price) gives no cell."""
    cells, cur = [], None

    def close():
        if cur and cur[1]:
            cells.append(((cur[0] + cur[1]).replace(",", ""), (cur[2] + cur[3]) / 2))

    for t, x0, x1 in words:
        for m in re.finditer(r"-?\$|[\d,]*\.?\d+|-", t):
            f = m.group(0)
            step = (x1 - x0) / max(len(t), 1)
            fx0, fx1 = x0 + m.start() * step, x0 + m.end() * step
            if f in ("$", "-$"):
                close()
                cur = ["-" if f == "-$" else "", "", None, None]
            elif f == "-":
                close()
                cur = None
            elif cur is not None and not ("." in cur[1] and "." in f):
                cur[1] += f
                cur[2] = fx0 if cur[2] is None else cur[2]
                cur[3] = fx1
            else:
                close()
                cur = ["", f, fx0, fx1]
    close()
    return cells


def sapn_schedule(doc, pages, gst_note, upright_only=True, status_note=""):
    """SA Power Networks NUoS tariff schedules 2017-18 to 2022-23 (pricing proposal appendices and the Tariff Price
    Lists): one row per tariff, its code(s) and name, then '$ <price>' cells under columns that a unit row
    ('$/day $/kWh ... $/kVA/day $/kW/day') anchors. A column's component is its group heading (centred over the columns
    it spans, as are the sub-group headings of the Price Lists) joined with the labels printed over it; the Price
    Lists print one label row for residential tariffs and one for the others."""
    path, out = doc["local_path"], []
    cls = ""
    for p in pages:
        ls = lines(path, p, upright_only=upright_only, spaces=True)
        ui = next(i for i, ln in enumerate(ls) if len(split_units(ln.words)) >= 8)
        units_ = split_units(ls[ui].words)
        centres = [(a + b) / 2 for _, a, b in units_]
        gaps = [b - a for a, b in zip(centres, centres[1:])]
        left_edge = centres[0] - gaps[0] / 2
        cap = next(i for i in range(ui) if re.search(r"Networks'\s*Tariffs", ls[i].text))
        title = next((ln.text for ln in ls[:cap] if ln.text.startswith("Table")), "")
        assert "NUoS" in title or "NUoS" in ls[ui].text, (path, p, title)

        def column(x):
            k = min(range(len(centres)), key=lambda i: abs(centres[i] - x))
            near = min(gaps[max(k - 1, 0)], gaps[min(k, len(gaps) - 1)])
            return k if abs(centres[k] - x) <= 0.6 * near else None

        with pdfplumber.open(os.path.join(common.ROOT, path)) as pdf:
            edges = [(e["x0"], e["top"], e["bottom"]) for e in pdf.pages[p - 1].edges if e["orientation"] == "v"]

        def spans(ln):
            """A heading row: each heading printed over the columns of its merged cell, whose borders are the
            vertical rules crossing the row and the points where the unit changes."""
            y = ln.top + 2
            cuts = sorted({x for x, top, bottom in edges if top <= y <= bottom and centres[0] < x < centres[-1]} |
                          {(centres[k] + centres[k + 1]) / 2 for k in range(len(centres) - 1)
                           if units_[k][0] != units_[k + 1][0]})
            right = [w for w in ln.words if (w[1] + w[2]) / 2 > left_edge and not re.search(r"\d{4}[/–-]\d{2}", w[0])]
            phrases = []
            for w in right:
                if phrases and w[1] - phrases[-1][-1][2] < 4.5:
                    phrases[-1].append(w)
                else:
                    phrases.append([w])
            bounds = [-1e9] + cuts + [1e9]
            got = [""] * len(centres)
            for lo, hi in zip(bounds, bounds[1:]):
                text = " ".join(" ".join(w[0] for w in ph) for ph in phrases if lo < (ph[0][1] + ph[-1][2]) / 2 < hi)
                for k, c in enumerate(centres):
                    if lo < c < hi:
                        got[k] = text
            return got

        heads = [spans(ls[i]) for i in (cap - 1, cap)]
        per_col, res_row, bus_row = [], None, None
        j = ui + 1
        while j < len(ls) and any((w[1] + w[2]) / 2 > left_edge for w in ls[j].words) and "$" not in ls[j].text:
            j += 1
        body_start = j
        for i in list(range(cap + 1, ui)) + list(range(ui + 1, body_start)):
            row = [""] * len(centres)
            for t, x0, x1 in ls[i].words:
                k = column((x0 + x1) / 2) if (x0 + x1) / 2 > left_edge else None
                if k is not None:
                    row[k] = (row[k] + " " + t).strip()
            if "(Residential)" in ls[i].text:
                res_row = row
            elif "(Business)" in ls[i].text:
                bus_row = row
            else:
                per_col.append(row)
        code_heads = sorted((w[1], w[2]) for i in range(ui + 1, body_start) for w in ls[i].words if w[0] == "Code")
        code_x = [(x0 + x1) / 2 for x0, x1 in code_heads]

        def component(k, residential):
            parts = [h[k] for h in heads] + [" ".join(r[k] for r in per_col if r[k])]
            if res_row is not None:
                a, b = res_row[k], bus_row[k]
                parts.append((a or b) if residential else (b or a))
            seen = []
            for x in parts:
                x = re.sub(r"\bOff- (?=Peak)", "Off-", x).strip()
                if x and x not in seen:
                    seen.append(x)
            return " - ".join(seen)

        for ln in ls[body_start:]:
            if "$" not in ln.text:
                t = ln.text.strip()
                if CLASS_HEAD.match(t) and not re.fullmatch(r"\d+", t):
                    cls = t
                continue
            first_cell = next(i for i, w in enumerate(ln.words) if "$" in w[0])
            lead = ln.words[:first_cell]
            codes, used = [], -1
            for w in lead:
                xc = (w[1] + w[2]) / 2
                # each code sits under its own 'Code' heading, left to right (a site-specific tariff may
                # print only a CBD code), left-aligned so it starts at or before the centred heading; a
                # short first name word ('HV Business ...') lying near a code heading starts well after it
                if code_x:
                    col = min(range(len(code_x)), key=lambda j: abs(xc - code_x[j]))
                    is_code_col = abs(xc - code_x[col]) < 22 and col > used and w[1] <= code_heads[col][0] + 5
                else:
                    col, is_code_col = 0, not codes
                if is_code_col and SCHED_CODE.fullmatch(w[0]):
                    codes.append(w[0])
                    used = col
                else:
                    break
            if not codes:
                raise ValueError(f"{path} p{p}: no tariff code in {ln.text!r}")
            name = " ".join(w[0] for w in lead[len(codes):])
            # the SA and CBD code columns print the same code when a tariff has no CBD variant
            codes = list(dict.fromkeys(codes))
            residential = cls.startswith("Residential")
            for value, xc in sched_cells(ln.words[first_cell:]):
                k = column(xc)
                if k is None:
                    raise ValueError(f"{path} p{p}: {codes[0]} value {value} at x={xc:.0f} under no column")
                comp = component(k, residential)
                unit = units_[k][0]
                charge = "fixed" if unit == "$/day" else "energy" if unit == "$/kWh" else "demand"
                band = schema_band(comp, charge)
                for code in codes:
                    notes = [f"{title or ls[cap].text.split(' Supply')[0]} (p{p})", gst_note]
                    if code != codes[0]:
                        notes.append(f"CBD variant of {codes[0]}, printed on the same schedule row")
                    if "REBATE" in comp.upper():
                        notes.append("supply rebate (negative $/day)")
                    if status_note:
                        notes.append(status_note)
                    out.append(common.row(doc, code, comp, value, unit, locators.pdf(p), name=name,
                                          customer_class=cls, charge_type=charge, time_band=band,
                                          note="; ".join(notes)))
    return out


SAPN_2016_COLUMNS = (
    "Supply - Supply Rate", "Energy based usage - Usage Block 1", "Energy based usage - Usage Block 2",
    "Energy based usage - Usage Peak", "Energy based usage - Usage Off-Peak", "Energy based usage - Controlled Load",
    "Annual agreed kVA demand - Block 1 Annual", "Annual agreed kVA demand - Block 2 Annual",
    "Annual agreed kVA demand - Additional Annual", "Monthly actual kVA demand - Summer Peak 5 months",
    "Monthly actual kVA demand - Year Shoulder 12 months", "Monthly actual kVA demand - Year Off-Peak 12 months",
    "Monthly actual kW demand - Summer Peak 5 months", "Monthly actual kW demand - Winter Shoulder 7 months",
    "Monthly actual kW demand - Year Off-Peak 12 months")
SAPN_2016_UNITS = ("$/day",) + ("$/kWh",) * 5 + ("$/kVA/day",) * 6 + ("$/kW/day",) * 3


OCR_2016_CODES = {"NIS": "STN"}  # OCR reads the scan's 'STN' (Sub Transmission ... non-locational, p1-p5) as 'NIS'


def sapn_2016_table(path, p):
    """One schedule page of the scanned 2016-17 list, read by OCR: (title, {code: (name, class, {column: value},
    {column: unreadable text})}). A cell is '$ <price>'; a lone '$' (OCR also reads it 'S', '5' or '9') is skipped."""
    ls = ocr_lines(path, p)
    ui = next(i for i, ln in enumerate(ls) if sum("/" in w[0] for w in ln.words if w[1] > 1400) >= 12)  # unit row
    unit_boxes = [w for w in ls[ui].words if w[1] > 1400]
    assert len(unit_boxes) == 15, (path, p, ls[ui].text)
    head = compact(" ".join(ln.text for ln in ls[:ui]))
    for must in ("energybasedusage", "annualagreedkvademand", "monthlyactualkvademand", "monthlyactualkwdemand"):
        assert must in head, (path, p, must)
    centres = [(w[1] + w[2]) / 2 for w in unit_boxes]
    title = next(ln.text for ln in ls[:ui] if re.search(r"Final .*(Schedule|Prices)", ln.text))
    title = re.sub(r"\s*\|.*", "", re.sub(r"(Schedule|Prices)\b.*", r"\1", title))
    tables, cls = {}, ""
    for ln in ls[ui + 1:]:
        if ln.text.startswith("SA Power Networks Pricing Proposal"):
            break
        lead = [w for w in ln.words if w[1] < 1450]
        cells = [w for w in ln.words if w[1] >= 1450]
        if ln.text.startswith("Tariff Class"):
            continue
        if not cells:
            if lead and CLASS_HEAD.match(lead[0][0]):
                cls = lead[0][0]
            continue
        if not lead or not SCHED_CODE.fullmatch(lead[0][0]):
            SKIPPED.append((path, p, f"a row of prices without a tariff code OCR could read ({ln.text})"))
            continue
        code, name = OCR_2016_CODES.get(lead[0][0], lead[0][0]), " ".join(w[0] for w in lead[1:])
        got, bad = {}, {}
        for t, x0, x1 in cells:
            t = t.strip()
            if len(t) <= 1:
                continue  # a lone '$' (read '$', 'S', '5' or '9')
            m = re.fullmatch(r"[$S]?\s?([\d,]+\.\d{2,4})\s?[$S]?", t)
            k = min(range(15), key=lambda i: abs(centres[i] - (x0 + x1) / 2))
            if m is None:
                bad[k] = t
                continue
            xc = x0 + (x1 - x0) * (m.start(1) + m.end(1)) / 2 / len(t)
            k = min(range(15), key=lambda i: abs(centres[i] - xc))
            assert abs(centres[k] - xc) < 120, (path, p, code, t)
            got[k] = m.group(1)
        tables[code] = (name, cls, got, bad)
    return title, tables


def sapn_2016_ocr(doc):
    """SA Power Networks 'NUoS Tariffs and explanatory notes' 2016-17, a scan read by OCR: the NUoS schedule (p1)
    with the DUoS (p2), TUoS (p3), JSO PV FiT (p4) and negotiated service (p5) schedules it sums. A NUoS price is kept
    only where the parts OCR read on the same row and column add up to it (to the printed 4 decimals, within
    rounding)."""
    path, out = doc["local_path"], []
    title, nuos = sapn_2016_table(path, 1)
    parts = [sapn_2016_table(path, p)[1] for p in (2, 3, 4, 5)]
    for code, (name, cls, got, bad) in nuos.items():
        for k, text in bad.items():
            SKIPPED.append((path, 1, f"{code} {SAPN_2016_COLUMNS[k]}: OCR read {text!r}"))
        for k, value in got.items():
            rows = [t.get(code) for t in parts[:3]]
            if code in parts[3]:  # the distribution element charged as a negotiated service (p5)
                rows.append(parts[3][code])
            why = None
            if any(r is None for r in rows):
                why = "no DUoS/TUoS/JSO row read for it"
            elif any(k in r[3] for r in rows):
                why = "a DUoS/TUoS/JSO part is unreadable"
            else:
                total = sum(float(r[2].get(k, "0").replace(",", "")) for r in rows)
                if abs(total - float(value.replace(",", ""))) > (0.00016 if len(value.split(".")[1]) == 4 else 0.0051):
                    why = f"DUoS + TUoS + JSO = {total:.4f}"
            if why:
                SKIPPED.append((path, 1, f"{code} {SAPN_2016_COLUMNS[k]}: OCR read {value} but {why}"))
                continue
            unit = SAPN_2016_UNITS[k]
            charge = "fixed" if unit == "$/day" else "energy" if unit == "$/kWh" else "demand"
            comp = SAPN_2016_COLUMNS[k]
            out.append(common.row(doc, code, comp, value.replace(",", ""), unit, locators.pdf(1, ocr=True), name=name,
                                  customer_class=cls, charge_type=charge, time_band=schema_band(comp, charge),
                                  note=f"{title} (p1), 'excludes GST, Metering'; read by OCR and checked (DUoS + TUoS "
                                       f"+ JSO (PV FiT) + negotiated service, p2-p5 = NUoS)"))
    return out


def schema_band(component, charge):
    """The time band of a schedule column: its last label first ('Non-TOU', 'Peak Year', 'Block 1 Annual'), a
    demand block (agreed-demand steps) without a band."""
    for part in reversed(component.split(" - ")):
        band = common.schema.time_band_from_label(part)
        if band:
            break
    if not band and "Controlled" in component:
        band = "anytime"
    if charge == "demand" and "block" in band:
        return ""
    return band


# ---------------------------------------------------------------------------------------------------- documents
A = "sources/archive/"
GST_PP = "p{}: 'All values shown in the proposal are in nominal dollars and exclude goods and services tax (GST), unless " \
         "otherwise stated'"
DOCUMENTS = [
    (A + "powerwater/1999-00/approv_netwk_tariffs_apr-jne_2000.pdf", pwc_1999),
    (A + "powerwater/2000-01/appvl_netwk_tariffs_2000-01.pdf", pwc_uc_schedule),
    (A + "powerwater/2001-02/netwk_tariff_approv__instrum_may_2001.pdf", pwc_uc_schedule),
    (A + "powerwater/2002-03/approv_intrum_netwk_tariff_may_2002.pdf", pwc_uc_schedule),
    (A + "powerwater/2003-04/approv_instrum_net_access_tariff_0304.pdf", pwc_uc_schedule),
    (A + "powerwater/2004-05/approv_instrump_instrum_netwk_access_tariff_2004-05.pdf", pwc_uc_schedule),
    (A + "powerwater/2005-06/network_tariff_approval_instrum_with_schedules_may_2005_at.pdf", pwc_uc_schedule),
    (A + "powerwater/2006-07/network-tariff-approval-instrum-May-_2006.pdf", pwc_uc_schedule),
    (A + "powerwater/2007-08/network_tariff_approval_instrum__May__2007.pdf", pwc_uc_schedule),
    (A + "powerwater/2008-09/network_tariff_approval_instrum__May__2008.pdf", pwc_uc_schedule),
    (A + "powerwater/2009-10/network_tariff_approval_instrum_May2009.pdf", pwc_uc_schedule),
    (A + "powerwater/2010-11/2010-11_network_tariff_schedules_final.pdf", pwc_uc_schedule),
    (A + "powerwater/2011-12/2011_12_network_tariff_approval_instrum_May2011.pdf", pwc_uc_schedule),
    (A + "powerwater/2012-13/network-tariff-approval-instrum-May-2012.pdf", pwc_uc_schedule),
    (A + "powerwater/2013-14/Network-tariff-approval-instrum-May-2013.pdf", pwc_uc_schedule),
    (A + "powerwater/2014-15/2014-15_electricity_network_tariffs_chargesfuture_price_trends.pdf",
     lambda d: pwc_all_regions(d, (19, 20))),
    (A + "powerwater/2015-16/PWC_Power_Networks_2015-16_electricity_network_tariffs_and_charges_and_future_price_trends.pdf",
     lambda d: pwc_all_regions(d, (17, 18, 19), ocr=True)),
    (A + "powerwater/2016-17/Power_and_Water_Corporation_Power_Networks_2016-17_Electricity_Network_Tariffs_and_Charges.pdf",
     lambda d: pwc_all_regions(d, (19, 20, 21))),
    (A + "powerwater/2017-18/2017-18_Network_Tariffs_and_Charges_-_Ministerial_Direction.pdf",
     lambda d: pwc_all_regions(d, (17, 18))),
    (A + "powerwater/2018-19/Power_and_Water_Corporation_Network_Tariff_Charges_-_2018-19_V2.pdf",
     lambda d: pwc_all_regions(d, (17, 18))),
    (A + "powerwater/2019-20/PWC_-_AER_approved_Power_Services_Network_Pricing_Proposal_2019-20_-_21_May_2019_-_Public_-_June_2019"
         ".pdf",
     lambda d: pwc_charging_parameter(d, 39, r"^Table 12: 2019-20 tariffs by charging parameter \(excluding GST\)",
                                      "table title '(excluding GST)'")),
    (A + "powerwater/2020-21/Power_and_Water_Corporation_-_Network_Pricing_Proposal_2020-21_-_31_March_2020_-_Public_0.pdf",
     lambda d: pwc_charging_parameter(d, 18, r"^Table 8: 2020-21 Price list for SCS", GST_PP.format(7))),
    (A + "powerwater/2021-22/Power_and_Water_Corporation_-_Network_Pricing_Proposal_2021-22_-_31_March_2021_-_UPDATED_0.pdf",
     lambda d: pwc_charging_parameter(d, 20, r"^Table 8: 2021-22 Price list for SCS", GST_PP.format(8))),
    (A + "powerwater/2022-23/Power_and_Water_Corporation_-_Network_Pricing_Proposal_2022-23_-_updated_29_April_2022.pdf",
     lambda d: pwc_charging_parameter(d, 20, r"^Table 8: 2022-23 Price list for SCS", GST_PP.format(8))),
    (A + "sapn/2000-01/tariff0102july2000.pdf", etsa_list),
    (A + "sapn/2000-01/tariff0102april2001.pdf", etsa_list),
    (A + "sapn/2001-02/tariff0102july2001.pdf", etsa_list),
    (A + "sapn/2001-02/tariff0102feb2002.pdf", etsa_list),
    (A + "sapn/2002-03/tariff0203jul2002.pdf", etsa_list),
    (A + "sapn/2002-03/tariff0203aug2002.pdf", etsa_list),
    (A + "sapn/2002-03/tariff0203jan2003.pdf", etsa_list),
    (A + "sapn/2003-04/tariff0304july2003.pdf", etsa_list),
    (A + "sapn/2003-04/tariff0304jan2004.pdf", etsa_list),
    (A + "sapn/2004-05/tariffs0405jul2004.pdf", etsa_list),
    (A + "sapn/2005-06/tariffs0506jul2005.pdf", etsa_list),
    (A + "sapn/2006-07/2006_035.pdf", lambda d: etsa_ocr(d, range(5, 13), "incl")),
    (A + "sapn/2007-08/070525-L-AnnualETSADistTariffApproval.pdf", lambda d: etsa_ocr(d, range(3, 11), "excl")),
    (A + "sapn/2009-10/2009_040.pdf", lambda d: etsa_ocr(d, range(9, 14), "incl")),
    (A + "sapn/2010-11/ETSA_Tariffs_1_August_2010_id11938.pdf", etsa_list),
    (A + "sapn/2011-12/ETSA_Tariffs_1_July_2011_id20991.pdf", etsa_list),
    (A + "sapn/2012-13/ETSA_Tariffs_1_July_2012_id20992.pdf", etsa_list),
    (A + "sapn/2013-14/SAPN_Tariffs_1_October_2013_id27658_20140110.pdf", lambda d: sapn_2013_ocr(d, (1, 3, 4))),
    (A + "sapn/2014-15/SAPN_Tariffs_1_July_2014_id46616_20150312.pdf", etsa_list),
    (A + "sapn/2015-16/SAPN_Tariffs_1_July_2015_id50876_20160303.pdf", etsa_list),
    (A + "sapn/2016-17/SAPN_Tariffs_1_July_2016_id55342_20170224.pdf", sapn_2016_ocr),
    (A + "sapn/2017-18/AER_approved_-_SA_Power_Networks_2017-18_Annual_Pricing_Proposal_-_12_May_2017.pdf",
     lambda d: sapn_schedule(d, (55,), "'excludes GST, Metering' (table heading)",
                             status_note="AER-approved pricing proposal, Appendix A")),
    (A + "sapn/2018-19/SA_Power_Networks_Pricing_Proposal_2018_v2.2F.pdf",
     lambda d: sapn_schedule(d, (52,), "'excludes GST, Metering' (table heading)",
                             status_note="AER-approved pricing proposal, Appendix A")),
    (A + "sapn/2019-20/AER_APPROVED_-_SA_Power_Networks_Pricing_Proposal_2019-20_-_May_2019.pdf",
     lambda d: sapn_schedule(d, (45,), "'excludes GST, Metering' (table heading)",
                             status_note="AER-approved pricing proposal, Appendix A")),
    (A + "sapn/2020-21/SAPN_Tariff_Price_List_2020_21_v5_id315323_20220119.pdf",
     lambda d: sapn_schedule(d, (5, 6), "GST basis not stated for the tariff schedule; taken as excl (the list's "
                                        "negotiated feeder charges 'exclude GST', p14)")),
    (A + "sapn/2021-22/SAPN_Tariff_Price_List_2021_22_id315323_20221221.pdf",
     lambda d: sapn_schedule(d, (4, 5), "p3: 'All prices listed are exclusive of GST'")),
    (A + "sapn/2022-23/SA_Power_Networks_-_Annual_Pricing_Proposal_-_updated_12_April_2022.pdf",
     lambda d: sapn_schedule(d, (59, 60), "GST basis not stated on the tariff schedule; taken as excl (the "
                                          "proposal's bill tables are 'excl. GST')", upright_only=False,
                             status_note="proposed prices (pricing proposal updated 12 April 2022, Appendix B)")),
]


def main():
    only = sys.argv[1:]
    rows = []
    for path, fn in DOCUMENTS:
        if only and not any(o in path for o in only):
            continue
        got = fn(common.document(path))
        if not got:
            raise SystemExit(f"{path}: no prices read")
        print(f"  {len(got):4} {path}")
        rows += got
    for s in SKIPPED:
        print("  left out, unreadable:", *s)
    common.write(SLUG, rows)
    if not only:
        common.schema.write_metering(f"history_{SLUG}", METERING)


if __name__ == "__main__":
    main()
