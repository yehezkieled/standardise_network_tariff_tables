"""How the AER spells a distributor's tariff codes: the rules in data/tariffdb/code_alias.csv.

The AER sometimes prints a tariff under another code than the distributor's own price list. A provisional (AER) code
that names a tariff the distributor's final list prices must give way to it, or the same tariff is stored twice.

  - Spelling: codes compare after norm() (whitespace removed, upper case, trailing '*' dropped), so 'LVDed' (AER) is
    the distributor's 'LVDED'. No rule is needed; the tariff is stored under the distributor's spelling.
  - Other codes: one row of code_alias.csv per rule. `{code}` stands for the same code text on both sides and `{n}`
    for one digit 1-9 on the distributor side; a row without placeholders names one pair. Example: ergon, `{code}`,
    `{code}T{n}`: the AER's EBDEM is priced by Ergon's list as EBDEMT1, EBDEMT2 and EBDEMT3.

A rule only ever matches codes the distributor's list really prices on that day; it never creates a code.
"""
import csv
import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_support as bs  # noqa: E402

PATH = os.path.join(bs.ROOT, "data", "tariffdb", "code_alias.csv")
COLUMNS = ["distributor_id", "aer_code", "distributor_code", "valid_from", "valid_to", "reason"]
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def norm(code):
    return re.sub(r"\s+", "", code).upper().rstrip("*")


def _pattern(text, code=None):
    """Regex for a rule side: literal parts normalised, `{code}` as a group (or the captured code), `{n}` a digit."""
    out, pos = [], 0
    for m in PLACEHOLDER.finditer(text):
        out.append(re.escape(norm(text[pos:m.start()])))
        out.append(r"(?P<code>.+)" if m.group(1) == "code" and code is None else
                   re.escape(code) if m.group(1) == "code" else "[1-9]")
        pos = m.end()
    out.append(re.escape(norm(text[pos:])))
    return re.compile("".join(out))


def _date(s):
    try:
        return datetime.date.fromisoformat(s).isoformat() == s
    except ValueError:
        return False


def problems(rows):
    known = {d["distributor_id"] for d in bs.DISTRIBUTORS}
    bad = []
    for i, r in enumerate(rows, 2):
        where = f"code_alias.csv:{i}"
        if r["distributor_id"] not in known:
            bad.append(f"{where}: unknown distributor {r['distributor_id']!r}")
        if not r["aer_code"].strip() or not r["distributor_code"].strip() or not r["reason"].strip():
            bad.append(f"{where}: aer_code, distributor_code and reason are required")
        for side in ("aer_code", "distributor_code"):
            names = PLACEHOLDER.findall(r[side])
            if set(names) - {"code", "n"} or names.count("code") > 1 or names.count("n") > 1:
                bad.append(f"{where}: {side} {r[side]!r} may hold only one {{code}} and one {{n}}")
        if "{n}" in r["aer_code"]:
            bad.append(f"{where}: {{n}} belongs on the distributor side")
        if ("{code}" in r["aer_code"]) != ("{code}" in r["distributor_code"]):
            bad.append(f"{where}: {{code}} must be on both sides or neither")
        for side in ("valid_from", "valid_to"):
            if r[side] and not _date(r[side]):
                bad.append(f"{where}: {side} {r[side]!r} is not YYYY-MM-DD")
        if r["valid_from"] and r["valid_to"] and r["valid_from"] > r["valid_to"]:
            bad.append(f"{where}: valid_from is after valid_to")
    return bad


def load(path=PATH):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise ValueError(f"{path}: header must be {','.join(COLUMNS)}")
        rows = list(reader)
    bad = problems(rows)
    if bad:
        raise ValueError("; ".join(bad))
    return rows


def targets(rules, distributor_id, aer_code, day, codes):
    """The codes among `codes` (a distributor's final list in force on `day`) that price the tariff the AER prints as
    `aer_code`, by the rules for that distributor and day."""
    found = set()
    for r in rules:
        if r["distributor_id"] != distributor_id or not (r["valid_from"] or day) <= day <= (r["valid_to"] or day):
            continue
        m = _pattern(r["aer_code"]).fullmatch(norm(aer_code))
        if not m:
            continue
        want = _pattern(r["distributor_code"], m.groupdict().get("code"))
        found |= {c for c in codes if want.fullmatch(norm(c))}
    return sorted(found)
