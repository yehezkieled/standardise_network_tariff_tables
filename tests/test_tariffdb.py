"""Tests of the tariff database in data/tariffdb/ (built by scripts/tariffdb/build.py).

  .venv/bin/python -m unittest tests/test_tariffdb.py

TARIFFDB_SOURCES=committed re-reads only the source documents committed to the repository (every file under sources/),
as CI does: the rebuild test needs the parser outputs in out/, which ./run.sh writes.
"""
import contextlib
import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "tariffdb"))
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402
import aliases  # noqa: E402
import build_support as bs  # noqa: E402
import curated  # noqa: E402
import load  # noqa: E402
import schema_doc  # noqa: E402
import spec  # noqa: E402
import validate  # noqa: E402

DB_DIR = ROOT / "data" / "tariffdb"
COMMITTED_ONLY = os.environ.get("TARIFFDB_SOURCES") == "committed"
UPDATE_DOC = ROOT / "docs" / "update-and-validate.md"


def rows(name):
    with open(DB_DIR / "tables" / f"{name}.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def quiet(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        return fn(*args), out.getvalue()


class TestGeneratedFiles(unittest.TestCase):
    def test_ddl_and_json_spec_are_current(self):
        self.assertEqual((DB_DIR / "schema.sqlite.sql").read_text(), spec.ddl(), "run scripts/tariffdb/build.py")
        self.assertEqual(json.loads((DB_DIR / "schema.json").read_text()), spec.json_spec(),
                         "run scripts/tariffdb/build.py")

    def test_schema_doc_is_current(self):
        for path, text in schema_doc.outputs().items():
            self.assertEqual(Path(path).read_text(encoding="utf-8"), text,
                             f"{path} is stale: run .venv/bin/python scripts/tariffdb/schema_doc.py")

    def test_csv_headers_match_spec(self):
        for t in spec.TABLES:
            with open(DB_DIR / "tables" / f"{t['name']}.csv", newline="", encoding="utf-8") as f:
                self.assertEqual(next(csv.reader(f)), [c["name"] for c in t["columns"]], t["name"])

    def test_every_column_is_described(self):
        for t in spec.TABLES:
            self.assertTrue(t["grain"] and t["source"] and t["description"], t["name"])
            for c in t["columns"]:
                self.assertTrue(c["description"], f"{t['name']}.{c['name']}")

    def test_rebuild_reproduces_every_table(self):
        if COMMITTED_ONLY or not (ROOT / "out" / "aer_long.csv").exists():
            self.skipTest("the rebuild needs the parser outputs in out/ (run ./run.sh)")
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, str(ROOT / "scripts" / "tariffdb" / "build.py"), "--out", tmp], cwd=ROOT,
                           check=True, capture_output=True)
            for p in sorted(Path(tmp).rglob("*")):
                if p.is_file():
                    rel = p.relative_to(tmp)
                    self.assertEqual(p.read_bytes(), (DB_DIR / rel).read_bytes(),
                                     f"{rel} differs from a rebuild: run scripts/tariffdb/build.py")


