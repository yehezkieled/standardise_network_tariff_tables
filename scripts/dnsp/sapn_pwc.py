"""Parse SA Power Networks (SA) and Power and Water Corporation (NT) network tariff PDFs.

Writes out/dnsp/sapn_pwc.csv with exactly schema.COLUMNS (see scripts/dnsp/CONTRACT.md).

SAPN documents publish wide tariff schedules (one row per tariff code, one column per price
component) in four price bases (NUoS, DUoS, TUoS, JSO).  The PDFs are Excel exports whose text
layer is unreliable for pdfplumber's default word extraction (many glyph runs are flagged
non-upright and numbers are split into fragments such as "$ 0 .3300"), so this parser works
directly from character coordinates: lines are rebuilt from `page.chars`, columns are located from
the unit row ("$/day $/kWh ... $/kVA/day"), header labels are attached to columns by x-position and
every price is mapped to the nearest column centre.

PWC documents are small "tariff by charging parameter" tables; they are parsed with label-based
column detection (header phrases clustered by x) or, where the table uses "-" placeholders, by
token index after verifying the header text.

Run:  .venv/bin/python scripts/dnsp/sapn_pwc.py [--debug]
"""
from __future__ import annotations

import csv
import os
import re
import sys
from collections import defaultdict
from statistics import median

import pdfplumber

sys.path.insert(0, "scripts")
import schema  # noqa: E402
import units  # noqa: E402
from tariffdb import locators  # noqa: E402

ROOT = os.getcwd()
OUT_PATH = os.path.join("out", "dnsp", "sapn_pwc.csv")
DEBUG = "--debug" in sys.argv

SAPN = "SA Power Networks"
METERING = []  # per-tariff metering cells (schema.METERING_COLUMNS), written to the metering side output
PWC = "Power and Water Corporation"

# --------------------------------------------------------------------------------------------
# File inventory
# --------------------------------------------------------------------------------------------

FILES = [
    # (distributor, fin_year, side, repo path, parser name, extra)
    (SAPN, "2023-24", "DNSP", "sources/dnsp/sapn/SAPN_Tariff_Price_List_2023-24_id320662.pdf", "sapn",
     dict(gst_note="document p3: 'All prices listed are exclusive of GST'")),
    (SAPN, "2023-24", "AER_HOSTED", "sources/aer/2023-24_price_lists/SAPN_2023-24_Annual_Pricing_Proposal_Revised_8May2023.pdf", "sapn",
     dict(doc_note="proposed (pre-approval) prices; Annual Pricing Proposal (revised 8 May 2023) Appendix B",
          gst_note="GST basis not stated on the tariff schedule; assumed excl (document bill tables are 'Excluding GST')")),
    (SAPN, "2024-25", "DNSP", "sources/dnsp/sapn/SAPN_Tariff_Price_List_2024-25_id328119.pdf", "sapn",
     dict(gst_note="document p3: 'All prices listed are exclusive of GST'")),
    (SAPN, "2024-25", "AER_HOSTED", "sources/aer/dnsp_copies/SAPN_2024-25_Annual_Pricing_Proposal_19Apr2024.pdf", "sapn",
     dict(doc_note="proposed (pre-approval) prices; Annual Pricing Proposal (19 Apr 2024) Appendix B",
          gst_note="GST basis not stated on the tariff schedule; assumed excl (document bill tables are 'Excluding GST')")),
    (SAPN, "2025-26", "DNSP", "sources/dnsp/sapn/SAPN_Initial_Pricing_Proposal_Overview_2025-26_id333252.pdf", "sapn",
     dict(doc_note="pricing proposal overview, not the price list (Appendix A SCS tariff schedules)",
          gst_note="GST basis not stated on the tariff schedule; assumed excl")),
    (SAPN, "2025-26", "AER_HOSTED", "sources/aer/dnsp_copies/SAPN_2025-26_Pricing_Proposal_Overview_14May2025.pdf", "sapn",
     dict(doc_note="proposed (pre-approval) prices; Pricing Proposal Overview (14 May 2025) Appendix A",
          gst_note="GST basis not stated on the tariff schedule; assumed excl")),
    (SAPN, "2026-27", "DNSP", "sources/dnsp/sapn/SAPN_Tariff_Price_List_2026-27_id338674.pdf", "sapn",
     dict(gst_note="document p3: 'All prices listed are exclusive of GST'")),
    (PWC, "2023-24", "AER_HOSTED", "sources/aer/2023-24_price_lists/PWC_2023-24_Network_Pricing_Proposal_30Mar2023.pdf", "pwc_indicative",
     dict(table="Table B.1", doc_note="proposed (pre-approval) prices; Table B.1 'Indicative price schedule for SCS (nominal $, excluding GST)'",
          demand_period_note="unit printed as $/kVA; p12: 'The demand charge is applied to the maximum demand value, within the "
                             "defined peak period each month'; value_std expressed per month")),
    (PWC, "2023-24", "DNSP", "sources/dnsp/powerwater/PWC_SCS_Tariffs_2023-24_wayback.pdf", "pwc_onepager",
     dict(demand_period_note="unit printed as $/kVA; page footer header fragment reads 'Demand* $/kVA/month'; value_std expressed per month")),
    (PWC, "2024-25", "AER_HOSTED", "sources/aer/dnsp_copies/PWC_2024-25_Pricing_Proposal_8May2024.pdf", "pwc_indicative",
     dict(table="Table A.1", doc_note="proposed (pre-approval) prices; Table A.1 'Indicative price schedule for SCS (nominal $, excluding GST)'",
          demand_period_note="unit printed as $/kVA; p17: 'Consumer charged for the highest recorded demand during the peak period "
                             "(regardless of season) each month', p24 header '($/kVA/Month)'; value_std expressed per month")),
    (PWC, "2024-25", "DNSP", "sources/dnsp/powerwater/PWC_SCS_Tariffs_2024-25_wayback.pdf", "pwc_onepager",
     dict(demand_period_note="unit printed as $/kVA; p2: 'Consumer are charged for the highest recorded demand during the peak period "
                             "(regardless of season) each month'; value_std expressed per month",
          tou_unit_note="unit not printed for this column; $/kWh per the 'Anytime Energy Charge' column header and "
                        "Table A.1 of the 2024-25 Pricing Proposal")),
    (PWC, "2025-26", "AER_HOSTED", "sources/aer/dnsp_copies/PWC_2025-26_Network_Pricing_Proposal_23May2025.pdf", "pwc_7col",
     dict(title_re=r"^Table 9 - 2025-26 price list for SCS",
          doc_note="proposed (pre-approval) prices; Table 9 '2025-26 price list for SCS - tariffs by charging parameter (nominal $)'",
          gst_note="p8: 'All values shown in the proposal are in nominal dollars and exclude GST, unless otherwise stated'")),
    (PWC, "2025-26", "DNSP", "sources/dnsp/powerwater/PWC_SCS_Tariffs_2025-26_210525_wayback.pdf", "pwc_7col",
     dict(title_re=r"^2025-26 SCS tariffs by charging parameter \(excluding GST\)",
          gst_note="table title '(excluding GST)'",
          unit_note="unit not printed in this document; taken from the 2025-26 Network Pricing Proposal Table 9 header (identical values)")),
    (PWC, "2026-27", "AER_HOSTED", "sources/aer/dnsp_copies/PWC_2026-27_Network_Pricing_Proposal_31Mar2026.pdf", "pwc_7col",
     dict(title_re=r"^Table 4\.3 - 2026-27 proposed price list for SCS",
          doc_note="proposed (pre-approval) prices; Table 4.3 '2026-27 proposed price list for SCS - tariffs by charging parameter (nominal $)'",
          gst_note="p9: 'All values shown in the proposal are in nominal dollars and exclude GST, unless otherwise stated'")),
]

