"""Network bill calculator over the tariff database (data/tariffdb/tables): bill a site's interval data on a tariff,
label each interval with the time-of-use period it falls in, and compare tariffs on the same data.

  .venv/bin/python scripts/billcalc.py bill sapn RSR 2024-07-01 2025-06-30 intervals.csv [--meter-type ...]
  .venv/bin/python scripts/billcalc.py categorise sapn RTOU 2025-07-01 2025-07-31 intervals.csv
  .venv/bin/python scripts/billcalc.py compare sapn RSR,RTOU 2025-07-01 2026-06-30 intervals.csv
  .venv/bin/python scripts/billcalc.py sweep [--write]   every tariff-period on one synthetic month; --write records
                                                         the counts in tests/billcalc_sweep.json

Interval data: a CSV (or DataFrame) indexed by the interval START in NEM time (AEST, UTC+10 all year, as NEM12 stores
it), with kWh columns E1 = general import, E2 = controlled-load import (optional), B1 = export (optional) and Q1 = kvarh
(optional; needed for kVA demand). Synthetic or anonymised data only.

Every bill says how far to trust it (Bill.status):
  exact     every number comes from the database with no assumption;
  assumed   a number comes out, but on a rule the database does not state (each one is in Bill.issues);
  blocked   the database lacks something the bill needs (e.g. TOU rates with no windows): the bill leaves it out.
Site facts the database cannot hold (meter type, opt-ins, agreed kVA, pricing zone) are inputs (Site); a missing one
is reported as `input` and never counted against the database.

Rules the calculator applies, all stated in the database except where an issue says otherwise:
  - prices are GST exclusive; GST is 10% from 1 July 2000 (A New Tax System (Goods and Services Tax) Act 1999);
  - a rate with a condition is charged only when the Site meets it (rate.condition);
  - a rate joins its windows as scripts/tariffdb/joins.py says; window times are local clock times in the distributor's
    time zone (distributor.iana_timezone) unless the window says standard_time; an interval belongs to the window its
    start falls in;
  - public holidays come from the `holidays` package for the distributor's state (the database has no calendar);
  - demand and export quantities are measured as charge_rule states; without a rule, the highest 30-minute demand of
    each calendar month (issue demand_rule_absent).
"""
import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tariffdb"))
import joins  # noqa: E402

ROOT = os.path.dirname(HERE)
TABLES = os.path.join(ROOT, "data", "tariffdb", "tables")
SWEEP_FILE = os.path.join(ROOT, "tests", "billcalc_sweep.json")
NEM = timezone(timedelta(hours=10))
GST_START = date(2000, 7, 1)
KEY = ["distributor_id", "tariff_code", "effective_from"]
REGISTER_COLUMN = {"general": "E1", "controlled_load": "E2", "export": "B1"}

# issue code -> severity. blocked: the database lacks what the bill needs; assumed: a number comes out on a rule the
# database does not state; input: a site fact the caller did not give; note: worth knowing, no effect on trust
ISSUES = {
    "no_tariff": "blocked",
    "dates_not_covered": "blocked",
    "no_rates": "blocked",
    "tou_rates_without_windows": "blocked",
    "season_months_unknown": "blocked",
    "block_bounds_missing": "blocked",
    "unit_unclear": "blocked",
    "unit_unhandled": "blocked",
    "ambiguous_usage": "blocked",
    "other_charge": "blocked",
    "window_months_not_stated": "blocked",
    "demand_rule_absent": "assumed",
    "time_basis_not_stated": "assumed",
    "time_basis_daylight": "assumed",
    "public_holiday_rule_not_stated": "assumed",
    "block_reset_assumed": "assumed",
    "partial_month": "assumed",
    "rolling_history_short": "assumed",
    "annual_demand_prorated": "assumed",
    "window_gap": "assumed",
    "provisional_rates": "note",
    "region_needed": "input",
    "agreed_demand_needed": "input",
    "reactive_energy_needed": "input",
    "meter_type_needed": "input",
}