class TestLoad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load.load()

    def test_row_counts_survive_the_load(self):
        for t in spec.TABLES:
            n = self.db.execute(f"SELECT count(*) FROM {t['name']}").fetchone()[0]
            self.assertEqual(n, len(rows(t["name"])), t["name"])

    def test_constraints_reject_bad_rows(self):
        bad = [
            "INSERT INTO rate (rate_id) VALUES ('x')",  # NOT NULL
            "UPDATE tariff SET status = 'approved' WHERE rowid = 1",  # enum
            "UPDATE tariff SET effective_to = '2000-01-01' WHERE rowid = 1",  # effective_from <= effective_to
            "UPDATE rate SET value = 'abc' WHERE rowid = 1",  # numeric
            "UPDATE time_window SET start_time = '7am' WHERE rowid = 1",  # time
            "UPDATE rate_condition SET condition_kind = 'meter_type' WHERE rowid = 1",  # meter type list
            "UPDATE season_part SET start_anchor = 'dst_start' WHERE start_month IS NOT NULL AND rowid IN "
            "(SELECT rowid FROM season_part WHERE start_month IS NOT NULL LIMIT 1)",  # a date or an anchor
            "UPDATE tariff_link SET linked_code = NULL WHERE link_type = 'opt_out_to' AND rowid IN "
            "(SELECT rowid FROM tariff_link WHERE link_type = 'opt_out_to' LIMIT 1)",  # an opt-out names its tariff
            "UPDATE rate SET tariff_code = 'NO-SUCH-CODE' WHERE rowid = 1",  # foreign key to tariff
            "UPDATE eligibility SET operator = NULL WHERE value_num IS NOT NULL AND rowid IN "
            "(SELECT rowid FROM eligibility WHERE value_num IS NOT NULL LIMIT 1)",  # threshold needs an operator
        ]
        for sql in bad:
            with self.subTest(sql=sql):
                self.db.execute("SAVEPOINT s")
                try:
                    with self.assertRaises(sqlite3.IntegrityError):
                        self.db.execute(sql)
                finally:
                    self.db.execute("ROLLBACK TO s")

    def test_empty_field_means_null(self):
        n = self.db.execute("SELECT count(*) FROM rate WHERE season = ''").fetchone()[0]
        self.assertEqual(n, 0)

    def test_load_script_saves_a_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tariffdb.sqlite"
            subprocess.run([sys.executable, str(ROOT / "scripts" / "tariffdb" / "load.py"), "--out", str(out)],
                           cwd=ROOT, check=True, capture_output=True)
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM rate").fetchone()[0], len(rows("rate")))
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_load_rejects_bad_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["cp", "-r", str(DB_DIR / "tables"), str(DB_DIR / "schema.json"),
                            str(DB_DIR / "schema.sqlite.sql"), tmp], check=True)
            path = Path(tmp) / "tables" / "tariff.csv"
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            path.write_text("".join(lines + [lines[1]]), encoding="utf-8")  # a duplicate primary key
            with self.assertRaisesRegex(ValueError, "tariff.csv"):
                load.load(tmp)


