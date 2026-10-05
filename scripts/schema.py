import re
"""Shared normalised row schema for both AER-side and DNSP-side extractions.

Every parser emits rows as dicts with exactly these keys (strings unless noted).
"""

COLUMNS = [
    "side",            # "AER" (AER-authored file) | "AER_HOSTED" (DNSP document hosted on aer.gov.au) | "DNSP" (published on the distributor's own site / Wayback copy of it)
    "distributor",     # canonical: Ausgrid, AusNet Services, CitiPower, Endeavour Energy, Energex, Ergon Energy, Essential Energy, Evoenergy, Jemena, Power and Water Corporation, Powercor, SA Power Networks, TasNetworks, United Energy
    "fin_year",        # 2023-24 | 2024-25 | 2025-26 | 2026-27
    "tariff_code",     # as published (stripped)
    "tariff_name",     # as published
    "customer_class",  # tariff class / customer class as published, else ""
    "component",       # charge component label as published (e.g. "Peak energy charge")
    "charge_type",     # fixed | energy | demand | capacity | export | other
    "time_band",       # peak | shoulder | offpeak | anytime | solar_soak | block1 | block2 | ... | "" (best effort from component label)
    "season",          # high | low | summer | winter | "" (best effort)
    "unit",            # unit as published, e.g. "cents/kWh", "$/kVA/month"
    "value",           # float as published (string repr ok)
    "value_std",       # float converted to standard units: cents, and per-day for fixed/daily charges; see units.py
    "unit_std",        # standard unit label after conversion: c/day, c/kWh, c/kW/day, c/kVA/day, c/kW/month, c/kVA/month, c/kW/year, c/kVA/year, c/kW/season ...
    "gst",             # excl | incl
    "basis",           # NUoS | DUoS | TUoS | JSA | DPPC | unknown   (NUoS = total network price incl transmission + jurisdictional scheme)
    "source_file",     # repo-relative path of the file parsed
    "source_url",      # exact URL the file was downloaded from
    "note",            # free text caveats (e.g. "LFiT included", "site-specific", "obsolete tariff")
]

CANON = {
    "ausgrid": "Ausgrid", "ausnet": "AusNet Services", "ausnet services": "AusNet Services",
    "citipower": "CitiPower", "endeavour": "Endeavour Energy", "endeavour energy": "Endeavour Energy",
    "energex": "Energex", "ergon": "Ergon Energy", "ergon energy": "Ergon Energy",
    "essential": "Essential Energy", "essential energy": "Essential Energy", "evoenergy": "Evoenergy",
    "jemena": "Jemena", "pwc": "Power and Water Corporation", "power and water": "Power and Water Corporation",
    "power and water corporation": "Power and Water Corporation", "powercor": "Powercor",
    "sapn": "SA Power Networks", "sa power networks": "SA Power Networks", "tasnetworks": "TasNetworks",
    "united energy": "United Energy", "ue": "United Energy",
}


def charge_type_from_label(label: str, unit: str = "") -> str:
    l = (label or "").lower()
    u = (unit or "").lower()
    if any(k in l for k in ("export", "reward", "rebate", "feed in", "feed-in", "credit")):
        return "export"
    if any(k in l for k in ("fixed", "standing", "access charge", "service charge", "supply charge", "connection unit", "general service", "common service", "daily charge", "network access", "system access")) or ("/day" in u and not re.search(r"k(w|va)", u)):
        return "fixed"
    if "real capacity" in l and re.search(r"/kw(?!h)", u):
        return "demand"
    if "capacity" in l:
        return "capacity"
    if "demand" in l or re.search(r"/k(w|va)(?!h)", u):
        return "demand"
    if "kwh" in u or "kvah" in u or any(k in l for k in ("energy", "usage", "volume", "unit rate", "consumption", "block", "anytime", "peak", "shoulder")):
        return "energy"
    return "other"


def time_band_from_label(label: str) -> str:
    l = (label or "").lower()
    if "critical" in l and "minimum" in l: return "critical_minimum"
    if "dynamic" in l and "minimum" in l: return "dynamic_minimum"
    if "dynamic" in l and "maximum" in l: return "dynamic_maximum"
    if "super off" in l: return "super_offpeak"
    if "off-peak" in l or "off peak" in l or "offpeak" in l or re.search(r"\bopk\b", l): return "offpeak"
    if "shoulder" in l: return "shoulder"
    if "solar soak" in l or "solar sponge" in l or "saver" in l or "daytime" in l: return "solar_soak"
    if "critical" in l: return "critical_peak"
    m = re.search(r"\bblock\s*(\d+)\b", l)
    if m: return f"block{m.group(1)}"
    if "1st block" in l or "first block" in l: return "block1"
    if "2nd block" in l or "second block" in l: return "block2"
    if "peak" in l or "real capacity" in l or "app. capacity" in l or re.search(r"\bpk\b", l): return "peak"
    if "non-tou" in l: return "anytime"
    if "anytime" in l or "any time" in l or "all " in l or "unit rate" in l or l.strip() in ("energy", "usage", "energy charge", "volume", "volume charge", "net", "net energy", "net energy consumption"): return "anytime"
    return ""


def season_from_label(label: str) -> str:
    l = (label or "").lower()
    if "dec-mar" in l: return "summer"
    if "apr-nov" in l: return "non_summer"
    if "high season" in l or "highsn" in l or "on-season" in l or "on season" in l or re.search(r"\bhs\b", l): return "high"
    if "low season" in l or "lowsn" in l or "off-season" in l or "off season" in l or re.search(r"\bls\b", l): return "low"
    if "non-summer" in l or "non summer" in l or re.search(r"\bnon[- ]sum\.", l): return "non_summer"
    if "summer" in l or re.search(r"\bsum\.", l): return "summer"
    if "winter" in l: return "winter"
    return ""


def export_direction(label, value):
    l = (label or "").lower()
    if any(word in l for word in ("reward", "rebate", "credit", "feed in", "feed-in")):
        return "reward"
    if "charge" in l.split(" - ")[-1]:
        return "charge"
    return "reward" if float(value) < 0 else "charge"