@dataclass(frozen=True)
class Site:
    """Facts about the site the database cannot hold."""
    meter_type: str = None          # interval, smart, basic, accumulation ... (rate.condition meter_type:<t>)
    opt_in: frozenset = frozenset()  # opt-in names the customer joined (rate.condition opt_in:<name>)
    agreed_kva: float = None        # agreed / contracted demand, for capacity and agreed-demand charges
    region: str = None              # pricing zone, for codes priced by zone


@dataclass
class Bill:
    distributor_id: str
    tariff_code: str
    start: date
    end: date
    lines: list = field(default_factory=list)
    issues: dict = field(default_factory=dict)  # code -> [details]

    def add(self, period, kind, label, qty, unit, rate_c, gst):
        amount = float(qty) * float(rate_c) / 100
        self.lines.append({"period": period, "kind": kind, "label": label, "quantity": round(float(qty), 6),
                           "unit": unit, "rate_c": float(rate_c), "ex_gst": amount, "gst": amount * gst})

    def flag(self, code, detail=""):
        if code not in ISSUES:
            raise KeyError(code)
        self.issues.setdefault(code, [])
        if detail not in self.issues[code]:
            self.issues[code].append(detail)

    @property
    def ex_gst(self):
        return sum(x["ex_gst"] for x in self.lines)

    @property
    def inc_gst(self):
        return sum(x["ex_gst"] + x["gst"] for x in self.lines)

    @property
    def status(self):
        kinds = {ISSUES[c] for c in self.issues}
        return "blocked" if "blocked" in kinds else "assumed" if "assumed" in kinds else \
            "input" if "input" in kinds else "exact"

    def table(self):
        return pd.DataFrame(self.lines)


# ---------------------------------------------------------------------------------------------------------- the data
class Db:
    """The tables, grouped by tariff-period."""

    def __init__(self, tables=TABLES):
        read = lambda t: pd.read_csv(os.path.join(tables, f"{t}.csv"), dtype=str, keep_default_na=False)  # noqa: E731
        self.tariff = read("tariff")
        self.distributor = read("distributor").set_index("distributor_id")
        rate = read("rate")
        rate["value"] = rate["value"].astype(float)
        self.rates = self._group(rate)
        self.windows = self._group(read("tou_window"))
        self.rules = self._group(read("charge_rule"))

    @staticmethod
    def _group(df):
        records = [{k: (None if v == "" else v) for k, v in r.items()} for r in df.to_dict("records")]
        out = {}
        for r in records:
            out.setdefault((r["distributor_id"], r["tariff_code"], r["effective_from"]), []).append(r)
        return out

    def periods(self, did, code, start, end):
        t = self.tariff
        m = (t.distributor_id == did) & (t.tariff_code == code) & (t.effective_from <= str(end)) & \
            (t.effective_to >= str(start))
        return t[m].sort_values("effective_from").to_dict("records")


@lru_cache(maxsize=1)
def default_db():
    return Db()


# ------------------------------------------------------------------------------------------------------- time handling
@lru_cache(maxsize=256)
def holiday_dates(state, years):
    return frozenset(holidays.AU(subdiv=state, years=list(years)).keys())


class Clock:
    """Each interval's NEM date, local clock time, local standard time, weekday and public-holiday flags."""

    def __init__(self, index, tz_name, state):
        tz = ZoneInfo(tz_name)
        utc = index.tz_localize(NEM).tz_convert("UTC")
        local = utc.tz_convert(tz).tz_localize(None)
        std_offset = tz.utcoffset(datetime(2025, 6, 15))  # June: no Australian zone is on daylight time
        standard = utc.tz_localize(None) + pd.Timedelta(std_offset)
        self.nem_date = np.array(index.date)
        self.clocks = {}
        for name, ts in (("local", local), ("standard", standard)):
            ph = holiday_dates(state, tuple(sorted(set(ts.year))))
            self.clocks[name] = {
                "minute": np.asarray(ts.hour * 60 + ts.minute), "month": np.asarray(ts.month),
                "weekday": np.asarray(ts.weekday < 5), "holiday": np.isin(np.array(ts.date), list(ph)),
            }
        self.month = np.asarray(local.month)
        self.n = len(index)


