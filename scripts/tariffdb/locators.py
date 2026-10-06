"""Locators: where in a source document a value was read, precise enough to re-check by hand.

Grammar (the `locator` column of the parser contract, scripts/schema.py):
  xlsx:<sheet>!<cell>        a spreadsheet cell, e.g.  xlsx:Tariff schedule!H16
  pdf:p<page>                a PDF page (1-based), e.g.  pdf:p37
  pdf-ocr:p<page>            an image-only PDF page read by OCR (text cannot be re-extracted; see the parser's sum check)
  html:text                  the visible text of a saved web page (AER landing pages with version changelogs)

`verify(path, locator, value)` re-reads the source at the locator and says whether the value is there.
"""
import functools
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from published import cell_value  # noqa: E402

LOCATOR_RE = re.compile(r"^(?:xlsx:(?P<sheet>[^!]+)!(?P<cell>[A-Z]{1,3}[1-9][0-9]*)|(?P<kind>pdf|pdf-ocr):p(?P<page>[1-9][0-9]*)"
                        r"|(?P<html>html):text)$")


def xlsx(ws, cell) -> str:
    """Locator of an openpyxl cell (pass the worksheet or its title and the cell or its coordinate)."""
    title = ws if isinstance(ws, str) else ws.title
    coord = cell if isinstance(cell, str) else cell.coordinate
    return f"xlsx:{title}!{coord}"


def pdf(page_no: int, ocr: bool = False) -> str:
    return f"{'pdf-ocr' if ocr else 'pdf'}:p{int(page_no)}"


def parse(locator: str) -> dict:
    """Split a locator into {kind, sheet, cell, page}; raises ValueError when it does not follow the grammar."""
    m = LOCATOR_RE.match(locator or "")
    if not m:
        raise ValueError(f"bad locator {locator!r}")
    if m.group("sheet"):
        return {"kind": "xlsx", "sheet": m.group("sheet"), "cell": m.group("cell"), "page": None}
    if m.group("html"):
        return {"kind": "html", "sheet": None, "cell": None, "page": None}
    return {"kind": m.group("kind"), "sheet": None, "cell": None, "page": int(m.group("page"))}


@functools.lru_cache(maxsize=8)
def _workbook(path):
    import openpyxl
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return openpyxl.load_workbook(path, data_only=True)


@functools.lru_cache(maxsize=4096)
def _page_tokens(path, page_no):
    """Whitespace-separated tokens of a page, from two extractions (layout text and word boxes): PDF tables often
    split one number over several glyph runs ('1 5.42'), so a value may span consecutive tokens."""
    import pdfplumber
    with pdfplumber.open(path) as doc:
        if page_no > len(doc.pages):
            return None
        page = doc.pages[page_no - 1]
        for c in page.objects.get("char", []):
            c["upright"] = True
        text = page.extract_text() or ""
        words = [w["text"] for w in page.extract_words(x_tolerance=1.5, y_tolerance=2.0)]
    return (tuple(text.split()), tuple(t for w in words for t in w.split()))


def read_cell(path, sheet, cell):
    """(raw value, displayed value) of a spreadsheet cell; displayed follows the cell's number format."""
    ws = _workbook(path)[sheet]
    c = ws[cell]
    return c.value, cell_value(c)