class TestValidate(unittest.TestCase):
    def test_every_rule_check_passes(self):
        status, out = quiet(validate.main, [])
        self.assertEqual(status, 0, out)
        self.assertNotIn("FAIL", out)

    def test_every_value_and_quote_is_in_its_source(self):
        """Every rate value is at its cell or page and every quote at its locator (minutes)."""
        status, out = quiet(validate.main, ["--sources"] + (["--committed-only"] if COMMITTED_ONLY else []))
        self.assertEqual(status, 0, out)
        checked = {m[0]: int(m[1]) for m in re.findall(r"PASS (\w+) \((\d+) checked\)", out)}
        # every row whose source file is in this checkout is re-read (all of them unless TARIFFDB_SOURCES=committed)
        held = {d["document_id"] for d in rows("source_document")
                if d["local_path"] and (ROOT / d["local_path"]).exists()}
        tariff_doc = {(t["distributor_id"], t["tariff_code"], t["effective_from"]): t["document_id"]
                      for t in rows("tariff")}
        expect = {"values": sum(tariff_doc[(r["distributor_id"], r["tariff_code"], r["effective_from"])] in held
                                for r in rows("rate")),
                  "quotes": sum(e["document_id"] in held and e["quote"] != ""
                                for t in ("eligibility", "tariff_assignment", "tariff_link", "rate_condition")
                                for e in rows(t))}
        self.assertEqual({k: checked[k] for k in expect}, expect, out)
        self.assertGreater(expect["values"], 0)
        if not COMMITTED_ONLY:
            self.assertEqual(expect, {"values": len(rows("rate")), "quotes": sum(
                e["quote"] != "" for t in ("eligibility", "tariff_assignment", "tariff_link", "rate_condition")
                for e in rows(t))})

    def test_coverage_lists_every_distributor_year(self):
        db = load.load()
        lines = validate.coverage(db)
        years = {(t["distributor_id"], t["status"]) for t in rows("tariff")}
        self.assertTrue(all(any(line.startswith(d) and s in line for line in lines) for d, s in years))

    def broken(self, *sql):
        db = load.load()
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("PRAGMA ignore_check_constraints = ON")
        for s in sql:
            db.execute(s)
        return db

    def test_checks_catch_broken_rows(self):
        t = rows("tariff")[0]
        key = f"distributor_id = '{t['distributor_id']}' AND tariff_code = '{t['tariff_code']}'"
        cases = [
            (validate.check_periods, f"INSERT INTO tariff SELECT distributor_id, tariff_code, date(effective_from, "
                                     f"'+1 day'), effective_to, tariff_name, customer_class, customer_class_published, "
                                     f"pricing_basis, status, document_id FROM tariff WHERE {key} AND effective_from = '{t['effective_from']}'"),
            (validate.check_status, f"UPDATE tariff SET status = CASE status WHEN 'final' THEN 'provisional' "
                                    f"ELSE 'final' END WHERE {key}"),
            (validate.check_units, "UPDATE rate SET unit = 'c/kWh' WHERE charge_type = 'daily' AND rowid IN "
                                   "(SELECT rowid FROM rate WHERE charge_type = 'daily' LIMIT 1)"),
            (validate.check_magnitude, "UPDATE rate SET value = 5000 WHERE rowid IN (SELECT rowid FROM rate WHERE "
                                       "unit = 'c/kWh' AND tou_period != 'critical_peak' LIMIT 1)"),
            (validate.check_blocks, "UPDATE rate SET block = 3 WHERE block = 2 AND rowid IN "
                                    "(SELECT rowid FROM rate WHERE block = 2 LIMIT 1)"),
            (validate.check_aliases, "INSERT INTO tariff SELECT distributor_id, lower(tariff_code), effective_from, "
                                     "effective_to, tariff_name, customer_class, customer_class_published, "
                                     "pricing_basis, 'provisional', document_id FROM tariff WHERE status = 'final' AND tariff_code <> lower(tariff_code) LIMIT 1"),
            (validate.check_tou, "INSERT INTO time_window SELECT window_id || '-copy', window_set_id, season_id, "
                                 "'critical_minimum', period_label, day_type, start_time, end_time, locator, quote "
                                 "FROM time_window WHERE window_set_id IN (SELECT window_set_id FROM "
                                 "tariff_window_set) LIMIT 1"),
            (validate.check_tou, "UPDATE time_window SET season_id = (SELECT season_id FROM season WHERE "
                                 "window_set_id != time_window.window_set_id LIMIT 1) WHERE rowid = 1"),
            (validate.check_joins, "UPDATE rate SET tou_period = 'super_off_peak' WHERE rowid IN (SELECT r.rowid FROM "
                                   "rate r JOIN tariff_window_set w USING (distributor_id, tariff_code, "
                                   "effective_from) WHERE r.charge_type = 'usage' AND w.applies_to = 'usage' LIMIT 1)"),
            (validate.check_rules, "UPDATE charge_rule SET measure = 'kVA' WHERE measure = 'kW' AND rowid IN "
                                   "(SELECT rowid FROM charge_rule WHERE measure = 'kW' LIMIT 1)"),
        ]
        for check, sql in cases:
            with self.subTest(check=check.__name__):
                self.assertEqual(check(load.load()), [], "the committed data passes")
                self.assertNotEqual(check(self.broken(sql)), [], sql)

    def test_joins_fails_a_window_no_rate_prices(self):
        """A usage window of a period its rates price only in another season fails; a window of a period the price
        list leaves unpriced, and a demand window no rate prices, are information only."""
        key = ("distributor_id", "tariff_code", "effective_from", "tou_period")
        seasons = {}
        for r in rows("rate"):
            if r["charge_type"] == "usage":
                seasons.setdefault(tuple(r[k] for k in key), set()).add(r["season"])
        tariff, w = next((k, w) for k, ws in validate.tariff_windows(load.load()).items() for w in ws
                         if w["applies_to"] == "usage" and w["season"] == "summer"
                         and seasons.get((*k, w["tou_period"])) == {"summer"})
        tw = next(x for x in rows("time_window") if f"{x['window_id']}@usage" == w["window_id"])
        new = tw["window_set_id"] + "-x"

        def copy(to, period, season):
            """The window in a new set of its own, linked to the same tariff for `to` charges."""
            return self.broken(
                f"INSERT INTO window_set SELECT '{new}', distributor_id, name, covers_full_day, time_basis, "
                f"public_holidays, document_id, locator, quote, note FROM window_set "
                f"WHERE window_set_id = '{tw['window_set_id']}'",
                f"INSERT INTO season VALUES ('{new}:s', '{new}', {season}, 'copy')",
                f"INSERT INTO season_part SELECT '{new}:s', part_no, start_month, start_day, start_anchor, end_month, "
                f"end_day, end_anchor FROM season_part WHERE season_id = '{tw['season_id']}'",
                f"INSERT INTO time_window SELECT '{new}:w', '{new}', '{new}:s', {period}, period_label, day_type, "
                f"start_time, end_time, NULL, NULL FROM time_window WHERE window_id = '{tw['window_id']}'",
                f"INSERT INTO tariff_window_set VALUES ('{tariff[0]}', '{tariff[1]}', '{tariff[2]}', '{new}', "
                f"'{to}', NULL, NULL)")

        bad = validate.check_joins(copy("usage", "tou_period", "'winter'"))
        self.assertIn(f"window {new}:w@usage: no rate prices usage {w['tou_period']} in season winter", bad)
        for to in ("usage", "demand"):
            bad = validate.check_joins(copy(to, "'critical_minimum'", "'summer'"))
            self.assertFalse(any(f"{new}:w" in b for b in bad), bad)