def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def window_mask(clock, w, holiday_rule=None):
    """Intervals whose start falls in window w."""
    c = clock.clocks["standard" if w["time_basis"] == "standard_time" else "local"]
    rule = holiday_rule or w["public_holidays"]
    wd, ph = c["weekday"], c["holiday"]
    as_non_business = rule in ("as_non_business_day", "not_stated")
    days = {"all_days": np.ones(clock.n, bool),
            "weekday": wd & ~(ph & (rule == "as_non_business_day")),
            "weekend": ~wd | (ph & (rule == "as_non_business_day")),
            "business_day": wd & ~ph if as_non_business else wd,
            "non_business_day": ~wd | ph if as_non_business else ~wd}[w["day_type"]]
    months = [int(m) for m in w["months"].split(",")] if w["months"] else None
    in_months = np.isin(c["month"], months) if months else np.zeros(clock.n, bool)
    t = c["minute"]
    return days & in_months & (t >= minutes(w["start_time"])) & (t < minutes(w["end_time"]))


def windows_mask(clock, ws, bill, period):
    """Intervals in any of the windows ws, flagging what the windows leave unstated."""
    mask = np.zeros(clock.n, bool)
    for w in ws:
        if not w["months"]:
            bill.flag("window_months_not_stated", f"{period}: {w['window_id']}")
            continue
        if w["time_basis"] == "not_stated":
            bill.flag("time_basis_not_stated", f"{period}: {w['window_id']}")
        elif w["time_basis"] == "daylight_time":
            bill.flag("time_basis_daylight", f"{period}: {w['window_id']} read as local clock time")
        m = window_mask(clock, w)
        if w["public_holidays"] == "not_stated" and w["day_type"] != "all_days":
            alt = window_mask(clock, w, "as_weekday")
            if (alt != m).any():
                bill.flag("public_holiday_rule_not_stated",
                          f"{period}: {w['window_id']}: public holidays read as non-business days")
        mask |= m
    return mask


def season_mask(clock, season, ws, bill, period, what):
    if season is None:
        return np.ones(clock.n, bool)
    months = joins.season_months(season, ws)
    if months is None:
        bill.flag("season_months_unknown", f"{period}: {what} season {season}")
        return None
    return np.isin(clock.month, sorted(months))


# ------------------------------------------------------------------------------------------------------------- billing
def gst_rate(day):
    return 0.10 if day >= GST_START else 0.0


def unit_parts(unit):
    """('kW', 'month') from 'c/kW/month'; ('kWh', None) from 'c/kWh'."""
    parts = unit.split("/")
    return parts[1], parts[2] if len(parts) > 2 else None


def applies(rate, site, bill, period):
    cond = rate["condition"]
    if cond is None:
        return True
    kind, _, value = cond.partition(":")
    if kind == "opt_in":
        return value in site.opt_in
    if kind == "meter_type":
        if site.meter_type is None:
            bill.flag("meter_type_needed", f"{period}: {rate['component']} applies to meter type {value}")
            return False
        return site.meter_type == value
    return False


def bill(did, code, start, end, intervals, site=Site(), db=None):
    """The network bill of one tariff over [start, end] (dates, inclusive) for the interval data."""
    db = db or default_db()
    start, end = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    b = Bill(did, code, start, end)
    periods = db.periods(did, code, start, end)
    if not periods:
        b.flag("no_tariff", f"{did} {code} is not in effect {start}..{end}")
        return b
    dist = db.distributor.loc[did]
    covered = set()
    for t in periods:
        p0 = max(date.fromisoformat(t["effective_from"]), start)
        p1 = min(date.fromisoformat(t["effective_to"]), end)
        covered |= {p0 + timedelta(days=i) for i in range((p1 - p0).days + 1)}
        day = np.array(intervals.index.date)
        iv = intervals[(day >= p0) & (day <= p1)]
        clock = Clock(iv.index, dist["iana_timezone"], dist["state"])
        key = (did, code, t["effective_from"])
        bill_period(b, t, p0, p1, iv, clock, db.rates.get(key, []), db.windows.get(key, []), db.rules.get(key, []),
                    site)
    days = (end - start).days + 1
    if len(covered) < days:
        b.flag("dates_not_covered", f"{days - len(covered)} days have no tariff period")
    return b


