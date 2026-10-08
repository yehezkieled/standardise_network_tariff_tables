"""Tests of the network bill calculator (scripts/billcalc.py) on the tariff database.

  .venv/bin/python -m unittest tests/test_billcalc.py

The fixtures are the worked bills the distributors publish. Each profile is synthetic: built from the window wording the
same document prints (quoted below), not from tou_window, so a match also checks the stored windows and the time
handling. No client data.
"""
import json
import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import billcalc as bc  # noqa: E402

ADELAIDE, MELBOURNE = "Australia/Adelaide", "Australia/Melbourne"
FY23 = (date(2023, 7, 1), date(2024, 6, 30))
FY24 = (date(2024, 7, 1), date(2025, 6, 30))
FY25 = (date(2025, 7, 1), date(2026, 6, 30))
SAPN_2025 = "sources/dnsp/sapn/SAPN_Initial_Pricing_Proposal_Overview_2025-26_id333252.pdf"
CITIPOWER_2023 = "sources/dnsp/citipower/CitiPower_Pricing_Proposal_2023-24_wayback.pdf"


def hour(t):
    return t.hour + t.minute / 60


def everywhere(t):
    return True


def profile(span, tz, total, buckets, cl_total=0.0, cl_hours=None):
    """Half-hour kWh over the span: buckets = [(share of total, rule(local time) -> bool)], each share spread evenly
    over the intervals its rule takes first; controlled-load kWh spread evenly over cl_hours."""
    idx = pd.date_range(f"{span[0]} 00:00", f"{span[1]} 23:30", freq="30min")
    local = pd.Series(idx.tz_localize(bc.NEM).tz_convert(tz).tz_localize(None), index=idx)
    e1 = pd.Series(0.0, index=idx)
    taken = pd.Series(False, index=idx)
    for share, rule in buckets:
        m = local.map(rule) & ~taken
        e1[m] = total * share / m.sum()
        taken |= m
    assert taken.all(), "the buckets must cover every interval"
    e2 = pd.Series(0.0, index=idx)
    if cl_total:
        m = local.map(cl_hours)
        e2[m] = cl_total / m.sum()
    return pd.DataFrame({"E1": e1, "E2": e2, "B1": 0.0})


def sa_night(t):  # where the controlled-load kWh go; the CL price is single rate
    return hour(t) >= 23 or hour(t) < 7


def summer(t):
    return t.month in (11, 12, 1, 2, 3)


def weekday(t):
    return t.weekday() < 5