def parsed_row(side, doc, code, value, fin_year="2025-26", component="Daily charge", charge_type="fixed",
               name="Residential", note=""):
    return {"side": side, "distributor": "Essential Energy", "fin_year": fin_year, "tariff_code": code,
            "tariff_name": name, "customer_class": "Residential", "component": component, "charge_type": charge_type,
            "time_band": "", "season": "", "unit": "c/day", "value": value, "value_std": value, "unit_std": "c/day",
            "gst": "excl", "basis": "NUoS", "source_file": doc, "note": note, "locator": "pdf:p1"}


def doc(document_id, side, status="published", fin_year="2025-26"):
    return {"document_id": document_id, "local_path": f"sources/{document_id}.pdf", "recon_side": side,
            "price_status": status, "fin_year": fin_year, "document_type": "price_list"}


class TestBuildRules(unittest.TestCase):
    """The rules that decide which rates a tariff code gets, on synthetic documents."""

    def run_build(self, docs, parsed, starts=None, code_aliases=()):
        b = build.Builder(parsed=parsed, metering=[], curated_files={}, docs=docs, starts=starts or {},
                          code_aliases=list(code_aliases))
        b.tariffs_and_rates()
        return {(k[1], k[2]): v for k, v in b.tables["tariff"].items()}, b

    def test_distributor_list_replaces_the_aer_rows_of_each_code_it_prices(self):
        docs = [doc("aer-v1", "AER", "proposed"), doc("dist", "DNSP")]
        tariffs, b = self.run_build(docs, [
            parsed_row("AER", "sources/aer-v1.pdf", "A1", "10"), parsed_row("AER", "sources/aer-v1.pdf", "A2", "20"),
            parsed_row("DNSP", "sources/dist.pdf", "A1", "11")])
        self.assertEqual(tariffs[("A1", "2025-07-01")]["status"], "final")
        self.assertEqual(tariffs[("A1", "2025-07-01")]["document_id"], "dist")
        self.assertEqual(tariffs[("A2", "2025-07-01")]["status"], "provisional")  # the list does not price A2
        self.assertEqual([r["value"] for r in b.tables["rate"].values() if r["tariff_code"] == "A1"], ["11"])

    def test_aer_rates_are_provisional_until_the_distributor_publishes(self):
        tariffs, _ = self.run_build([doc("aer-v1", "AER", "proposed")],
                                    [parsed_row("AER", "sources/aer-v1.pdf", "A1", "10")])
        self.assertEqual(tariffs[("A1", "2025-07-01")]["status"], "provisional")

    def test_mid_year_reissue_starts_a_new_period(self):
        docs = [doc("dist-jul", "DNSP"), doc("dist-oct", "DNSP")]
        tariffs, b = self.run_build(docs, [
            parsed_row("DNSP", "sources/dist-jul.pdf", "A1", "11"), parsed_row("DNSP", "sources/dist-jul.pdf", "A2",
                                                                               "21"),
            parsed_row("DNSP", "sources/dist-oct.pdf", "A1", "12")], starts={"sources/dist-oct.pdf": "2025-10-01"})
        self.assertEqual((tariffs[("A1", "2025-07-01")]["effective_to"], tariffs[("A1", "2025-10-01")]["effective_to"]),
                         ("2025-09-30", "2026-06-30"))
        self.assertEqual(tariffs[("A2", "2025-07-01")]["effective_to"], "2026-06-30")  # not re-issued: unchanged
        self.assertEqual(sorted((r["effective_from"], r["value"]) for r in b.tables["rate"].values()
                                if r["tariff_code"] == "A1"), [("2025-07-01", "11"), ("2025-10-01", "12")])

    def test_two_lists_for_the_same_day_need_a_named_choice(self):
        docs = [doc("dist-a", "DNSP"), doc("dist-b", "DNSP")]
        with self.assertRaisesRegex(SystemExit, "FINAL_DOCUMENT"):
            self.run_build(docs, [parsed_row("DNSP", "sources/dist-a.pdf", "A1", "1"),
                                  parsed_row("DNSP", "sources/dist-b.pdf", "A1", "2")])

    def test_years_before_the_cutoff_are_parsed_but_not_stored(self):
        docs = [doc("old", "DNSP", fin_year="2015-16"), doc("edge", "DNSP", fin_year="2016-17")]
        parsed = [parsed_row("DNSP", "sources/old.pdf", "A1", "1", fin_year="2015-16"),
                  parsed_row("DNSP", "sources/edge.pdf", "A1", "2", fin_year="2016-17")]
        for first_day, kept in ((bs.FIRST_STORED_DAY, {"edge"}), (None, {"old", "edge"})):
            b = build.Builder(parsed=parsed, metering=[], curated_files={}, docs=docs, starts={}, code_aliases=[],
                              first_day=first_day)
            b.tariffs_and_rates()
            self.assertEqual({v["document_id"] for v in b.tables["tariff"].values()}, kept)

    def test_two_lists_pricing_different_codes_both_count(self):
        tariffs, _ = self.run_build([doc("dist-a", "DNSP"), doc("dist-b", "DNSP")],
                                    [parsed_row("DNSP", "sources/dist-a.pdf", "A1", "1"),
                                     parsed_row("DNSP", "sources/dist-b.pdf", "B1", "2")])
        self.assertEqual({(k[0], v["document_id"]) for k, v in tariffs.items()}, {("A1", "dist-a"), ("B1", "dist-b")})

    def test_joint_aer_codes_are_split_and_withdrawn_rows_dropped(self):
        tariffs, _ = self.run_build([doc("aer", "AER", "approved")], [
            parsed_row("AER", "sources/aer.pdf", "010, 011*", "10"),
            parsed_row("AER", "sources/aer.pdf", "A100/F100", "10"),
            parsed_row("AER", "sources/aer.pdf", "X1", "9", name="Old (withdrawn from 1 Jul 25)")])
        self.assertEqual(sorted(c for c, _ in tariffs), ["010", "011", "A100", "F100"])

    def test_one_code_printed_for_two_tariffs(self):
        aer = "sources/aer.pdf"
        tariffs, _ = self.run_build([doc("aer", "AER", "approved")], [
            parsed_row("AER", aer, "TBA", "1", name="Trial A"), parsed_row("AER", aer, "TBA", "2", name="Trial B"),
            parsed_row("AER", aer, "R", "5", name="Name 1"), parsed_row("AER", aer, "R", "5", name="Name 2")])
        self.assertEqual(sorted(c for c, _ in tariffs), ["R", "TBA (Trial A)", "TBA (Trial B)"])

    def test_curated_code_matches_spacing_case_and_aliases(self):
        _, b = self.run_build([doc("dist", "DNSP")], [
            parsed_row("DNSP", "sources/dist.pdf", "LVKVATOU 1", "1"),
            parsed_row("DNSP", "sources/dist.pdf", "EBDEMT1", "1"),
            parsed_row("DNSP", "sources/dist.pdf", "EBDEMT2", "1")],
            code_aliases=[alias("{code}", "{code}T{n}")])
        self.assertEqual(b.tariff_codes("essential", "LVKVATOU1", "2025-26", "test"), ["LVKVATOU 1"])
        self.assertEqual(b.tariff_codes("essential", "EBDEM", "2025-26", "test"), ["EBDEMT1", "EBDEMT2"])
        self.assertEqual(b.tariff_codes("essential", "NOPE", "2025-26", "test"), [])

    def test_aer_spelling_is_stored_under_the_distributor_code(self):
        """An AER code that differs only in case is the distributor's tariff: one row, final, distributor spelling; in a
        year the distributor publishes nothing it still carries the distributor's spelling."""
        docs = [doc("aer", "AER", "approved"), doc("dist", "DNSP"), doc("aer-26", "AER", "approved", "2026-27")]
        tariffs, _ = self.run_build(docs, [
            parsed_row("AER", "sources/aer.pdf", "LVDed", "10"), parsed_row("DNSP", "sources/dist.pdf", "LVDED", "11"),
            parsed_row("AER", "sources/aer-26.pdf", "LVDed", "12", fin_year="2026-27")])
        self.assertEqual(sorted(tariffs), [("LVDED", "2025-07-01"), ("LVDED", "2026-07-01")])
        self.assertEqual(tariffs[("LVDED", "2025-07-01")]["status"], "final")
        self.assertEqual(tariffs[("LVDED", "2026-07-01")]["status"], "provisional")

    def test_aliased_aer_code_gives_way_to_the_distributor_codes(self):
        docs = [doc("aer", "AER", "approved"), doc("dist", "DNSP")]
        parsed = [parsed_row("AER", "sources/aer.pdf", "HV", "10"), parsed_row("AER", "sources/aer.pdf", "HVX", "10"),
                  parsed_row("DNSP", "sources/dist.pdf", "HV1", "11"),
                  parsed_row("DNSP", "sources/dist.pdf", "HV2", "12")]
        tariffs, _ = self.run_build(docs, parsed, code_aliases=[alias("{code}", "{code}{n}")])
        # HV is priced as HV1 and HV2; HVX has no HVX<n> in the list and stays provisional
        self.assertEqual(sorted(tariffs), [("HV1", "2025-07-01"), ("HV2", "2025-07-01"), ("HVX", "2025-07-01")])
        tariffs, _ = self.run_build(docs, parsed)  # without the rule both spellings are kept
        self.assertIn(("HV", "2025-07-01"), tariffs)

    def test_aliased_aer_code_ends_when_a_mid_year_list_starts(self):
        docs = [doc("aer", "AER", "approved"), doc("dist-oct", "DNSP")]
        tariffs, _ = self.run_build(docs, [parsed_row("AER", "sources/aer.pdf", "X-SA", "10"),
                                           parsed_row("DNSP", "sources/dist-oct.pdf", "X", "11")],
                                    starts={"sources/dist-oct.pdf": "2025-10-01"},
                                    code_aliases=[alias("{code}-SA", "{code}")])
        self.assertEqual((tariffs[("X-SA", "2025-07-01")]["effective_to"], tariffs[("X", "2025-10-01")]["status"]),
                         ("2025-09-30", "final"))