def bill_period(b, t, p0, p1, iv, clock, rates, windows, rules, site):
    period = t["effective_from"]
    g = gst_rate(p0)
    days = (p1 - p0).days + 1
    if t["status"] == "provisional":
        b.flag("provisional_rates", f"{period}: AER provisional rates")
    if not rates:
        b.flag("no_rates", f"{period}: the tariff has no rate rows")
    regions = sorted({r["region"] for r in rates if r["region"]})
    if regions and site.region not in regions:
        b.flag("region_needed", f"{period}: priced by zone {regions}")
        region = regions[0]
    else:
        region = site.region
    rates = [r for r in rates if r["region"] in (None, region) and applies(r, site, b, period)]
    for r in rates:
        if r["charge_type"] in ("daily", "metering"):
            if r["unit"] in ("c/day", "c/lamp/day"):
                b.add(period, r["charge_type"], r["component"], days, "day", r["value"], g)
            elif r["unit"] == "c/kWh":
                b.add(period, r["charge_type"], r["component"], iv["E1"].sum(), "kWh", r["value"], g)
            else:
                b.flag("unit_unhandled", f"{period}: {r['charge_type']} in {r['unit']}")
        elif r["charge_type"] == "other":
            b.flag("other_charge", f"{period}: {r['component']}")
    for register in ("general", "controlled_load"):
        bill_usage(b, period, [r for r in rates if r["charge_type"] == "usage" and r["register"] == register],
                   register, iv, clock, windows, g)
    bill_demand(b, period, [r for r in rates if r["charge_type"] in ("demand", "capacity")], iv, clock, windows,
                rules, site, g)
    bill_export(b, period, [r for r in rates if r["charge_type"] == "export"], iv, clock, windows, rules, g)


def quantity(iv, column, rate, b, period):
    """kWh (or kVAh) of a register."""
    if column not in iv:
        return pd.Series(0.0, index=iv.index)
    if rate["unit"] == "c/kVAh":
        if "Q1" not in iv:
            b.flag("reactive_energy_needed", f"{period}: {rate['component']} is priced per kVAh")
            return iv[column]
        return np.sqrt(iv[column] ** 2 + iv["Q1"] ** 2)
    return iv[column]


def rate_mask(b, period, rate, clock, windows, what):
    """Intervals a rate applies in (its period and season), or None when the database cannot say."""
    mask = season_mask(clock, rate["season"], windows, b, period, what)
    if mask is None:
        return None
    if rate["tou_period"] in joins.ALL_TIMES:
        return mask
    ws = joins.rate_windows(rate, windows)
    if not ws:
        b.flag("tou_rates_without_windows", f"{period}: {what} {rate['tou_period']} has no window")
        return None
    return mask & windows_mask(clock, ws, b, period)


def bill_usage(b, period, rates, register, iv, clock, windows, g):
    if not rates:
        return
    column = REGISTER_COLUMN[register]
    blocks = [r for r in rates if r["block"] is not None]
    plain = [r for r in rates if r["block"] is None]
    for r in plain:
        if r["unit"] not in ("c/kWh", "c/kVAh"):
            b.flag("unit_unhandled", f"{period}: usage in {r['unit']}")
            continue
    # rates of one register that no period, season or block tells apart cannot all apply
    seen = {}
    for r in plain:
        k = (r["tou_period"] if r["tou_period"] not in joins.ALL_TIMES else None, r["season"])
        if k in seen:
            b.flag("ambiguous_usage", f"{period}: {register} {seen[k]!r} and {r['component']!r}")
            continue
        seen[k] = r["component"]
        mask = rate_mask(b, period, r, clock, windows, f"{register} usage")
        if mask is None:
            continue
        kwh = quantity(iv, column, r, b, period)
        b.add(period, f"usage:{register}", r["component"], kwh[mask].sum(), r["unit"][2:], r["value"], g)
    if blocks:
        bill_blocks(b, period, blocks, register, iv, clock, windows, g)
    if plain and not any(r["tou_period"] in joins.ALL_TIMES and r["season"] is None for r in plain) and not blocks:
        covered = np.zeros(clock.n, bool)
        for r in plain:
            m = rate_mask(Bill(b.distributor_id, b.tariff_code, b.start, b.end), period, r, clock, windows, "")
            if m is not None:
                covered |= m
        if column in iv and (~covered & (iv[column].to_numpy() > 0)).any():
            b.flag("window_gap", f"{period}: {register} kWh outside every priced window")