PWC_BASIS_NOTE = ("basis 'unknown': document does not state whether prices are NUoS or DUoS and publishes "
                  "no DUoS/TUoS split (prices titled 'Standard Control Service network tariffs' / "
                  "'SCS tariffs by charging parameter')")


def load_inventory() -> dict:
    urls = {}
    with open(os.path.join("sources", "inventory.csv"), newline="", encoding="utf-8") as fh:
        for rec in csv.DictReader(fh):
            urls[rec["local_path"]] = rec["source_url"]
    return urls


# --------------------------------------------------------------------------------------------
# Character-level line / word helpers (independent of pdfplumber's upright flag)
# --------------------------------------------------------------------------------------------

class Word:
    __slots__ = ("text", "x0", "x1", "top", "bold")

    def __init__(self, chars):
        self.text = "".join(c["text"] for c in chars)
        self.x0 = chars[0]["x0"]
        self.x1 = chars[-1]["x1"]
        self.top = chars[0]["top"]
        self.bold = sum("Bold" in c["fontname"] for c in chars) * 2 > len(chars)

    @property
    def xc(self):
        return (self.x0 + self.x1) / 2.0

    def __repr__(self):
        return f"{self.text}[{self.x0:.0f}-{self.x1:.0f}]"


class Line:
    def __init__(self, chars):
        self.chars = sorted(chars, key=lambda c: c["x0"])
        self.top = chars[0]["top"]
        self.size = median(c["size"] for c in self.chars)
        self.words = self._split_words()
        self.text = " ".join(w.text for w in self.words)
        letters = [c for c in self.chars if c["text"].strip()]
        self.bold = bool(letters) and sum("Bold" in c["fontname"] for c in letters) * 2 > len(letters)

    def _split_words(self):
        """Split on explicit space characters and on gaps wider than half a typical glyph."""
        cs = self.chars
        widths = [c["x1"] - c["x0"] for c in cs if c["text"].strip()]
        w = median(widths) if widths else 3.0
        out, cur, prev = [], [], None
        for c in cs:
            if c["text"] == " " or (prev is not None and c["x0"] - prev["x1"] > 0.5 * w):
                if cur:
                    out.append(Word(cur))
                    cur = []
                if c["text"] == " ":
                    prev = c
                    continue
            cur.append(c)
            prev = c
        if cur:
            out.append(Word(cur))
        return [wd for wd in out if wd.text.strip()]

    def phrases(self, words=None, gap_factor=0.7):
        """Group words into phrases: a new phrase starts when the gap exceeds gap_factor*font size."""
        words = self.words if words is None else words
        out, cur = [], []
        for wd in words:
            if cur and wd.x0 - cur[-1].x1 > gap_factor * self.size:
                out.append(cur)
                cur = []
            cur.append(wd)
        if cur:
            out.append(cur)
        return [Phrase(p) for p in out]


class Phrase:
    def __init__(self, words):
        self.words = words
        self.text = " ".join(w.text for w in words)
        self.x0 = words[0].x0
        self.x1 = words[-1].x1
        self.top = words[0].top

    @property
    def xc(self):
        return (self.x0 + self.x1) / 2.0

    def __repr__(self):
        return f"{self.text}@{self.xc:.0f}"


def build_lines(page, ytol=1.5):
    chars = sorted(page.chars, key=lambda c: (round(c["top"]), c["x0"]))
    groups = []
    for c in chars:
        # anchor a line on its first visible glyph: stray spaces between rows (e.g. 1.5pt above the last row of a
        # page) would otherwise start a line that the row's text joins and its values, 0.35pt lower, miss
        anchor = next((x for x in groups[-1] if x["text"].strip()), groups[-1][0]) if groups else None
        if anchor is not None and abs(anchor["top"] - c["top"]) <= ytol:
            groups[-1].append(c)
        else:
            groups.append([c])
    lines = [Line(g) for g in groups]
    return [ln for ln in lines if ln.words]


def nearest(centres, x):
    """Index of the nearest centre and the distance."""
    i = min(range(len(centres)), key=lambda k: abs(centres[k] - x))
    return i, abs(centres[i] - x)


FRAG_RE = re.compile(r"-\$|\$|\d[\d,]*\.?\d*|\.\d+|-")