class TestCuratedRateFacts(unittest.TestCase):
    """The curated conditions, rate_periods, metering and charge_rules sections, on synthetic documents."""

    def test_curated_facts_reach_the_rates_they_name(self):
        dist = "sources/dist.pdf"
        demand = dict(parsed_row("DNSP", dist, "A1", "5", component="Summer incentive", charge_type="demand"),
                      unit="c/kW/day", unit_std="c/kW/day", season="summer")
        parsed = [parsed_row("DNSP", dist, "A1", "100"), parsed_row("DNSP", dist, "A1", "30", component="Rebate"),
                  demand, parsed_row("DNSP", dist, "B1", "100")]
        schedule = {"distributor": "Essential Energy", "fin_year": "2025-26", "tariff_code": "", "gst": "excl",
                    "component": "Legacy meter", "meter_class": "", "value": "36.5", "unit": "$/year",
                    "source_file": dist, "locator": "pdf:p9", "note": ""}
        fact = {"doc": dist, "fin_year": "2025-26", "locator": "pdf:p1", "quote": "q"}
        rule = dict(fact, charge_type="demand", measure="kW", method="max", reset="month")
        files = {"essential": {
            "distributor": "essential",
            "conditions": [dict(fact, codes=["A1"], component="Rebate", condition="opt_in:x")],
            "rate_periods": [dict(fact, codes=["A1"], component="Summer incentive", tou_period="peak",
                                  season="winter")],
            "metering": [dict(fact, codes="all", schedule="Legacy meter", condition="meter_class:old|new")],
            "charge_rules": [dict(rule, codes="all"), dict(rule, codes=["A1"], interval_min=15)]}}
        b = build.Builder(parsed=parsed, metering=[schedule], curated_files=files, docs=[doc("dist", "DNSP")],
                          starts={}, code_aliases=[])
        b.tariffs_and_rates()
        b.curated_facts()
        b.rate_ids()
        rates = {r["rate_id"]: r for r in b.tables["rate"].values()}
        conditions = {(c["rate_id"], c["condition_kind"], c["value"]) for c in b.tables["rate_condition"].values()}
        self.assertIn(("essential:A1:2025-07-01:daily:rebate", "opt_in", "x"), conditions)
        incentive = rates["essential:A1:2025-07-01:demand:summer-incentive:peak:winter"]  # the id follows the fact
        self.assertIn("season (the price list says summer)", incentive["note"])
        meters = [r for r in rates.values() if r["charge_type"] == "metering"]
        self.assertEqual(sorted((r["tariff_code"], r["value"], r["unit"]) for r in meters),
                         [(c, "10", "c/day") for c in ("A1", "B1")])
        self.assertEqual({(c[0], c[1], c[2]) for c in conditions if c[1] == "meter_class"},
                         {(r["rate_id"], "meter_class", v) for r in meters for v in ("old", "new")})
        # the rule naming A1 wins over the one for every tariff; B1 has no demand rate, so no rule
        self.assertEqual([(r["rule_id"], r["interval_min"]) for r in b.tables["charge_rule"].values()],
                         [("essential:A1:2025-07-01:demand:all:all:kW", 15)])

    def test_a_fact_stated_for_several_years_reaches_each_year(self):
        """A fin_year list (a tariff structure statement for its period) states the fact for each of those years."""
        parsed = []
        for fy in ("2024-25", "2025-26"):
            dist = f"sources/dist-{fy}.pdf"
            parsed += [parsed_row("DNSP", dist, "A1", "100", fin_year=fy),
                       dict(parsed_row("DNSP", dist, "A1", "5", fin_year=fy, component="Peak demand",
                                       charge_type="demand"), unit="c/kW/day", unit_std="c/kW/day", time_band="peak")]
        fact = {"doc": "sources/tss.pdf", "fin_year": ["2024-25", "2025-26"], "locator": "pdf:p1", "quote": "q"}
        files = {"essential": {
            "distributor": "essential",
            "tou_schedules": [dict(fact, id="essential-peak", name="peak", time_basis="local_time",
                                   public_holidays="as_weekday", covers_full_day=False,
                                   windows=[{"period": "peak", "label": "Peak", "days": "weekday", "start": "16:00",
                                             "end": "20:00"}],
                                   tariffs=[{"codes": ["A1"], "applies_to": "demand"}])],
            "charge_rules": [dict(fact, codes=["A1"], charge_type="demand", measure="kW", method="max",
                                  reset="month")]}}
        year_errors = lambda years: [e for e in curated.validate(dict(files["essential"], charge_rules=[
            dict(files["essential"]["charge_rules"][0], fin_year=years)]), check_quotes=False) if "fin_year" in e]
        self.assertEqual(year_errors(["2024-25", "2025-26"]), [])
        self.assertEqual(year_errors(2019), [])  # a Victorian calendar year
        self.assertEqual(len(year_errors(["2025-26", "2025-26"]) + year_errors(["2025"]) + year_errors([])), 3)
        docs = [doc("dist-2024-25", "DNSP", fin_year="2024-25"), doc("dist-2025-26", "DNSP"), doc("tss", "DNSP")]
        b = build.Builder(parsed=parsed, metering=[], curated_files=files, docs=docs, starts={}, code_aliases=[])
        b.tariffs_and_rates()
        b.curated_facts()
        self.assertEqual(list(b.tables["window_set"]), [("essential-peak",)])  # one statement, one set
        self.assertEqual(sorted(w["effective_from"] for w in b.tables["tariff_window_set"].values()),
                         ["2024-07-01", "2025-07-01"])
        self.assertEqual(sorted(r["effective_from"] for r in b.tables["charge_rule"].values()),
                         ["2024-07-01", "2025-07-01"])