def bill_blocks(b, period, blocks, register, iv, clock, windows, g):
    column = REGISTER_COLUMN[register]
    ladders = {}
    for r in blocks:
        ladders.setdefault((r["tou_period"], r["season"]), []).append(r)
    for (_, _), ladder in ladders.items():
        ladder.sort(key=lambda r: int(r["block"]))
        mask = rate_mask(b, period, ladder[0], clock, windows, f"{register} block usage")
        if mask is None:
            continue
        kwh = (iv[column] if column in iv else pd.Series(0.0, index=iv.index))[mask]
        if len(ladder) == 1:
            r = ladder[0]
            b.add(period, f"usage:{register}", r["component"], kwh.sum(), "kWh", r["value"], g)
            continue
        if any(r["block_from"] is None for r in ladder):
            b.flag("block_bounds_missing", f"{period}: {register} blocks {[r['component'] for r in ladder]}")
            continue
        unit = ladder[0]["block_unit"]
        day = pd.Index(kwh.index.date)
        if unit == "kWh/day":
            spans = [(kwh.groupby(day).sum(), 1)]
        elif unit == "kWh/billing_day":
            n = len(set(day))
            spans = [(pd.Series([kwh.sum()]), n)]
        elif unit == "kWh/quarter":
            spans = [(kwh.groupby(kwh.index.to_period("Q")).sum(), 1)]
            b.flag("block_reset_assumed", f"{period}: kWh/quarter blocks by calendar quarter, part quarters not "
                                          f"prorated")
        else:
            spans = [(pd.Series([kwh.sum()]), 1)]
            b.flag("block_reset_assumed", f"{period}: block bounds with no reset period: one ladder for the bill")
        for totals, scale in spans:
            for r in ladder:
                lo = float(r["block_from"]) * scale
                hi = float(r["block_to"]) * scale if r["block_to"] is not None else np.inf
                q = (totals.clip(lower=lo, upper=hi) - lo).sum()
                b.add(period, f"usage:{register}", r["component"], q, "kWh", r["value"], g)


def find_rule(rules, rate):
    """The charge_rule for a rate: the most specific of (tou_period, season), (tou_period), (season), (all)."""
    best = None
    for r in rules:
        if r["charge_type"] != rate["charge_type"]:
            continue
        if r["tou_period"] not in (None, rate["tou_period"]) or r["season"] not in (None, rate["season"]):
            continue
        score = (r["tou_period"] is not None) * 2 + (r["season"] is not None)
        if best is None or score > best[0]:
            best = (score, r)
    return best[1] if best else None


def demand_series(iv, measure, interval_min, b, period, component):
    kw = iv["E1"] * 60 / interval_min if "E1" in iv else pd.Series(0.0, index=iv.index)
    if measure == "kVA":
        if "Q1" not in iv:
            b.flag("reactive_energy_needed", f"{period}: {component} is measured in kVA")
            return kw
        return np.sqrt(iv["E1"] ** 2 + iv["Q1"] ** 2) * 60 / interval_min
    return kw