# (name, distributor, code, span, profile, published $, basis, $ outside the database, source)
CASES = [
    # CitiPower 2023-24 Pricing Proposal p5 Figure 2, GST exclusive: single rate $451 and TOU $443 for 4,000 kWh a
    # year, each including $66 of metering, which Victoria prices outside the network tariff (not in the database);
    # TOU: '31.9% peak and 68.1% off peak', CRTOU peak '3pm-9pm' every day
    ("CitiPower C1R 2023-24", "citipower", "C1R", FY23, lambda: profile(FY23, MELBOURNE, 4000, [(1.0, everywhere)]),
     451, "ex_gst", 66, CITIPOWER_2023 + " p5"),
    ("CitiPower CRTOU 2023-24", "citipower", "CRTOU", FY23,
     lambda: profile(FY23, MELBOURNE, 4000, [(0.319, lambda t: 15 <= hour(t) < 21), (0.681, everywhere)]),
     443, "ex_gst", 66, CITIPOWER_2023 + " p5"),
    # SA Power Networks 2025-26 Initial Pricing Proposal Overview section 3, GST inclusive, metering included
    ("SAPN RSR 2024-25", "sapn", "RSR", FY24, lambda: profile(FY24, ADELAIDE, 4000, [(1.0, everywhere)]),
     922, "inc_gst", 0, SAPN_2025 + " p33 Figure 16"),
    ("SAPN RSR + CL 2024-25", "sapn", "RSR", FY24,
     lambda: profile(FY24, ADELAIDE, 4200, [(1.0, everywhere)], 1800, sa_night),
     1105, "inc_gst", 0, SAPN_2025 + " p34 Figure 17"),
    ("SAPN BSR 2024-25", "sapn", "BSR", FY24, lambda: profile(FY24, ADELAIDE, 10000, [(1.0, everywhere)]),
     2206, "inc_gst", 0, SAPN_2025 + " p34 Figure 18"),
    ("SAPN RSR 2025-26", "sapn", "RSR", FY25, lambda: profile(FY25, ADELAIDE, 4000, [(1.0, everywhere)]),
     897, "inc_gst", 0, SAPN_2025 + " p33 Figure 16"),
    ("SAPN RSR + CL 2025-26", "sapn", "RSR", FY25,
     lambda: profile(FY25, ADELAIDE, 4200, [(1.0, everywhere)], 1800, sa_night),
     1073, "inc_gst", 0, SAPN_2025 + " p34 Figure 17"),
    ("SAPN BSR 2025-26", "sapn", "BSR", FY25, lambda: profile(FY25, ADELAIDE, 10000, [(1.0, everywhere)]),
     2282, "inc_gst", 0, SAPN_2025 + " p34 Figure 18"),
    # RTOU (p13): 'Off Peak 12:00am-6:00am', 'Solar Sponge 10:00am-4:00pm', peak the other 12 hours; 55/19/26%
    ("SAPN RTOU 2025-26", "sapn", "RTOU", FY25,
     lambda: profile(FY25, ADELAIDE, 4476, [(0.19, lambda t: hour(t) < 6), (0.26, lambda t: 10 <= hour(t) < 16),
                                            (0.55, everywhere)]),
     921, "inc_gst", 0, SAPN_2025 + " p35 Figure 20"),
    # SBTOU (p21): 'Peak 5:00pm-9:00pm All days November-March', 'Shoulder 7:00am-5:00pm WD November-March and
    # 7:00am-9:00pm WD April-October', 'Off Peak all other times'; 7/48/45%
    ("SAPN SBTOU 2025-26", "sapn", "SBTOU", FY25,
     lambda: profile(FY25, ADELAIDE, 12319, [
         (0.07, lambda t: summer(t) and 17 <= hour(t) < 21),
         (0.48, lambda t: weekday(t) and ((summer(t) and 7 <= hour(t) < 17) or (not summer(t) and 7 <= hour(t) < 21))),
         (0.45, everywhere)]),
     2402, "inc_gst", 0, SAPN_2025 + " p35 Figure 22"),
]
# a published bill rounds to whole dollars; the profile shares are rounded to the percent the document prints
TOLERANCE = 0.50
# the published SAPN bills include SA Power Networks' legacy metering charge (a meter SAPN installed before July 2015)
# and no opt-in
SITE = {"citipower": bc.Site(meter_type="interval"),
        "sapn": bc.Site(meter_type="accumulation", meter_class="network_meter_before_july_2015")}


class TestPublishedBills(unittest.TestCase):
    def test_every_published_bill(self):
        for name, did, code, span, make, published, basis, outside, ref in CASES:
            with self.subTest(name):
                bill = bc.bill(did, code, *span, make(), SITE[did])
                got = (bill.ex_gst if basis == "ex_gst" else bill.inc_gst) + outside
                self.assertLessEqual(abs(got - published), TOLERANCE, f"{name}: {got:.2f} vs {published} ({ref})\n"
                                     f"{bill.table()}\n{bill.issues}")
                self.assertIn(bill.status, ("exact",), f"{name}: {bill.issues}")