def money_values(words):
    """Reconstruct numeric cell values from fragmented tokens such as '$', '0', '.3300' or '-$'.

    Returns a list of (value: float, x0, x1).  A bare '-' (placeholder) is returned as value None.
    """
    vals = []
    cur = None  # [sign, text, x0, x1]

    def close():
        nonlocal cur
        if cur and cur[1]:
            txt = cur[1].replace(",", "")
            try:
                float(txt)
                v = ("-" if cur[0] < 0 else "") + txt
            except ValueError:
                v = None
            vals.append((v, cur[2], cur[3]))
        cur = None

    for wd in words:
        pos = wd.x0
        n = max(len(wd.text), 1)
        step = (wd.x1 - wd.x0) / n
        for m in FRAG_RE.finditer(wd.text):
            frag = m.group(0)
            fx0 = pos + m.start() * step
            fx1 = pos + m.end() * step
            if frag in ("$", "-$"):
                close()
                cur = [-1.0 if frag == "-$" else 1.0, "", None, None]
            elif frag == "-":
                close()
                vals.append((None, fx0, fx1))
            else:
                if cur is None:
                    cur = [1.0, "", None, None]
                if cur[1] and "." in cur[1] and "." in frag:
                    # two decimal points cannot belong to one number: start a new value
                    close()
                    cur = [1.0, "", None, None]
                cur[1] += frag
                cur[2] = fx0 if cur[2] is None else cur[2]
                cur[3] = fx1
    close()
    return vals


# --------------------------------------------------------------------------------------------
# Row construction
# --------------------------------------------------------------------------------------------

def make_row(distributor, fin_year, side, code, name, cls, component, unit, value, gst, basis,
             source_file, source_url, note, charge_type=None, time_band=None, season=None,
             std_unit=None, locator=""):
    vstd, ustd = units.to_std(value, std_unit or unit, component)
    return {
        "side": side,
        "distributor": distributor,
        "fin_year": fin_year,
        "tariff_code": code.strip(),
        "tariff_name": name.strip(),
        "customer_class": cls.strip(),
        "component": component.strip(),
        "charge_type": charge_type or schema.charge_type_from_label(component, unit),
        "time_band": time_band if time_band is not None else schema.time_band_from_label(component),
        "season": season if season is not None else schema.season_from_label(component),
        "unit": unit,
        "value": repr(float(value)) if isinstance(value, float) else str(value),
        "value_std": "" if vstd is None else repr(vstd),
        "unit_std": ustd,
        "gst": gst,
        "basis": basis,
        "source_file": source_file,
        "source_url": source_url,
        "note": "; ".join(p for p in note if p),
        "locator": locator,
    }


# --------------------------------------------------------------------------------------------
# SA Power Networks tariff schedules
# --------------------------------------------------------------------------------------------

SAPN_UNITS = {"$/day", "$/kWh", "$/kVA/day", "$kW/day", "$/kW/day"}
SAPN_TITLE_RE = re.compile(r"Table\s+\d+\s*:\s*(NUoS|DUoS|TUoS|JSO|Metering)\s+(Tariff Schedule|Charges|Tariff)\s*(\S+)?", re.I)
ANY_TABLE_RE = re.compile(r"^Table\s+\d+\s*[:\-]")
SAPN_BASIS = {"NUOS": "NUoS", "DUOS": "DUoS", "TUOS": "TUoS", "JSO": "JSA"}
ELIG_RE = re.compile(r"Refer to [\d.]+|for eligib\w*", re.I)


