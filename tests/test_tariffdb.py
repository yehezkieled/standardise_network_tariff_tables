"""Tests of the tariff database in data/tariffdb/ (built by scripts/tariffdb/build.py).

  .venv/bin/python -m unittest tests/test_tariffdb.py

TARIFFDB_SOURCES=committed re-reads only the source documents committed to the repository (AER files and Wayback
copies), as CI does: the other documents are fetched by ./run.sh, and the rebuild test needs the parser outputs in out/.
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
import build_support as bs  # noqa: E402
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
            "UPDATE tou_window SET start_time = '7am' WHERE rowid = 1",  # time
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
        self.assertGreater(checked["values"], 4000 if COMMITTED_ONLY else len(rows("rate")) - 1, out)
        if not COMMITTED_ONLY:
            self.assertEqual(checked["values"], len(rows("rate")))
            self.assertEqual(checked["quotes"], len(rows("eligibility")))

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
                                     f"'+1 day'), effective_to, tariff_name, customer_class, status, document_id "
                                     f"FROM tariff WHERE {key} AND effective_from = '{t['effective_from']}'"),
            (validate.check_status, f"UPDATE tariff SET status = CASE status WHEN 'final' THEN 'provisional' "
                                    f"ELSE 'final' END WHERE {key}"),
            (validate.check_units, "UPDATE rate SET unit = 'c/kWh' WHERE charge_type = 'daily' AND rowid IN "
                                   "(SELECT rowid FROM rate WHERE charge_type = 'daily' LIMIT 1)"),
            (validate.check_blocks, "UPDATE rate SET block = 3 WHERE block = 2 AND rowid IN "
                                    "(SELECT rowid FROM rate WHERE block = 2 LIMIT 1)"),
            (validate.check_tou, "INSERT INTO tou_window SELECT window_id || '-copy', distributor_id, tariff_code, "
                                 "effective_from, effective_to, applies_to, tou_period, period_label, day_type, "
                                 "start_time, end_time, months, season, time_basis, public_holidays, document_id, "
                                 "locator FROM tou_window LIMIT 1"),
        ]
        for check, sql in cases:
            with self.subTest(check=check.__name__):
                self.assertEqual(check(load.load()), [], "the committed data passes")
                self.assertNotEqual(check(self.broken(sql)), [], sql)


def parsed_row(side, doc, code, value, fin_year="2025-26", component="Daily charge", charge_type="fixed",
               name="Residential", note=""):
    return {"side": side, "distributor": "Essential Energy", "fin_year": fin_year, "tariff_code": code,
            "tariff_name": name, "customer_class": "Residential", "component": component, "charge_type": charge_type,
            "time_band": "", "season": "", "unit": "c/day", "value": value, "value_std": value, "unit_std": "c/day",
            "gst": "excl", "basis": "NUoS", "source_file": doc, "note": note, "locator": "pdf:p1"}


def doc(document_id, side, status="published", fin_year="2025-26"):
    return {"document_id": document_id, "local_path": f"sources/{document_id}.pdf", "recon_side": side,
            "price_status": status, "fin_year": fin_year}


class TestBuildRules(unittest.TestCase):
    """The rules that decide which rates a tariff code gets, on synthetic documents."""

    def run_build(self, docs, parsed, starts=None):
        b = build.Builder(parsed=parsed, metering=[], curated_files={}, docs=docs, starts=starts or {})
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

    def test_curated_code_matches_spacing_and_case(self):
        _, b = self.run_build([doc("dist", "DNSP")], [parsed_row("DNSP", "sources/dist.pdf", "LVKVATOU 1", "1")])
        self.assertEqual(b.tariff_code("essential", "LVKVATOU1", "2025-26", "test"), "LVKVATOU 1")
        self.assertIsNone(b.tariff_code("essential", "NOPE", "2025-26", "test"))


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
        text = UPDATE_DOC.read_text(encoding="utf-8")
        checks = re.findall(r"^  (\w+) {2,}", validate.__doc__.split("The checks, in order")[1], re.M)
        self.assertEqual(checks, ["load", "periods", "status", "units", "blocks", "tou", "files", "values",
                                  "quotes"])
        for c in checks:
            self.assertRegex(text, rf"\| `{c}` \|", f"docs/update-and-validate.md does not explain the {c} check")

    def test_fin_year_lists_agree(self):
        self.assertEqual(sorted(bs.FIN_YEAR_DATES), spec.FIN_YEARS)


if __name__ == "__main__":
    unittest.main()