def interval_minutes(iv):
    if len(iv.index) < 2:
        return 30
    return int((iv.index[1] - iv.index[0]).total_seconds() // 60)


def bill_demand(b, period, rates, iv, clock, windows, rules, site, g):
    data_min = interval_minutes(iv)
    for r in rates:
        q, per = unit_parts(r["unit"])
        if "?" in r["unit"]:
            b.flag("unit_unclear", f"{period}: {r['component']} in {r['unit']}")
            continue
        rule = find_rule(rules, r)
        if rule is None:
            b.flag("demand_rule_absent", f"{period}: {r['component']}: highest 30-minute demand per calendar month")
            rule = {"measure": q, "interval_min": "30", "method": "max", "n": None, "reset": "month",
                    "minimum_value": None, "threshold_value": None}
        mask = rate_mask(b, period, r, clock, windows, "demand")
        if mask is None:
            continue
        method = rule["method"]
        if method in ("agreed", "max_of_agreed_and_measured") and site.agreed_kva is None:
            b.flag("agreed_demand_needed", f"{period}: {r['component']}")
            continue
        interval_min = int(rule["interval_min"] or data_min)
        series = demand_series(iv, rule["measure"], data_min, b, period, r["component"])
        if interval_min != data_min:
            series = series.resample(f"{interval_min}min").mean()
            mask = pd.Series(mask, index=iv.index).resample(f"{interval_min}min").max().to_numpy()
        series = series.where(mask, np.nan)
        for label, idx, days, full_days in reset_spans(series.index, rule["reset"], b, period, r):
            s = series[idx].dropna()
            if method == "agreed":
                value = site.agreed_kva
            else:
                value = measured(s, method, rule["n"])
                if method == "max_of_agreed_and_measured":
                    value = max(value, site.agreed_kva)
            if rule["minimum_value"] is not None:
                value = max(value, float(rule["minimum_value"]))
            if rule["threshold_value"] is not None:
                value = max(0.0, value - float(rule["threshold_value"]))
            if per == "day":
                qty, unit = value * days, f"{q}·day"
            elif per == "month":
                qty, unit = value, f"{q}·month"
                if days < full_days:
                    b.flag("partial_month", f"{period}: {label} has {days} of {full_days} days; a full month charged")
            elif per == "year":
                qty, unit = value * days / 365, f"{q}·year"
                b.flag("annual_demand_prorated", f"{period}: {r['component']} charged by days / 365")
            else:
                b.flag("unit_unhandled", f"{period}: demand in {r['unit']}")
                break
            b.add(period, r["charge_type"], f"{r['component']} {label}", qty, unit, r["value"], g)


def measured(s, method, n):
    if s.empty:
        return 0.0
    if method in ("max", "max_of_agreed_and_measured"):
        return float(s.max())
    n = int(n)
    if method == "avg_top_n_intervals":
        return float(s.nlargest(n).mean())
    daily = s.groupby(s.index.date).max()
    return float(daily.nlargest(n).mean())


def reset_spans(index, reset, b, period, rate):
    """(label, boolean index, days in the span, days in a full span) per span the measured value restarts on."""
    day = pd.Index(index.date)
    if reset in ("month", "billing_period") or reset not in ("day", "season", "year", "rolling_12_months"):
        if reset == "billing_period":
            months = [("billing period", np.ones(len(index), bool))]
        else:
            months = [(str(p), np.asarray(index.to_period("M") == p)) for p in sorted(set(index.to_period("M")))]
        for label, idx in months:
            days = len(set(day[idx]))
            full = days if reset == "billing_period" else pd.Period(label).days_in_month
            yield label, idx, days, full
        return
    if reset == "day":
        for d in sorted(set(day)):
            idx = np.asarray(day == d)
            yield str(d), idx, 1, 1
        return
    # season, year, rolling 12 months: the highest value since the span started; with only this bill's data the
    # span is cut to the data held
    b.flag("rolling_history_short", f"{period}: {rate['component']} resets by {reset}; only this bill's data is held")
    for p in sorted(set(index.to_period("M"))):
        idx_month = np.asarray(index.to_period("M") == p)
        upto = np.asarray(index.to_period("M") <= p)
        yield str(p), upto, len(set(day[idx_month])), p.days_in_month


def bill_export(b, period, rates, iv, clock, windows, rules, g):
    for r in rates:
        q, per = unit_parts(r["unit"])
        mask = rate_mask(b, period, r, clock, windows, "export")
        if mask is None:
            continue
        exported = (iv["B1"] if "B1" in iv else pd.Series(0.0, index=iv.index))
        rule = find_rule(rules, r)
        if q in ("kWh", "kVAh"):
            kwh = exported.where(mask, 0.0)
            if rule and rule["allowance_per_day"] is not None:
                allowance = float(rule["allowance_per_day"])
                daily = kwh.groupby(kwh.index.date).sum()
                if rule["allowance_rollover"] == "1":
                    total = max(0.0, daily.sum() - allowance * len(daily))
                else:
                    total = (daily - allowance).clip(lower=0).sum()
            else:
                total = kwh.sum()
            b.add(period, "export", r["component"], total, "kWh", r["value"], g)
        elif q in ("kW", "kVA") and per in ("month", "day"):
            kw = exported * 60 / interval_minutes(iv)
            threshold = float(rule["threshold_value"]) if rule and rule["threshold_value"] is not None else 0.0
            if rule is None:
                b.flag("demand_rule_absent", f"{period}: {r['component']}: highest export interval per month")
            kw = kw.where(mask, 0.0)
            for p in sorted(set(kw.index.to_period("M"))):
                peak = max(0.0, float(kw[kw.index.to_period("M") == p].max()) - threshold)
                days = len(set(kw[kw.index.to_period("M") == p].index.date))
                qty = peak if per == "month" else peak * days
                b.add(period, "export", f"{r['component']} {p}", qty, f"{q}·{per}", r["value"], g)
        else:
            b.flag("unit_unhandled", f"{period}: export in {r['unit']}")


# ------------------------------------------------------------------------------------------------- categorise, compare
def categorise(did, code, start, end, intervals, db=None):
    """One row per interval: the tariff period in force, the season, and the TOU period of each charge group."""
    db = db or default_db()
    start, end = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    dist = db.distributor.loc[did]
    out = pd.DataFrame(index=intervals.index)
    out["effective_from"] = None
    for group in ("usage", "controlled_load", "demand", "export"):
        out[group] = None
    out["season"] = None
    for t in db.periods(did, code, start, end):
        p0 = max(date.fromisoformat(t["effective_from"]), start)
        p1 = min(date.fromisoformat(t["effective_to"]), end)
        day = np.array(intervals.index.date)
        sel = (day >= p0) & (day <= p1)
        clock = Clock(intervals.index[sel], dist["iana_timezone"], dist["state"])
        windows = db.windows.get((did, code, t["effective_from"]), [])
        out.loc[sel, "effective_from"] = t["effective_from"]
        for w in sorted(windows, key=lambda w: w["window_id"]):
            if not w["months"]:
                continue
            m = window_mask(clock, w)
            groups = ["usage", "controlled_load", "demand", "export"] if w["applies_to"] == "all" else [w["applies_to"]]
            for gname in groups:
                col = out.loc[sel, gname].to_numpy()
                col[m] = w["tou_period"]
                out.loc[sel, gname] = col
            if w["season"]:
                col = out.loc[sel, "season"].to_numpy()
                col[m] = w["season"]
                out.loc[sel, "season"] = col
    return out


def compare(did, codes, start, end, intervals, site=Site(), db=None):
    """One row per tariff: the bill ex and inc GST, and how far to trust it."""
    rows = []
    for code in codes:
        bl = bill(did, code, start, end, intervals, site, db)
        rows.append({"tariff_code": code, "ex_gst": round(bl.ex_gst, 2), "inc_gst": round(bl.inc_gst, 2),
                     "status": bl.status, "issues": ", ".join(sorted(bl.issues))})
    return pd.DataFrame(rows).sort_values("inc_gst")


# -------------------------------------------------------------------------------------------------------------- sweep
def synthetic_month(start, seed):
    """A deterministic synthetic month of half-hour data from `start`: import shaped by time of day, controlled load
    overnight, export at midday, reactive energy at 0.3 x import. Not any real site's data."""
    idx = pd.date_range(pd.Timestamp(start), periods=48 * 28, freq="30min")
    rng = np.random.default_rng(seed)
    hour = idx.hour + idx.minute / 60
    shape = 0.3 + 0.5 * np.exp(-((hour - 18) ** 2) / 8) + 0.3 * np.exp(-((hour - 8) ** 2) / 4)
    e1 = shape * rng.uniform(0.8, 1.2, len(idx))
    e2 = np.where((hour >= 22) | (hour < 6), 0.6, 0.0)
    b1 = np.clip(np.sin((hour - 6) / 12 * np.pi), 0, None) * 1.2
    return pd.DataFrame({"E1": e1, "E2": e2, "B1": b1, "Q1": 0.3 * e1}, index=idx)


def sweep(db=None):
    """Bill every tariff-period on 28 synthetic days from its first day, with every site input given (so only what
    the database lacks or leaves unstated is counted). Returns per tariff-period (key, status, issue codes)."""
    db = db or default_db()
    out = []
    for t in db.tariff.sort_values(KEY).to_dict("records"):
        key = (t["distributor_id"], t["tariff_code"], t["effective_from"])
        start = date.fromisoformat(t["effective_from"])
        end = min(start + timedelta(days=27), date.fromisoformat(t["effective_to"]))
        seed = int(hashlib.sha256(":".join(key).encode()).hexdigest()[:8], 16)
        iv = synthetic_month(start, seed)
        rates = db.rates.get(key, [])
        regions = sorted({r["region"] for r in rates if r["region"]})
        site = Site(meter_type=None, agreed_kva=50.0, region=regions[0] if regions else None)
        bl = bill(*key, start, end, iv, site, db)
        issues = sorted(c for c in bl.issues if ISSUES[c] in ("blocked", "assumed"))
        status = "blocked" if any(ISSUES[c] == "blocked" for c in issues) else "assumed" if issues else "exact"
        out.append({"key": ":".join(key), "status": status, "issues": issues})
    return out


def sweep_counts(results):
    """{status: n, 'issue:<code>': n}: tariff-periods per status and per issue."""
    counts = {s: 0 for s in ("exact", "assumed", "blocked")}
    for r in results:
        counts[r["status"]] += 1
        for c in r["issues"]:
            counts[f"issue:{c}"] = counts.get(f"issue:{c}", 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------------------------------------------- CLI
def read_intervals(path):
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return df.astype(float)


def site_args(a):
    return Site(meter_type=a.meter_type, opt_in=frozenset(a.opt_in or ()), agreed_kva=a.agreed_kva, region=a.region)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("bill", "categorise", "compare"):
        p = sub.add_parser(name)
        p.add_argument("distributor")
        p.add_argument("code", help="tariff code (compare: comma-separated codes)")
        p.add_argument("start")
        p.add_argument("end")
        p.add_argument("intervals", help="CSV: interval start (NEM time), E1[, E2, B1, Q1] kWh")
        p.add_argument("--meter-type")
        p.add_argument("--opt-in", action="append")
        p.add_argument("--agreed-kva", type=float)
        p.add_argument("--region")
    p = sub.add_parser("sweep")
    p.add_argument("--write", action="store_true", help=f"record the counts in {os.path.relpath(SWEEP_FILE, ROOT)}")
    a = ap.parse_args(argv)
    pd.set_option("display.width", 200)
    if a.cmd == "sweep":
        counts = sweep_counts(sweep())
        print(json.dumps(counts, indent=1))
        if a.write:
            with open(SWEEP_FILE, "w", encoding="utf-8") as f:
                json.dump(counts, f, indent=1)
                f.write("\n")
        return 0
    iv = read_intervals(a.intervals)
    if a.cmd == "bill":
        bl = bill(a.distributor, a.code, a.start, a.end, iv, site_args(a))
        print(bl.table().round(4).to_string(index=False))
        print(f"total ex GST {bl.ex_gst:.2f}  inc GST {bl.inc_gst:.2f}  status {bl.status}")
        for c, details in sorted(bl.issues.items()):
            print(f"  {ISSUES[c]:8} {c}: {details[0]}" + (f" (+{len(details) - 1} more)" if len(details) > 1 else ""))
    elif a.cmd == "categorise":
        print(categorise(a.distributor, a.code, a.start, a.end, iv).to_csv())
    else:
        print(compare(a.distributor, a.code.split(","), a.start, a.end, iv, site_args(a)).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
