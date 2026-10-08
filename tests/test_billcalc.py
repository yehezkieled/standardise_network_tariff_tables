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
# the published SAPN bills are for a customer with a metering-coordinator meter on the standard tariff (no opt-in)
SITE = {"citipower": bc.Site(meter_type="interval"), "sapn": bc.Site(meter_type="accumulation")}


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
        joined = bc.bill("sapn", "RSR", *FY24, iv, bc.Site(meter_type="accumulation", opt_in=frozenset({"diversify"})))
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
