"""Unit normalisation.

Standard units: cents for money; per-day for fixed/daily charges; demand charges are kept in
their published period (day/month/year/season) but converted to cents, because converting
$/kVA/month to c/kVA/day changes the economic quantity (billing period). The reconciler compares
in `unit_std` space and falls back to period conversion only when the two sides disagree on
period (flagged as a 'unit_period' explanation).
"""
import re

DAYS_PER_YEAR = 365.0
DAYS_PER_MONTH = 365.0 / 12.0


def parse_unit(u: str):
    """Return (money, quantity, period) from a unit string.
    money: 'c' | '$' ; quantity: 'kWh','kW','kVA','day','customer','lamp','site' or '' ; period: 'day','month','year','season','' .
    """
    s = (u or "").strip().lower()
    s = s.replace("¢", "c").replace("cents", "c").replace("cent", "c").replace("aud", "$")
    s = re.sub(r"\s+", "", s)
    money = "$" if s.startswith("$") else "c"
    quantity = ""
    for q, pat in (("kwh", r"kwh"), ("kvah", r"kvah"), ("kva", r"kva"), ("kw", r"kw(?!h)"), ("lamp", r"lamp|lmp"), ("mwh", r"mwh")):
        if re.search(pat, s):
            quantity = {"kwh": "kWh", "kvah": "kVAh", "kva": "kVA", "kw": "kW", "lamp": "lamp", "mwh": "MWh"}[q]
            break
    period = ""
    if re.search(r"/day|perday|/d\b|daily|pd\b", s):
        period = "day"
    elif re.search(r"/month|/mth|mths|permonth|/mo\b|monthly|pm\b", s):
        period = "month"
    elif re.search(r"/year|/yr|/annum|p\.?a\.?|perannum|annual|/a\b", s):
        period = "year"
    elif re.search(r"season|highsn|lowsn|/sn|summer|winter|smmr|/sum", s):
        period = "season"
    if quantity == "" and period == "" and re.search(r"customer|connection|site|nmi", s):
        period = "year" if re.search(r"p\.?a|annum|year", s) else ""
    return money, quantity, period


def to_std(value, unit: str, label: str = ""):
    """Convert published value+unit to (value_std, unit_std).

    - money to cents
    - fixed/daily charges (no kW/kVA/kWh quantity) to per-day
    - kWh charges: c/kWh
    - demand charges: cents per kW/kVA per published period (day|month|year|season)
    """
    if value is None or value == "":
        return None, ""
    try:
        v = float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None, ""
    money, quantity, period = parse_unit(unit)
    inferred = False
    if not period and label:
        ll = label.lower()
        if "annual" in ll or "per annum" in ll or "p.a" in ll or "/year" in ll or "yearly" in ll or re.match(r"ann\b", ll) or "ann dmnd" in ll:
            period = "year"
        elif "monthly" in ll or "/month" in ll or "per month" in ll or re.match(r"mth\b", ll) or "mth dmnd" in ll or "rolling" in ll:
            period = "month"
        elif "daily" in ll or "/day" in ll or "per day" in ll:
            period = "day"
        elif "season" in ll or "highsn" in ll or "lowsn" in ll or "summer" in ll or "winter" in ll:
            period = "season"
        inferred = bool(period)
    if money == "$":
        v *= 100.0
    if quantity == "kWh":
        return v, "c/kWh"
    if quantity == "kVAh":
        return v, "c/kVAh"  # apparent-energy export charges (Evoenergy); compared in the energy family
    if quantity == "MWh":
        return v / 1000.0, "c/kWh"
    if quantity in ("kW", "kVA"):
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