def alias(aer_code, distributor_code, distributor_id="essential", valid_from="", valid_to=""):
    return {"distributor_id": distributor_id, "aer_code": aer_code, "distributor_code": distributor_code,
            "link_type": "alias", "valid_from": valid_from, "valid_to": valid_to, "reason": "test"}


class TestAliases(unittest.TestCase):
    def test_rules_match_only_codes_the_list_prices(self):
        rules = [alias("{code}", "{code}T{n}", "ergon"), alias("{code}-SA", "{code}", "sapn"),
                 alias("OLD", "NEW 1", "sapn", valid_to="2024-06-30")]
        self.assertEqual(aliases.targets(rules, "ergon", "EBDEM", "2024-07-01", ["EBDEMT1", "EBDEMT3", "EBDEMX"]),
                         ["EBDEMT1", "EBDEMT3"])
        self.assertEqual(aliases.targets(rules, "sapn", "hvad-sa", "2024-07-01", ["HVAD", "HVADF"]), ["HVAD"])
        self.assertEqual(aliases.targets(rules, "sapn", "OLD", "2023-07-01", ["NEW1"]), ["NEW1"])
        self.assertEqual(aliases.targets(rules, "sapn", "OLD", "2024-07-01", ["NEW1"]), [])  # rule expired
        self.assertEqual(aliases.targets(rules, "ergon", "EBDEM", "2024-07-01", []), [])  # never creates a code

    def test_committed_rules_load(self):
        self.assertTrue(aliases.load())

    def test_bad_rules_are_rejected(self):
        for row in (alias("{code}", "{code}", "nowhere"), alias("{code}{n}", "{code}"), alias("{code}", "X"),
                    alias("{x}", "{x}"), alias("A", "B", valid_from="2025-13-01"),
                    alias("A", "B", valid_from="2025-07-01", valid_to="2024-07-01"), {**alias("A", "B"), "reason": ""}):
            with self.subTest(row=row):
                self.assertTrue(aliases.problems([row]))
        self.assertEqual(aliases.problems([alias("{code}-SA", "{code}", "sapn")]), [])