class SapnHeader:
    """Column model for one SAPN schedule page.

    Columns are anchored on the unit row.  Group / sub-group / label texts are derived from the
    header rows of the table's first page; continuation pages re-use that model (`template`) because
    their repeated header rows can carry overlapping text in the PDF, while their unit row, label
    rows and code columns are still used for page-local geometry.
    """

    def __init__(self, lines, unit_idx, template=None):
        self.unit_idx = unit_idx
        uline = lines[unit_idx]
        uwords = [w for w in uline.words if w.text in SAPN_UNITS]
        self.centres = [w.xc for w in uwords]
        self.units = [w.text for w in uwords]
        n = len(self.centres)
        self.pitch = (self.centres[-1] - self.centres[0]) / (n - 1)
        self.left_edge = self.centres[0] - (self.centres[1] - self.centres[0]) / 2.0
        self.groups = [""] * n
        self.runs = [0] * n          # contiguous group run id per column
        self.subs = [""] * n
        self.labels_res = [""] * n   # label row used for residential tariffs (dual-row layouts)
        self.labels_bus = [""] * n   # label row used for all other tariffs
        self.elig = [""] * n         # eligibility remark found in a label cell ("Refer to 2.3.7 ...")
        self.dual = False
        self.code_cols = []          # [(centre, role)]
        self.name_header_x0 = None
        self.body_start = None       # index of first body line
        self.from_template = False
        self._parse(lines, template)

    # -- header rows above the unit row ------------------------------------------------------
    def _parse(self, lines, template):
        if template is not None and template.units == self.units:
            for attr in ("groups", "runs", "subs", "labels_res", "labels_bus", "elig", "dual"):
                setattr(self, attr, getattr(template, attr))
            self.from_template = True
        else:
            if template is not None:
                print("   WARN continuation page unit row differs from table start; parsing header locally")
            start = 0
            for i in range(self.unit_idx - 1, -1, -1):
                if SAPN_TITLE_RE.search(lines[i].text) or lines[i].size > 7.5:
                    start = i + 1
                    break
            header_rows = [ln for ln in lines[start:self.unit_idx]
                           if any(w.xc > self.left_edge for w in ln.words)]
            group_row, sub_rows = None, []
            for ln in header_rows:
                right = [w for w in ln.words if w.xc > self.left_edge]
                upper = sum(w.text.isupper() for w in right)
                if ln.bold and upper * 2 >= len(right) and group_row is None:
                    group_row = ln
                else:
                    sub_rows.append(ln)
            if group_row is None:
                raise ValueError("SAPN header: group row not found")
            self._assign_groups(group_row)
            self._assign_subs(sub_rows)
        # -- label rows below the unit row (until the first bold heading / data row)
        j = self.unit_idx + 1
        label_rows = []
        while j < len(lines) and not lines[j].bold and not any("$" in w.text for w in lines[j].words):
            label_rows.append(lines[j])
            j += 1
        self.body_start = j
        self._assign_labels(label_rows, labels_too=not self.from_template)
        self._validate()

    def _validate(self):
        problems = []
        if any(not g for g in self.groups):
            problems.append("column without group")
        texts = " ".join(self.groups).upper()
        for must in ("SUPPLY", "ENERGY BASED USAGE", "EXPORT", "DEMAND"):
            if must not in texts:
                problems.append(f"group {must!r} missing")
        if sum(1 for k in range(len(self.groups)) if "EXPORT" in self.groups[k].upper()) != 2:
            problems.append("EXPORT group should span exactly 2 columns")
        for k in range(len(self.groups)):
            if "EXPORT" in self.subs[k] and "EXPORT" not in self.groups[k].upper():
                problems.append(f"col {k}: export sub-label under {self.groups[k]!r}")
        if problems:
            raise ValueError("SAPN header validation failed: " + "; ".join(problems) + "\n" + self.describe())

    def _assign_groups(self, row):
        right = [w for w in row.words if w.xc > self.left_edge]
        phrases = row.phrases(right)
        left = self.left_edge
        for r, ph in enumerate(phrases):
            rightb = 2 * ph.xc - left     # label is centred over its span
            for k, c in enumerate(self.centres):
                if left <= c < rightb + 0.01:
                    self.groups[k] = ph.text
                    self.runs[k] = r
            left = rightb
        # any column after the last span belongs to the last group
        for k, c in enumerate(self.centres):
            if not self.groups[k] and c >= left:
                self.groups[k] = phrases[-1].text
                self.runs[k] = len(phrases) - 1

    def _group_spans(self):
        """{run id: (lo, hi, [column indexes])} for contiguous runs of the same group."""
        spans = {}
        for k, r in enumerate(self.runs):
            lo = self.centres[k] - self.pitch / 2.0
            hi = self.centres[k] + self.pitch / 2.0
            if r in spans:
                spans[r] = (min(spans[r][0], lo), max(spans[r][1], hi), spans[r][2] + [k])
            else:
                spans[r] = (lo, hi, [k])
        return spans

    def _assign_subs(self, rows):
        words = [w for ln in rows for w in ln.words if w.xc > self.left_edge]
        size = median([ln.size for ln in rows]) if rows else 4.6
        for r, (lo, hi, cols) in self._group_spans().items():
            gw = sorted([w for w in words if lo <= w.xc < hi], key=lambda w: w.x0)
            if not gw:
                continue
            clusters, cur, reach = [], [], None
            for w in gw:
                if cur and w.x0 - reach > 0.7 * size:
                    clusters.append(cur)
                    cur, reach = [], None
                cur.append(w)
                reach = w.x1 if reach is None else max(reach, w.x1)
            clusters.append(cur)
            phrases = []
            for cl in clusters:
                cl_sorted = sorted(cl, key=lambda w: (round(w.top), w.x0))
                phrases.append((" ".join(w.text for w in cl_sorted), (min(w.x0 for w in cl) + max(w.x1 for w in cl)) / 2.0))
            if len(phrases) == 1:
                for k in cols:
                    self.subs[k] = phrases[0][0]
            else:
                pcs = [p[1] for p in phrases]
                for k in cols:
                    i, _ = nearest(pcs, self.centres[k])
                    self.subs[k] = phrases[i][0]

    def _assign_labels(self, rows, labels_too=True):
        if labels_too:
            per_row = []
            for ln in rows:
                labels = defaultdict(list)
                for w in ln.words:
                    if w.xc > self.left_edge:
                        i, d = nearest(self.centres, w.xc)
                        labels[i].append(w.text)
                per_row.append((ln, {k: " ".join(v) for k, v in labels.items()}))
            res_rows = [r for r in per_row if "(Residential)" in r[0].text]
            bus_rows = [r for r in per_row if "(Business)" in r[0].text]
            self.dual = bool(res_rows and bus_rows)
            if self.dual:
                res, bus = res_rows[0][1], bus_rows[0][1]
                for k in range(len(self.centres)):
                    a, b = res.get(k, ""), bus.get(k, "")
                    self.labels_res[k] = a or b
                    self.labels_bus[k] = b or a
            else:
                for k in range(len(self.centres)):
                    parts = [r[1][k] for r in per_row if k in r[1]]
                    lab = " ".join(dict.fromkeys(parts))  # drop exact repeats
                    self.labels_res[k] = self.labels_bus[k] = lab
            for k in range(len(self.centres)):
                both = self.labels_res[k] + " " + self.labels_bus[k]
                if ELIG_RE.search(both):
                    m = re.search(r"Refer to [\d.]*\d", both)
                    self.elig[k] = (m.group(0) + " for eligibility") if m else "eligibility remark in header"
                    self.labels_res[k] = ELIG_RE.sub("", self.labels_res[k]).strip()
                    self.labels_bus[k] = ELIG_RE.sub("", self.labels_bus[k]).strip()
        # code columns / name column from the header words left of the first price column
        name_words = [w for ln in rows for w in ln.words if re.fullmatch(r"Name|Description", w.text)]
        if name_words:
            self.name_header_x0 = min(w.x0 for w in name_words)
        left_words = sorted([w for ln in rows for w in ln.words
                             if w.xc < self.left_edge and (self.name_header_x0 is None or w.x1 <= self.name_header_x0 + 1)],
                            key=lambda w: w.x0)
        clusters = []
        for w in left_words:
            if clusters and w.x0 - clusters[-1][-1].x1 < 6 and abs(w.xc - clusters[-1][0].xc) < 25:
                clusters[-1].append(w)
            elif clusters and any(abs(w.xc - c[0].xc) < 10 for c in clusters):
                next(c for c in clusters if abs(w.xc - c[0].xc) < 10).append(w)
            else:
                clusters.append([w])
        for cl in clusters:
            text = " ".join(w.text for w in cl)
            role = "CBD" if "CBD" in text else ("EXPORT" if ("Export" in text or ">30" in text) else "SA")
            self.code_cols.append(((min(w.x0 for w in cl) + max(w.x1 for w in cl)) / 2.0, role))

    def component(self, k, residential):
        lab = self.labels_res[k] if residential else self.labels_bus[k]
        lab = ELIG_RE.sub("", lab).strip()
        parts = []
        for p in (self.groups[k], self.subs[k], lab):
            p = p.strip()
            if p and (not parts or parts[-1] != p):
                parts.append(p)
        return " - ".join(parts)

    def describe(self):
        out = []
        for k in range(len(self.centres)):
            lab = self.labels_res[k] if self.labels_res[k] == self.labels_bus[k] else f"{self.labels_res[k]} | {self.labels_bus[k]}"
            out.append(f"      col{k:02d} x={self.centres[k]:.0f} {self.units[k]:<10} {self.groups[k]} / {self.subs[k]} / {lab}")
        out.append(f"      code cols: {[(round(c), r) for c, r in self.code_cols]} name_header_x0={self.name_header_x0}")
        return "\n".join(out)