def excel_display(cell):
    """The number as Excel shows it: Excel keeps 15 significant digits and rounds half away from zero, so 0.02215 in a
    4-dp format shows 0.0222. (published.cell_value formats the binary float, 0.022149999..., and gives 0.0221.)"""
    from decimal import ROUND_HALF_UP, Decimal
    shown = cell_value(cell)
    value = cell.value
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isinstance(shown, str) \
            or not re.fullmatch(r"-?\d+(\.\d+)?", shown):
        return shown
    digits = len(shown.partition(".")[2])
    return str(Decimal(format(value, ".15g")).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def read_cell_excel(path, sheet, cell):
    """(raw value, value as Excel displays it)."""
    c = _workbook(path)[sheet][cell]
    return c.value, excel_display(c)


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


@functools.lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


@functools.lru_cache(maxsize=32)
def ocr_text(path, page_no, resolution=288):
    """Re-render and recognise image tables; never treat an OCR locator as proof by itself."""
    import pdfplumber
    with pdfplumber.open(path) as doc:
        result, _ = _ocr_engine()(doc.pages[page_no - 1].to_image(resolution=resolution).original)
    return " ".join(r[1] for r in (result or []))


def renderings(value: str):
    """Ways a published number can appear in PDF text: as is, with thousands separators, in parentheses or with a
    leading '$', with or without its leading zero."""
    v = str(value).strip()
    neg = v.startswith("-")
    body = v.lstrip("-")
    forms = {body}
    if re.match(r"^\d+\.\d+$|^\d+$", body):
        ip, _, fp = body.partition(".")
        grouped = f"{int(ip):,}" + (f".{fp}" if fp else "")
        forms.add(grouped)
        if ip == "0" and fp:
            forms.add("." + fp)
    out = set()
    for f in forms:
        if neg:
            out |= {"-" + f, "(" + f + ")", "-$" + f, "($" + f + ")", "$(" + f + ")", "–" + f, "−" + f}
        else:
            out |= {f, "$" + f}
    return out


@functools.lru_cache(maxsize=256)
def _page_glyphs(path, page_no):
    import pdfplumber
    with pdfplumber.open(path) as doc:
        return tuple((c["text"], c["x0"], c["x1"], c["top"], c["bottom"]) for c in doc.pages[page_no - 1].chars
                     if c["text"].strip())


def _glyph_run(path, page_no, form, gap=1.5):
    """Is `form` printed as one run of adjacent glyphs (left to right on a line, or top to bottom), with no digit
    glyph adjacent before or after it?"""
    glyphs = _page_glyphs(path, page_no)
    by_text = {}
    for g in glyphs:
        by_text.setdefault(g[0], []).append(g)

    def nxt(g, h, horizontal):
        if horizontal:
            return abs(h[3] - g[3]) <= 1 and -0.5 <= h[1] - g[2] <= gap
        return abs(h[1] - g[1]) <= 1 and -0.5 <= h[3] - g[4] <= gap

    for horizontal in (True, False):
        for g in by_text.get(form[0], []):
            run = [g]
            for ch in form[1:]:
                h = next((h for h in by_text.get(ch, []) if nxt(run[-1], h, horizontal)), None)
                if h is None:
                    break
                run.append(h)
            else:
                if not any(d[0].isdigit() and (nxt(d, run[0], horizontal) or nxt(run[-1], d, horizontal))
                           for d in glyphs):
                    return True
    return False


def verify(path: str, locator: str, value) -> tuple[bool, str]:
    """Re-read `value` at `locator` in the file at `path`. Returns (ok, evidence)."""
    loc = parse(locator)
    if not os.path.exists(path):
        return False, f"missing file {path}"
    if loc["kind"] == "xlsx":
        raw, shown = read_cell(path, loc["sheet"], loc["cell"])
        if str(shown) == str(value):
            return True, f"cell shows {shown}"
        _, excel = read_cell_excel(path, loc["sheet"], loc["cell"])
        if str(excel) == str(value):
            return True, f"cell shows {excel} in Excel (half-up rounding)"
        a, b = _num(shown), _num(value)
        if a is not None and b is not None and abs(a - b) <= 1e-12 * max(1.0, abs(a)):
            return True, f"cell shows {shown}"
        return False, f"cell {loc['sheet']}!{loc['cell']} holds {raw!r} (shown {shown!r}), expected {value!r}"
    streams = ((tuple(ocr_text(path, loc["page"]).split()),) if loc["kind"] == "pdf-ocr"
               else _page_tokens(path, loc["page"]))
    if streams is None:
        return False, f"page {loc['page']} beyond end of {path}"
    forms = sorted(renderings(value), key=len, reverse=True)
    negative = str(value).strip().startswith("-")
    # a unit, footnote mark or label may be glued to the value, but never another digit; a positive value must not
    # carry a minus sign or opening parenthesis anywhere before it, glued or as the previous token
    pre = r"[^\d.]*" if negative else r"[^\d.\-(\u2013\u2212]*"
    for tokens in streams:
        for i in range(len(tokens)):
            if not negative and i and re.fullmatch(r"[^\w.]*[\-(\u2013\u2212][^\w.]*", tokens[i - 1]):
                # a lone '-' or '(' token is a sign only when its glyph touches the number; tables also print '-'
                # in empty cells next to a value
                if loc["kind"] != "pdf" or any(_glyph_run(path, loc["page"], tokens[i - 1][-1] + f) for f in forms):
                    continue
            joined, cuts = "", []
            for j in range(i, min(i + 4, len(tokens))):
                if joined:
                    cuts.append(len(joined))
                joined += tokens[j]
                for form in forms:
                    m = re.fullmatch("(" + pre + ")" + re.escape(form) + r"(?:[^\d][^\d]*)?", joined)
                    if not m:
                        continue
                    a, b = m.end(1), m.end(1) + len(form)
                    # extraction can split one printed number over glyph runs ('4 1.10'), but two separate numbers
                    # ('12' and '3.4') must not be read as one: a token break between two digits of the value is
                    # accepted only when the glyphs themselves are adjacent on the page
                    if any(a < c < b and joined[c - 1].isdigit() and joined[c].isdigit() for c in cuts) and not (
                            loc["kind"] == "pdf" and _glyph_run(path, loc["page"], form)):
                        continue
                    return True, f"'{form}' on page {loc['page']}"
    return False, f"{value!r} not found on page {loc['page']}"


def normalise_text(s: str) -> str:
    """Whitespace- and dash-insensitive form used to find quoted wording in a source."""
    return re.sub(r"\s+", "", _spaced(s))


def _spaced(s: str) -> str:
    """Lower-case text with quotes and dashes unified and each whitespace run reduced to one space."""
    s = (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[‐-―−]", "-", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _kind(ch):
    return "d" if ch.isdigit() else "a" if ch.isalpha() else "p"


class _Text:
    """Text with its whitespace removed plus where its words and numbers end. In a spreadsheet cell (words=True) a
    boundary is a space or a change between digits, letters and punctuation. PDF and OCR text runs neighbouring words
    and table cells together ('residentialcustomers', 'TAS1013 56.440'), so there only numbers are delimited: every
    position is a boundary except inside a run of digits that the text does not split."""

    def __init__(self, s, words=False):
        chars, spaced = [], set()
        for ch in _spaced(s):
            if ch == " ":
                spaced.add(len(chars))
            else:
                chars.append(ch)
        self.s = "".join(chars)
        self.spaces = spaced - {0, len(chars)}
        if words:
            inner = {i for i in range(1, len(chars)) if _kind(chars[i - 1]) != _kind(chars[i]) or _kind(chars[i]) == "p"}
        else:
            inner = {i for i in range(1, len(chars)) if not (chars[i - 1].isdigit() and chars[i].isdigit())}
        self.boundaries = {0, len(chars)} | spaced | inner

    def digit_gaps(self, lo, hi):
        """Spaces strictly inside [lo, hi) that separate two digits."""
        return {p for p in self.spaces if lo < p < hi and self.s[p - 1].isdigit() and self.s[p].isdigit()}

    def contains(self, quote: "_Text") -> bool:
        """The quote occurs starting and ending on boundaries, and splits numbers exactly where the text does ('1' is
        not in '14', 'NEE2 44' is not 'NEE24 4'; in a cell a cut-off word does not match either). Other spaces are
        ignored."""
        q = quote.s
        k = self.s.find(q)
        while k >= 0:
            if k in self.boundaries and k + len(q) in self.boundaries and \
                    {p - k for p in self.digit_gaps(k, k + len(q))} == quote.digit_gaps(0, len(q)):
                return True
            k = self.s.find(q, k + 1)
        return False


@functools.lru_cache(maxsize=16)
def _html_text(path):
    import html
    with open(path, encoding="utf-8", errors="replace") as f:
        s = f.read()
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)
    return (_Text(html.unescape(re.sub(r"<[^>]+>", " ", s))),)


@functools.lru_cache(maxsize=2048)
def _page_text(path, page_no):
    """The page's text as laid out and with every glyph treated as upright, each also as its word boxes: layout text
    can run neighbouring table cells together, the word boxes often keep them apart."""
    import pdfplumber
    with pdfplumber.open(path) as doc:
        if page_no > len(doc.pages):
            return None
        page = doc.pages[page_no - 1]
        out = [_Text(page.extract_text() or ""), _Text(" ".join(w["text"] for w in page.extract_words()))]
        for char in page.chars:
            char["upright"] = True
        out += [_Text(page.extract_text() or ""), _Text(" ".join(w["text"] for w in page.extract_words()))]
        return tuple(out)


@functools.lru_cache(maxsize=64)
def _sheet_text(path, sheet):
    ws = _workbook(path)[sheet]
    return {c.coordinate: _Text(str(c.value), words=True) for row in ws.iter_rows() for c in row if c.value is not None}


@functools.lru_cache(maxsize=32)
def _ocr_page_text(path, page_no):
    return (_Text(ocr_text(path, page_no)),)


def verify_quote(path: str, locator: str, quote: str) -> tuple[bool, str]:
    """Is the verbatim `quote` present at `locator`, on word boundaries? For xlsx the locator cell must contain it;
    for a PDF the page."""
    loc = parse(locator)
    q = _Text(quote, words=True)
    if not q.s:
        return False, "empty quote"
    if not os.path.exists(path):
        return False, f"missing file {path}"
    if loc["kind"] == "xlsx":
        cell = _sheet_text(path, loc["sheet"]).get(loc["cell"])
        if cell is not None and cell.contains(q):
            return True, "quote in cell"
        return False, f"quote not in {loc['sheet']}!{loc['cell']}"
    if loc["kind"] == "pdf-ocr":
        texts, where = _ocr_page_text(path, loc["page"]), "rendered-page OCR"
    elif loc["kind"] == "html":
        texts, where = _html_text(path), "page text"
    else:
        texts, where = _page_text(path, loc["page"]), f"page {loc['page']}"
        if texts is None:
            return False, f"page {loc['page']} beyond end"
    present = any(t.contains(q) for t in texts)
    return present, (f"quote in {where}" if present else f"quote not in {where}")