class TestDocs(unittest.TestCase):
    def test_documented_commands_exist(self):
        """Every `.venv/bin/python scripts/... [--flag]` in the update guide names a script and flags it accepts."""
        text = UPDATE_DOC.read_text(encoding="utf-8")
        commands = re.findall(r"\.venv/bin/python (scripts/[\w/]+\.py)((?: --[\w-]+)*)", text)
        self.assertGreater(len(commands), 5)
        for script, flags in sorted(set(commands)):
            with self.subTest(script=script, flags=flags):
                self.assertTrue((ROOT / script).exists(), script)
                if flags:
                    help_text = subprocess.run([sys.executable, str(ROOT / script), "--help"], cwd=ROOT,
                                               capture_output=True, text=True).stdout
                    for flag in flags.split():
                        self.assertIn(flag, help_text, f"{script} {flag}")

    def test_every_validation_check_is_documented(self):
        """Each check validate.py runs (and prints as PASS/FAIL) has a row in the Checks table of the update guide."""
        _, out = quiet(validate.main, [])
        ran = re.findall(r"^(?:PASS|FAIL) (\w+)", out, re.M)
        self.assertEqual(ran, ["load"] + [name for name, _ in validate.CHECKS])
        text = UPDATE_DOC.read_text(encoding="utf-8")
        for name in ran + [name for name, _ in validate.SOURCE_CHECKS]:
            self.assertRegex(text, rf"\| `{name}` \|", f"docs/update-and-validate.md does not explain the {name} check")

    def test_pricing_year_dates(self):
        self.assertEqual(sorted(bs.YEAR_DATES), sorted(spec.PRICING_YEARS))
        self.assertEqual(bs.year_dates("2025-26"), ("2025-07-01", "2026-06-30"))
        self.assertEqual(bs.year_dates("1999-00"), ("1999-07-01", "2000-06-30"))
        self.assertEqual(bs.year_dates("2005"), ("2005-01-01", "2005-12-31"))
        self.assertEqual(bs.year_dates("2021-H1"), ("2021-01-01", "2021-06-30"))
        self.assertEqual(bs.year_dates("2000-H2"), ("2000-07-01", "2000-12-31"))


if __name__ == "__main__":
    unittest.main()