CODE_RE = re.compile(r"^[A-Z][A-Z0-9/]*$")
CLASS_RE = re.compile(r"^(Residential|Small & Medium Business|Small Business|Medium Business|Large LV Business|"
                      r"Large HV Business|Major Business|Generation Tariffs|Trial Tariffs)\b(.*)$")


def heading_is_l1(text, state):
    """Decide whether a bold heading starts a new customer class (level 1) or is a sub-heading.

    Level-1 headings are the SAPN tariff-class names (optionally followed by a size qualifier such as
    '>160 MWh pa' or '(Domestic tariffs)', or by a 'Site Specific' qualifier).  A heading immediately
    following another heading, a heading whose words are a subset of the current class ('Small
    Business' under 'Small & Medium Business'), and a class name re-used under a class that has no
    rows of its own ('Large HV Business' under 'Generation Tariffs') are sub-headings.  The first
    heading on a page always starts a new class.
    """
    stack = state["stack"]
    if state.get("first_on_page", True) or not stack:
        return True
    if not state.get("after_rows"):
        return False
    m = CLASS_RE.match(text)
    if not m:
        return False
    if text != stack[0] and set(text.split()) <= set(stack[0].split()):
        return False
    if text in state["l1_seen"] and text != stack[0] and not state.get("rows_under_l1"):
        return False
    qual = m.group(2).strip()
    return qual == "" or qual[0] in "<>(" or "Site Specific" in qual


def sapn_parse_page(lines, hdr, basis, ctx, state):
    """Yield rows for one schedule page.  `state` carries the heading stack across pages."""
    body = lines[hdr.body_start:]
    # name column = left-most x0 of the first lowercase-containing word in data rows
    name_x0 = None
    for ln in body:
        if ln.bold:
            continue
        for w in ln.words:
            if re.search(r"[a-z]", w.text) and w.xc < hdr.left_edge:
                name_x0 = w.x0 if name_x0 is None else min(name_x0, w.x0)
                break
    rows = []
    stack = state.setdefault("stack", [])
    state.setdefault("l1_seen", set())
    state["first_on_page"] = True
    for ln in body:
        has_money = any("$" in w.text for w in ln.words)
        if ln.bold and not has_money:
            text = ln.text.strip()
            if re.fullmatch(r"\d+", text) or text.startswith("SA Power Networks"):
                continue
            if heading_is_l1(text, state):
                stack[:] = [text]
                state["l1_seen"].add(text)
                state["rows_under_l1"] = False
            else:
                stack[1:] = [text]                                 # new / replaced sub-heading
            state["after_rows"] = False
            state["first_on_page"] = False
            continue
        if not ln.words or ln.words[0].xc > hdr.left_edge:
            continue   # footer / page number / stray fragments
        if name_x0 is None or ln.words[0].x0 >= name_x0 - 2:
            continue
        # -- data row
        codes = {}
        name_words, value_words = [], []
        for w in ln.words:
            if w.x0 < name_x0 - 2 and not value_words:
                i, _ = nearest([c for c, _ in hdr.code_cols], w.xc) if hdr.code_cols else (0, 0)
                role = hdr.code_cols[i][1] if hdr.code_cols else "SA"
                codes.setdefault(role, w.text)
            elif "$" in w.text or value_words:
                value_words.append(w)
            else:
                name_words.append(w)
        if not codes:
            continue
        published_codes = {}
        for role, published_code in codes.items():
            if CODE_RE.fullmatch(published_code):
                published_codes.setdefault(published_code, role)
        if not published_codes:
            if DEBUG:
                print("   skip non-code row:", ln.text[:80])
            continue
        code = next(iter(published_codes))
        state["after_rows"] = True
        state["first_on_page"] = False
        if len(stack) == 1:
            state["rows_under_l1"] = True
        name = " ".join(w.text for w in name_words)
        cls = " - ".join(stack)
        residential = bool(stack) and stack[0].lower().startswith("residential")
        for v, x0, x1 in money_values(value_words):
            if v is None:
                continue
            xc = (x0 + x1) / 2.0
            k, d = nearest(hdr.centres, xc)
            if d > 0.65 * hdr.pitch:
                print(f"   WARN value {v} at x={xc:.0f} is {d:.0f}pt from column {k} ({ctx['file']} p{ctx['page']} {code})")
                continue
            if hdr.groups[k].upper().startswith("METERING"):
                # metering charge column of the network table (alternative control): metering side output
                for published_code in published_codes:
                    METERING.append({"distributor": SAPN, "fin_year": ctx["fin_year"], "tariff_code": published_code,
                                     "meter_class": name, "component": hdr.component(k, residential),
                                     "unit": hdr.units[k], "value": v, "gst": "excl", "source_file": ctx["file"],
                                     "locator": locators.pdf(ctx["page"]), "note": ctx["table_note"]})
                continue
            comp = hdr.component(k, residential)
            extra = []
            ct = tb = None
            if hdr.groups[k].upper().startswith("REBATE"):
                ct = "fixed"
                extra.append("Diversify tariff-trial rebate (negative $/day)")
                if hdr.elig[k]:
                    extra.append(f"header: '{hdr.elig[k]}'")
            lab_l = comp.lower()
            if "non-tou" in lab_l or "single rate" in lab_l.split(" - ")[-1]:
                tb = "anytime"
            for published_code, role in published_codes.items():
                if role == "EXPORT" and hdr.groups[k].upper().startswith("EXPORT"):
                    continue
                variant_notes = []
                if published_code != code:
                    if role == "CBD":
                        variant_notes.append(f"site-specific CBD variant of {code}; shared schedule-row prices")
                    elif role == "EXPORT":
                        variant_notes.append(f">30 kW export variant of {code}; export tariffs do not apply; shared schedule-row prices")
                site = "site-specific" if ("Site Specific" in cls or re.search(r"\d{3}$", published_code)) else ""
                rows.append(make_row(
                    SAPN, ctx["fin_year"], ctx["side"], published_code, name, cls, comp, hdr.units[k], v,
                    "excl", basis, ctx["file"], ctx["url"],
                    [ctx["table_note"], ctx.get("doc_note", ""), site] + variant_notes + extra + [ctx["gst_note"]],
                    charge_type=ct, time_band=tb, locator=locators.pdf(ctx["page"])))
    return rows


