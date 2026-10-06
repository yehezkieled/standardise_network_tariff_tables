"""Unit normalisation.

Standard units: cents for money; per-day for fixed/daily charges; demand charges are kept in
their published period (day/month/year/season; AER seasonal suffixes like highsn/lowsn/Summer name
the season of a per-day price, not a period) but converted to cents, because converting
$/kVA/month to c/kVA/day changes the economic quantity (billing period). The reconciler compares
in `unit_std` space; conflicting explicit billing periods cannot match. Periods inferred
only from labels are marked soft with `?` and do not establish the billing period.
"""
import re

DAYS_PER_YEAR = 365.0
DAYS_PER_MONTH = 365.0 / 12.0


SEASON_SUFFIX_RE = r"highsn|lowsn|/sn\b|summer|winter|smmr|/sum|/hs\b|/ls\b"


def _stated_period(s):
    """Billing period named in a lower-cased, whitespace-free unit string, or ''."""
    if re.search(r"/day|perday|/d\b|daily|pd\b", s):
        return "day"
    if re.search(r"/month|/mth|mths|permonth|/mo\b|monthly|pm\b", s):
        return "month"
    if re.search(r"/year|/yr|/annum|p\.?a\.?|perannum|annual|/a\b", s):
        return "year"
    return ""


def parse_unit(u: str):
    """Return (money, quantity, period) from a unit string.
    money: 'c' | '$'; quantity: 'kWh', 'kVAh', 'MWh', 'kW', 'kVA', 'k?'
    (unit names both kW and kVA), 'lamp' or ''; period: 'day', 'month',
    'year', 'season' or ''.
    """
    s = (u or "").strip().lower()
    s = s.replace("¢", "c").replace("cents", "c").replace("cent", "c").replace("aud", "$")
    s = re.sub(r"\s+", "", s)
    money = "$" if s.startswith("$") else "c"
    quantity = "k?" if re.search(r"kva(?!h)", s) and re.search(r"kw(?!h)", s) else ""
    for q, pat in (("kwh", r"kwh"), ("kvah", r"kvah"), ("kva", r"kva"), ("kw", r"kw(?!h)"), ("lamp", r"lamp|lmp"), ("mwh", r"mwh")):
        if quantity:
            break
        if re.search(pat, s):
            quantity = {"kwh": "kWh", "kvah": "kVAh", "kva": "kVA", "kw": "kW", "lamp": "lamp", "mwh": "MWh"}[q]
    period = _stated_period(s)
    if not period and re.search(SEASON_SUFFIX_RE, s):
        period = "day"  # 'cents/kVA/Summer': a season, not a billing period (see period_stated)
    elif "season" in s:
        period = "season"
    if quantity == "" and period == "" and re.search(r"customer|connection|site|nmi", s):
        period = "year" if re.search(r"p\.?a|annum|year", s) else ""
    return money, quantity, period


def period_stated(unit: str) -> bool:
    """True when the unit text itself names a billing period (day, month or year). A season suffix
    ('cents/kVA/Summer', read as per day) or a bare quantity ('$/kVA') leaves the period to interpretation."""
    return bool(_stated_period(re.sub(r"\s+", "", (unit or "").lower())))


def to_std(value, unit: str, label: str = "", period_hint: str = ""):
    """Convert published value+unit to (value_std, unit_std).

    - money to cents
    - fixed/daily charges (no kW/kVA/kWh quantity) to per-day
    - kWh charges: c/kWh
    - demand charges: cents per kW/kVA per published period (day|month|year|season)

    period_hint: billing period of a demand price whose unit states none, taken from the distributor's own price list
    (it then outranks a period inferred from the label, which may name the measurement window instead).
    """
    if value is None or value == "":
        return None, ""
    try:
        v = float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None, ""
    money, quantity, period = parse_unit(unit)
    # a period read from a season suffix is not printed in the unit: soft, like a label-inferred period
    inferred = period == "day" and not period_stated(unit)
    if not period and period_hint and quantity in ("kW", "kVA", "k?"):
        period, inferred = period_hint, True  # not printed in this unit: soft, like a label-inferred period
    elif not period and label:
        ll = label.lower()
        if "annual" in ll or "per annum" in ll or "p.a" in ll or "/year" in ll or "yearly" in ll or re.match(r"ann\b", ll) or "ann dmnd" in ll:
            period = "year"
        elif "monthly" in ll or "/month" in ll or "per month" in ll or re.match(r"mth\b", ll) or "mth dmnd" in ll or "rolling" in ll:
            period = "month"
        elif "daily" in ll or "/day" in ll or "per day" in ll:
            period = "day"
        inferred = bool(period)
    if money == "$":
        v *= 100.0
    if quantity == "kWh":
        return v, "c/kWh"
    if quantity == "kVAh":
        return v, "c/kVAh"
    if quantity == "MWh":
        return v / 1000.0, "c/kWh"
    if quantity in ("kW", "kVA", "k?"):
        # period taken from the label (not the unit) is marked soft with "?" : e.g. "Annual demand" may name the
        # measurement window while the charge is billed per day (SA Power Networks $/kVA/day)
        p = (period + "?") if (period and inferred) else (period or "?")
        return v, f"c/{quantity}/{p}"
    # fixed-type charge: normalise to per-day (label-inferred periods are accepted here: "per annum" style labels)
    if period == "year":
        return v / DAYS_PER_YEAR, "c/day"
    if period == "month":
        return v / DAYS_PER_MONTH, "c/day"
    if quantity == "lamp":
        return v, "c/lamp/" + (period or "day")
    return v, "c/day" if period in ("day", "") else f"c/{period}"