class TestRules(unittest.TestCase):
    def test_opt_in_rebate_needs_the_opt_in(self):
        iv = profile(FY24, ADELAIDE, 4000, [(1.0, everywhere)])
        plain = bc.bill("sapn", "RSR", *FY24, iv, SITE["sapn"])
        site = bc.Site(meter_type="accumulation", meter_class="network_meter_before_july_2015",
                       opt_in=frozenset({"diversify"}))
        joined = bc.bill("sapn", "RSR", *FY24, iv, site)
        self.assertAlmostEqual(plain.ex_gst - joined.ex_gst, 0.33 * 365, places=6)

    def test_gst_starts_1_july_2000(self):
        self.assertEqual(bc.gst_rate(date(2000, 6, 30)), 0.0)
        self.assertEqual(bc.gst_rate(date(2000, 7, 1)), 0.10)

    def test_unknown_tariff_is_blocked(self):
        b = bc.bill("sapn", "NO-SUCH", *FY24, profile(FY24, ADELAIDE, 1, [(1.0, everywhere)]))
        self.assertEqual(b.status, "blocked")

    def test_dst_moves_a_local_window_in_nem_time(self):
        """Jemena's peak is 3-9 pm local time: on a daylight-saving day it starts at 2 pm NEM time."""
        idx = pd.date_range("2025-10-06 00:00", "2025-10-06 23:30", freq="30min")
        clock = bc.Clock(idx, "Australia/Melbourne", "VIC")
        w = {"time_basis": "local_time", "public_holidays": "unchanged", "day_type": "all_days", "months": "10",
             "start_time": "15:00", "end_time": "21:00"}
        hours = sorted({t.hour for t in idx[bc.window_mask(clock, w)]})
        self.assertEqual(hours, list(range(14, 20)))
        w["time_basis"] = "standard_time"
        self.assertEqual(sorted({t.hour for t in idx[bc.window_mask(clock, w)]}), list(range(15, 21)))

    def test_categorise_labels_every_interval(self):
        iv = profile((date(2025, 7, 1), date(2025, 7, 7)), ADELAIDE, 10, [(1.0, everywhere)])
        cats = bc.categorise("sapn", "RTOU", date(2025, 7, 1), date(2025, 7, 7), iv)
        self.assertEqual(len(cats), len(iv))
        self.assertTrue(cats["usage"].notna().all())

    def demand_quantity(self, method, tou_period="off_peak", measure="kW", **site):
        """Bill one 'demand' rate of 100 c/kW/month on two days of hand-made 30-minute data: day 1 imports 2 kWh an
        interval except 6 kWh at 17:00 (peak window 16-21) and 4 kWh at 03:00; day 2 imports 1 kWh an interval."""
        idx = pd.date_range("2025-08-01 00:00", "2025-08-02 23:30", freq="30min")
        e1 = pd.Series(2.0, index=idx)
        e1["2025-08-02"] = 1.0
        e1["2025-08-01 17:00"], e1["2025-08-01 03:00"] = 6.0, 4.0
        q1 = pd.Series(0.0, index=idx)
        q1["2025-08-01 03:00"] = 3.0
        iv = pd.DataFrame({"E1": e1, "Q1": q1})
        window = {"applies_to": "demand", "day_type": "all_days", "months": ",".join(map(str, range(1, 13))),
                  "season": None, "time_basis": "local_time", "public_holidays": "unchanged"}
        windows = [dict(window, tou_period="peak", start_time="16:00", end_time="21:00", window_id="p"),
                   dict(window, tou_period="off_peak", start_time="00:00", end_time="16:00", window_id="o1"),
                   dict(window, tou_period="off_peak", start_time="21:00", end_time="24:00", window_id="o2")]
        rate = {"charge_type": "demand", "register": None, "tou_period": tou_period, "season": None, "block": None,
                "unit": f"c/{measure}/month", "value": "100", "component": "Demand", "condition": None}
        rule = {"charge_type": "demand", "tou_period": None, "season": None, "measure": measure, "interval_min": "30",
                "method": method, "n": None, "reset": "month", "minimum_value": None, "threshold_value": None}
        b = bc.Bill("x", "X", date(2025, 8, 1), date(2025, 8, 2))
        clock = bc.Clock(idx, "Australia/Brisbane", "QLD")
        bc.bill_demand(b, "p", [rate], iv, clock, windows, [rule], bc.Site(**site), 0.0)
        return b.lines[0]["quantity"] if b.lines else None, b

    def test_demand_methods(self):
        """kW = kWh x 2 on 30-minute data; kVA = sqrt(kWh^2 + kvarh^2) x 2."""
        self.assertEqual(self.demand_quantity("max")[0], 8.0)  # 03:00 on day 1, outside the peak window
        self.assertEqual(self.demand_quantity("excess_over_window_max")[0], 0.0)  # 8 kW less the 12 kW peak
        self.assertEqual(self.demand_quantity("max_daily_window_mean", tou_period="peak")[0], 12.0 / 10 + 4.0 * 9 / 10)
        self.assertEqual(self.demand_quantity("kva_at_max_kw", measure="kVA")[0], 10.0)  # 4 kWh, 3 kvarh at 03:00
        self.assertEqual(self.demand_quantity("assigned", agreed_kva=50.0)[0], 50.0)
        self.assertEqual(self.demand_quantity("avg_daily_max")[0], (8.0 + 2.0) / 2)
        self.assertEqual(self.demand_quantity("avg_nominated_days", event_days=frozenset({date(2025, 8, 2)}))[0], 2.0)
        _, b = self.demand_quantity("avg_nominated_days")
        self.assertIn("event_days_needed", b.issues)

    def test_billing_period_reset_restarts_each_calendar_month(self):
        """A 'billing_period' demand over two months is measured per month, not over the whole bill."""
        idx = pd.date_range("2025-07-01 00:00", "2025-08-31 23:30", freq="30min")
        e1 = pd.Series(1.0, index=idx)
        e1["2025-07-10 12:00"] = 5.0
        iv = pd.DataFrame({"E1": e1})
        rate = {"charge_type": "demand", "register": None, "tou_period": None, "season": None, "block": None,
                "unit": "c/kW/day", "value": "100", "component": "Demand", "condition": None}
        rule = {"charge_type": "demand", "tou_period": None, "season": None, "measure": "kW", "interval_min": "30",
                "method": "max", "n": None, "reset": "billing_period", "minimum_value": None, "threshold_value": None}
        clock = bc.Clock(idx, "Australia/Sydney", "NSW")
        b = bc.Bill("x", "X", date(2025, 7, 1), date(2025, 8, 31))
        bc.bill_demand(b, "p", [rate], iv, clock, [], [rule], bc.Site(), 0.0)
        self.assertEqual([x["quantity"] for x in b.lines], [10.0 * 31, 2.0 * 31])
        self.assertIn("billing_period_assumed", b.issues)
        one = bc.Bill("x", "X", date(2025, 8, 1), date(2025, 8, 31))
        aug = idx.month == 8
        bc.bill_demand(one, "p", [rate], iv[aug], bc.Clock(idx[aug], "Australia/Sydney", "NSW"), [], [rule],
                       bc.Site(), 0.0)
        self.assertEqual([x["quantity"] for x in one.lines], [2.0 * 31])
        self.assertNotIn("billing_period_assumed", one.issues)

    def test_export_allowance_pools_within_each_billing_period(self):
        """An allowance that rolls over pools within a billing period, not across the whole bill: 10 kWh a day in
        July and none in August against 5 kWh a day leaves 5 x 31 kWh charged."""
        idx = pd.date_range("2025-07-01 00:00", "2025-08-31 23:30", freq="30min")
        b1 = pd.Series(0.0, index=idx)
        b1[(idx.month == 7) & (idx.hour == 12) & (idx.minute == 0)] = 10.0
        iv = pd.DataFrame({"E1": 0.0, "B1": b1}, index=idx)
        rate = {"charge_type": "export", "register": "export", "tou_period": None, "season": None, "block": None,
                "unit": "c/kWh", "value": "1", "component": "Export", "condition": None}
        rule = {"charge_type": "export", "tou_period": None, "season": None, "measure": "kWh",
                "allowance_per_day": "5", "allowance_rollover": "1"}
        b = bc.Bill("x", "X", date(2025, 7, 1), date(2025, 8, 31))
        bc.bill_export(b, "p", [rate], iv, bc.Clock(idx, "Australia/Sydney", "NSW"), [], [rule], 0.0)
        self.assertEqual(b.lines[0]["quantity"], 5.0 * 31)
        self.assertIn("billing_period_assumed", b.issues)

    def test_event_period_leaves_the_rest_rate_charged(self):
        """Ausgrid EA374 2025-26: an anytime energy rate beside a critical peak rate with no fixed hours. Without event
        times every kWh is charged at the anytime rate and only the input is asked for; with them the event kWh move
        to the critical peak rate."""
        span = (date(2025, 7, 1), date(2025, 7, 31))
        iv = profile(span, "Australia/Sydney", 1488, [(1.0, everywhere)])
        plain = bc.bill("ausgrid", "EA374", *span, iv)
        usage = {x["label"]: x["quantity"] for x in plain.lines if x["kind"] == "usage:general"}
        self.assertEqual(usage, {"Network Energy Prices - Off-peak": 1488.0})
        self.assertNotIn("tou_rates_without_windows", plain.issues)
        self.assertEqual(plain.status, "input")
        self.assertIn("event_times_needed", plain.issues)
        site = bc.Site(event_times={"critical_peak": (("2025-07-15 16:00", "2025-07-15 20:00"),)})
        evented = bc.bill("ausgrid", "EA374", *span, iv, site)
        usage = {x["label"]: x["quantity"] for x in evented.lines if x["kind"] == "usage:general"}
        self.assertEqual(usage, {"Network Energy Prices - Critical peak energy": 8.0,
                                 "Network Energy Prices - Off-peak": 1480.0})
        self.assertEqual(evented.status, "exact")

    def test_each_event_period_takes_only_its_own_event_times(self):
        """Ausgrid EA974 2026-27 prices dynamic maximum and dynamic minimum events beside an anytime rate: an event kWh
        is charged under its own period only, and a period with no event times given asks for them."""
        span = (date(2026, 7, 1), date(2026, 7, 31))
        iv = profile(span, "Australia/Sydney", 1488, [(1.0, everywhere)])
        site = bc.Site(event_times={"dynamic_maximum": (("2026-07-15 16:00", "2026-07-15 20:00"),),
                                    "dynamic_minimum": (("2026-07-20 11:00", "2026-07-20 12:00"),)})
        b = bc.bill("ausgrid", "EA974", *span, iv, site)
        usage = {x["label"]: x["quantity"] for x in b.lines if x["kind"] == "usage:general"}
        self.assertEqual(usage, {"Network Energy Prices - Dynamic (maximum)": 8.0,
                                 "Network Energy Prices - Dynamic (minimum)": 2.0,
                                 "Network Energy Prices - Anytime": 1478.0})
        self.assertNotIn("event_times_needed", b.issues)
        site = bc.Site(event_times={"dynamic_maximum": (("2026-07-15 16:00", "2026-07-15 20:00"),)})
        b = bc.bill("ausgrid", "EA974", *span, iv, site)
        usage = {x["label"]: x["quantity"] for x in b.lines if x["kind"] == "usage:general"}
        self.assertEqual(usage, {"Network Energy Prices - Dynamic (maximum)": 8.0,
                                 "Network Energy Prices - Anytime": 1480.0})
        self.assertIn("event_times_needed", b.issues)

    def test_compare_ranks_tariffs(self):
        iv = profile(FY25, ADELAIDE, 4000, [(1.0, everywhere)])
        table = bc.compare("sapn", ["RSR", "RTOU"], *FY25, iv, SITE["sapn"])
        self.assertEqual(list(table.columns[:3]), ["tariff_code", "ex_gst", "inc_gst"])
        self.assertEqual(len(table), 2)


class TestSweep(unittest.TestCase):
    """Every tariff-period billed on one synthetic month: the blocked and assumed counts may only fall."""

    def test_counts_never_rise(self):
        recorded = json.loads(Path(bc.SWEEP_FILE).read_text())
        now = bc.sweep_counts(bc.sweep())
        risen = {k: (recorded.get(k, 0), v) for k, v in now.items()
                 if k != "exact" and v > recorded.get(k, 0)}
        self.assertEqual(risen, {}, "counts rose (recorded, now); fix the data or the calculator. When they fall, "
                                    "record them: .venv/bin/python scripts/billcalc.py sweep --write")
        self.assertEqual(sum(now[s] for s in ("exact", "assumed", "blocked")),
                         sum(recorded[s] for s in ("exact", "assumed", "blocked")),
                         "the number of tariff-periods changed: record the sweep again")


if __name__ == "__main__":
    unittest.main()