def parse_sapn(path, fin_year, side, url, extra):
    rows = []
    basis = None
    skip = False
    state = {}
    table_note = ""
    # Header model (groups / sub-groups / labels) is taken from the document's first schedule page
    # (the NUoS table) and re-anchored on each later page's unit row: the four bases share one
    # layout, and later tables / continuation pages in these PDFs sometimes carry squeezed or
    # overlapping header text.
    template = None
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            lines = build_lines(page)
            title = None
            for ln in lines[:6]:
                m = SAPN_TITLE_RE.search(ln.text)
                if m:
                    title = m
                    break
                if ANY_TABLE_RE.match(ln.text.strip()) and "Tariff Schedule" not in ln.text:
                    basis, skip = None, False
            if title:
                kind = title.group(1).upper()
                skip = kind == "METERING"
                basis = SAPN_BASIS.get(kind)
                state = {}
                yr = (title.group(3) or "").replace("/", "-")
                typo = ""
                if yr and yr[-5:] != fin_year[-5:]:
                    typo = f" (table title says {title.group(3)}; document is {fin_year})"
                table_note = f"{title.group(0).split(' ' + title.group(1))[0]} {kind if kind != 'JSO' else 'JSO'} Tariff Schedule p{pno}{typo}"
                table_note = re.sub(r"\s+", " ", table_note)
                if kind == "JSO":
                    table_note += " (published as JSO = Jurisdiction Obligation Scheme)"
            if skip or basis is None:
                continue
            unit_idx = next((i for i, ln in enumerate(lines)
                             if sum(w.text in SAPN_UNITS for w in ln.words) >= 8), None)
            if unit_idx is None:
                continue
            hdr = SapnHeader(lines, unit_idx, template=template)
            if template is None:
                template = hdr
            if DEBUG:
                print(f"  p{pno} basis={basis} dual={hdr.dual} from_template={hdr.from_template}")
                print(hdr.describe())
            ctx = dict(file=path, url=url, fin_year=fin_year, side=side, page=pno,
                       table_note=table_note if title else table_note.split(" p")[0] + f" (cont.) p{pno}",
                       doc_note=extra.get("doc_note", ""), gst_note=extra["gst_note"])
            state["after_rows"] = state.get("after_rows", False)
            rows.extend(sapn_parse_page(lines, hdr, basis, ctx, state))
    return rows


# --------------------------------------------------------------------------------------------
# Power and Water Corporation
# --------------------------------------------------------------------------------------------

PWC_TARIFF_RE = re.compile(r"^Tariff\s+(\d+[a-c]?)\s*:\s*(.*)$")


def pwc_header_columns(header_lines, left_limit):
    """Cluster header phrases by x-centre into columns -> list of dicts(centre, label, unit)."""
    phrases = [ph for ln in header_lines for ph in ln.phrases() if ph.xc > left_limit]
    phrases.sort(key=lambda p: p.xc)
    cols = []
    for ph in phrases:
        if cols and abs(ph.xc - cols[-1]["xs"][0]) < 22:
            cols[-1]["phrases"].append(ph)
            cols[-1]["xs"].append(ph.xc)
        else:
            cols.append({"phrases": [ph], "xs": [ph.xc]})
    out = []
    for c in cols:
        ps = sorted(c["phrases"], key=lambda p: p.top)
        unit = next((p.text for p in ps if p.text.startswith("$/")), "")
        label = " ".join(p.text for p in ps if not p.text.startswith("$/"))
        label = re.sub(r"Off -Peak", "Off-Peak", label)
        label = re.sub(r"\s+", " ", label).strip()
        out.append({"centre": sum(c["xs"]) / len(c["xs"]), "label": label, "unit": unit})
    return out


def pwc_default_unit(label):
    l = label.lower()
    if "sac" in l:
        return "$/NMI/day"
    if "demand" in l or "season" in l:
        return "$/kVA"
    return "$/kWh"


def pwc_charge_type(label):
    """'SAC' (system access charge) is a fixed daily charge; the schema helper does not know the acronym."""
    return "fixed" if label.upper().startswith("SAC") else None


def parse_pwc_onepager(path, fin_year, side, url, extra):
    """PWC 'Standard Control Service Network Price List' one-pagers (2023-24, 2024-25)."""
    rows = []
    with pdfplumber.open(path) as pdf:
        lines = build_lines(pdf.pages[0])
    # locate blocks: title line "(excluding GST)" / "(including GST)" ... until the footnote
    blocks = []
    for i, ln in enumerate(lines):
        m = re.search(r"Tariffs by charging parameter \((excluding|including) GST\)", ln.text)
        if m:
            blocks.append((i, "excl" if m.group(1) == "excluding" else "incl", ln.text))
    footnotes = [ln.text for ln in lines if ln.text.startswith("*")]
    for bi, (start, gst, title) in enumerate(blocks):
        end = blocks[bi + 1][0] if bi + 1 < len(blocks) else len(lines)
        seg = lines[start + 1:end]
        first_data = next(i for i, ln in enumerate(seg) if PWC_TARIFF_RE.match(ln.text))
        header_lines = seg[:first_data]
        tariff_x1 = max((w.x1 for ln in header_lines for w in ln.words if w.text == "Tariff"), default=0)
        cols = pwc_header_columns(header_lines, tariff_x1 + 5)
        centres = [c["centre"] for c in cols]
        if DEBUG:
            print(f"  block '{title}': columns", [(round(c["centre"]), c["label"], c["unit"]) for c in cols])
        cur = None
        for ln in seg[first_data:]:
            if ln.text.startswith("*") or not ln.words:
                break
            m = PWC_TARIFF_RE.match(ln.text)
            if m:
                cur = {"code": f"Tariff {m.group(1)}", "name": [], "vals": []}
                rows_vals = []
                name_words = []
                for w in ln.words:
                    if re.fullmatch(r"\$?[\d,.]+|-", w.text) and w.xc > tariff_x1 + 5:
                        rows_vals.append(w)
                    else:
                        name_words.append(w)
                cur["name"].append(re.sub(r"^Tariff\s+\d+[a-c]?\s*:\s*", "", " ".join(w.text for w in name_words)))
                cur["vals"] += money_values(rows_vals)
                rows.extend(pwc_emit(cur, cols, centres, gst, fin_year, side, path, url, extra, title, footnotes))
                cur["emitted"] = True
                continue
            if cur is None:
                continue
            vals = money_values([w for w in ln.words if w.xc > tariff_x1 + 5])
            if vals:
                cur["vals"] += vals
                rows.extend(pwc_emit(cur, cols, centres, gst, fin_year, side, path, url, extra, title, footnotes))
            elif ln.words[0].x0 < tariff_x1 + 5:
                cur["name"].append(ln.text.strip())
                # name continuation after emission: patch names already emitted for this tariff
                for r in rows:
                    if r["tariff_code"] == cur["code"] and r["gst"] == gst and r["source_file"] == path:
                        r["tariff_name"] = " ".join(cur["name"])
    return rows


def pwc_emit(cur, cols, centres, gst, fin_year, side, path, url, extra, title, footnotes):
    out = []
    pending = cur.get("vals", [])
    for v, x0, x1 in pending:
        if v is None:
            continue
        k, d = nearest(centres, (x0 + x1) / 2.0)
        if d > 30:
            print(f"   WARN PWC value {v} far from any column ({path} {cur['code']})")
            continue
        col = cols[k]
        unit = published = col["unit"]  # published stays empty when the column prints no unit
        note = [f"'{title}'", PWC_BASIS_NOTE]
        if not unit:
            unit = pwc_default_unit(col["label"])
            note.append(extra.get("tou_unit_note") or f"unit not printed for this column; {unit} assumed from the column semantics")
        label = col["label"].rstrip("*")
        if col["label"].endswith("*") and footnotes:
            note.append("footnote: " + footnotes[0].lstrip("* ").strip())
        std_unit = None if unit == published else unit
        if unit == "$/kVA" and extra.get("demand_period_note"):
            std_unit = "$/kVA/month"
            note.append(extra["demand_period_note"])
        out.append(make_row(PWC, fin_year, side, cur["code"], " ".join(cur["name"]), "", label, published, v,
                            gst, "unknown", path, url, note, charge_type=pwc_charge_type(label), std_unit=std_unit,
                            locator=locators.pdf(1)))  # one-pager: only pdf.pages[0] is parsed
    cur["vals"] = []
    return out


PWC7_COLS = [
    ("SAC", "$/NMI/day", ("SAC", "$/NMI/day")),
    ("Energy - Anytime (24/7)", "$/kWh", ("Anytime", "(24/7)")),
    ("Energy - Low Period (Super Off Peak)", "$/kWh", ("Low Period", "Super Off")),
    ("Energy - Mid Period (Off Peak)", "$/kWh", ("Mid Period", "(Off")),
    ("Energy - High Period (Peak)", "$/kWh", ("High Period", "(Peak)")),
    ("Demand - On Season", "$/kVA/month", ("On Season",)),
    ("Demand - Off Season", "$/kVA/month", ("Off Season",)),
]


def parse_pwc_7col(path, fin_year, side, url, extra):
    """Seven-column 'tariffs by charging parameter' tables with '-' placeholders
    (2025-26 one-pager, 2025-26 proposal Table 9, 2026-27 proposal Table 4.3)."""
    title_re = re.compile(extra["title_re"])
    rows = []
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            lines = build_lines(page)
            ti = next((i for i, ln in enumerate(lines) if title_re.search(ln.text)), None)
            if ti is None:
                continue
            first_data = next(i for i, ln in enumerate(lines) if i > ti and PWC_TARIFF_RE.match(ln.text))
            header_text = " ".join(ln.text for ln in lines[ti + 1:first_data])
            for _, _, frags in PWC7_COLS:
                for f in frags:
                    assert f in header_text, f"PWC 7-col header fragment {f!r} missing in {path} p{pno}: {header_text}"
            units_printed = "($/kWh)" in header_text and "($/kVA/month)" in header_text
            unit_note = "" if units_printed else extra.get("unit_note", "")
            title = lines[ti].text.strip()
            cur = None
            for ln in lines[first_data:]:
                if ln.text.startswith("*") or ln.text.startswith("Network Pricing Proposal") or re.match(r"^\d+(\.\d+)*\s+[A-Z]", ln.text):
                    break
                m = PWC_TARIFF_RE.match(ln.text)
                toks = [w.text for w in ln.words]
                vals_idx = [i for i, t in enumerate(toks) if re.fullmatch(r"[\d,]*\.\d+|-", t)]
                if m:
                    cur = {"code": f"Tariff {m.group(1)}", "name": []}
                    name_toks = [t for i, t in enumerate(toks) if i not in vals_idx]
                    cur["name"].append(re.sub(r"^Tariff\s+\d+[a-c]?\s*:\s*", "", " ".join(name_toks)))
                elif cur is not None:
                    name_toks = [t for i, t in enumerate(toks) if i not in vals_idx]
                    if name_toks:
                        cur["name"].append(" ".join(name_toks))
                        for r in rows:
                            if r["tariff_code"] == cur["code"] and r["source_file"] == path:
                                r["tariff_name"] = " ".join(cur["name"])
                if cur is None or not vals_idx:
                    continue
                vals = [toks[i] for i in vals_idx]
                assert len(vals) == 7, f"expected 7 value tokens, got {vals} ({path} p{pno} {ln.text})"
                for (label, unit, _), tok in zip(PWC7_COLS, vals):
                    if tok == "-":
                        continue
                    v = tok.replace(",", "")
                    # a column whose unit is not printed has no published unit; the unit is assumed for conversion
                    unit_pub = unit if units_printed or label == "SAC" else ""
                    note = [f"'{title}' p{pno}", extra.get("doc_note", ""), PWC_BASIS_NOTE, extra.get("gst_note", "")]
                    if not units_printed and label != "SAC":
                        note.append(unit_note)
                    note.append("'-' placeholders in other columns omitted")
                    rows.append(make_row(PWC, fin_year, side, cur["code"], " ".join(cur["name"]), "", label, unit_pub, v,
                                         "excl", "unknown", path, url, note, charge_type=pwc_charge_type(label),
                                         std_unit=None if unit_pub else unit, locator=locators.pdf(pno)))
            break
    return rows


PWC_UNIT_TOKENS = r"\$ \$/day/NMI|\$/day/NMI|\$ per day per|\$/kWh|\$/kVA"
PWC_COMP_RE = re.compile(rf"^(?P<label>[A-Za-z][A-Za-z \-/]*?)\s+(?P<unit>{PWC_UNIT_TOKENS})\s+(?P<nums>[\d.]+(?:\s+[\d.]+)*)$")
YEAR_RE = re.compile(r"20\d\d[‐‑‒–-]\d\d")


def parse_pwc_indicative(path, fin_year, side, url, extra):
    """Indicative price schedule tables (Table B.1 2023-24, Table A.1 2024-25): rows = components,
    columns = years; pick the column for fin_year."""
    rows = []
    table = extra["table"]
    with pdfplumber.open(path) as pdf:
        pages = [(i + 1, build_lines(p)) for i, p in enumerate(pdf.pages)]
    start = next(i for i, (pno, lines) in enumerate(pages) if any(ln.text.startswith(table + ":") for ln in lines))
    year_idx = None
    cur = None          # current tariff dict
    last = None         # ("tariff" | "comp", obj)
    gst_title = ""
    for pno, lines in pages[start:start + 2]:
        for ln in lines:
            t = re.sub(r"\s+", " ", ln.text.strip())
            if t.startswith(table + ":"):
                gst_title = t
                continue
            yrs = YEAR_RE.findall(t)
            if len(yrs) >= 4:
                yrs = [re.sub(r"[‐‑‒–]", "-", y) for y in yrs]
                year_idx = yrs.index(fin_year)
                continue
            m = PWC_TARIFF_RE.match(t)
            if m:
                cur = {"code": f"Tariff {m.group(1)}", "name": m.group(2).strip(), "comps": []}
                last = ("tariff", cur)
                continue
            m = PWC_COMP_RE.match(t)
            if m and cur is not None and year_idx is not None:
                nums = m.group("nums").split()
                comp = {"label": m.group("label").strip(), "unit": m.group("unit"), "value": nums[year_idx], "page": pno}
                cur["comps"].append(comp)
                rows.append((cur, comp))
                last = ("comp", comp)
                continue
            # continuation lines ("NMI", "Charge", "Residential")
            if last and re.fullmatch(r"[A-Za-z\-]+", t):
                if last[0] == "tariff":
                    cur["name"] = cur["name"] + ("" if cur["name"].endswith("-") else " ") + t
                elif t == "NMI":
                    last[1]["unit"] += " NMI"
                else:
                    last[1]["label"] += " " + t
            if t.startswith("Network Pricing Proposal") or t.startswith("Page "):
                last = None
    out = []
    for cur, comp in rows:
        unit = comp["unit"]
        note = [extra["doc_note"], f"p{comp['page']}", PWC_BASIS_NOTE, f"'{gst_title}'"]
        if unit.startswith("$ $"):
            note.append("stray '$' before unit in source ('$ $/day/NMI')")
            unit = unit[2:]
        std_unit = None
        if unit == "$/kVA" and extra.get("demand_period_note"):
            std_unit = "$/kVA/month"
            note.append(extra["demand_period_note"])
        out.append(make_row(PWC, fin_year, side, cur["code"], cur["name"], "", comp["label"], unit, comp["value"],
                            "excl", "unknown", path, url, note, charge_type=pwc_charge_type(comp["label"]), std_unit=std_unit,
                            locator=locators.pdf(comp["page"])))
    return out


# --------------------------------------------------------------------------------------------

PARSERS = {
    "sapn": parse_sapn,
    "pwc_onepager": parse_pwc_onepager,
    "pwc_7col": parse_pwc_7col,
    "pwc_indicative": parse_pwc_indicative,
}


def main():
    urls = load_inventory()
    all_rows = []
    for distributor, fin_year, side, path, parser, extra in FILES:
        url = urls.get(path)
        if not url:
            raise SystemExit(f"no inventory URL for {path}")
        print(f"== {distributor} {fin_year} {side}: {path}")
        rows = PARSERS[parser](path, fin_year, side, url, extra)
        for r in rows:
            assert r["distributor"] == distributor and r["fin_year"] == fin_year and r["side"] == side
        codes = sorted({r["tariff_code"] for r in rows})
        bases = sorted({r["basis"] for r in rows})
        gsts = sorted({r["gst"] for r in rows})
        print(f"   rows={len(rows)} codes={len(codes)} basis={bases} gst={gsts}")
        if DEBUG:
            print("   codes:", codes)
        all_rows.extend(rows)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=schema.COLUMNS)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in schema.COLUMNS})
    print(f"wrote {len(all_rows)} rows -> {OUT_PATH}")
    schema.write_metering("sapn_pwc", METERING)
    print(f"wrote {len(METERING)} metering cells -> {schema.METERING_OUT_DIR}/sapn_pwc.csv")


if __name__ == "__main__":
    main()
