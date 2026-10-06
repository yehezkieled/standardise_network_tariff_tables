"""Tests of the tariff database in data/tariffdb/ (built by scripts/tariffdb/build.py).

  .venv/bin/python -m unittest tests/test_tariffdb.py

Schema, load, key, history, TOU and exception tests read only the committed CSVs. The source re-read tests open each
source document; a document that is not in the checkout is skipped (./run.sh fetches them; the Wayback copies are
committed), and the parser-count test runs only where the parser outputs (out/) exist.
"""
import csv
import glob
import json
import os
import re
import sqlite3
import statistics
import sys
import unittest
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "tariffdb"))
import spec  # noqa: E402
import exceptions as catalogue  # noqa: E402

DB_DIR = ROOT / "data" / "tariffdb"
TABLES = DB_DIR / "tables"


def read(table):
    with open(TABLES / f"{table}.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


_cache = {}


def rows(table):
    if table not in _cache:
        _cache[table] = read(table)
    return _cache[table]


def by(table, key):
    return {r[key]: r for r in rows(table)}


def load_sqlite():
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((DB_DIR / "schema.sqlite.sql").read_text())
    for t in spec.TABLES:
        cols = [c["name"] for c in t["columns"]]
        data = [[None if r[c] == "" else r[c] for c in cols] for r in rows(t["name"])]
        con.executemany(f"INSERT INTO {t['name']} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", data)
    con.commit()
    return con


def half_unit(c):
    v, s = float(c["value_num"]), float(c["value_std"])
    factor = s / v if v else 1.0
    return 0.5 * 10 ** (-max(0, -Decimal(c["value_published"]).as_tuple().exponent)) * abs(factor)


def src(doc):
    return ROOT / doc["local_path"] if doc["local_path"] else None


class TestSchemaFiles(unittest.TestCase):
    def test_generated_files_are_current(self):
        self.assertEqual((DB_DIR / "schema.sqlite.sql").read_text(), spec.ddl("sqlite"))
        self.assertEqual((DB_DIR / "schema.postgres.sql").read_text(), spec.ddl("postgres"))
        self.assertEqual((DB_DIR / "load.postgres.sql").read_text(), spec.postgres_load())
        self.assertEqual(json.loads((DB_DIR / "schema.json").read_text()), json.loads(json.dumps(spec.json_spec())))

    def test_docs_are_current(self):
        import docs
        self.assertEqual((ROOT / "docs" / "tariffdb.md").read_text(encoding="utf-8"), docs.markdown(docs.Data()),
                         "run .venv/bin/python scripts/tariffdb/docs.py")

    def test_csv_headers_match_spec(self):
        for t in spec.TABLES:
            with open(TABLES / f"{t['name']}.csv", newline="", encoding="utf-8") as f:
                self.assertEqual(next(csv.reader(f)), [c["name"] for c in t["columns"]], t["name"])

    def test_every_table_documents_its_design(self):
        for t in spec.TABLES:
            self.assertTrue(t["description"] and t["why"], t["name"])
            for c in t["columns"]:
                self.assertTrue(c["description"] or c["name"].endswith("_id"), f"{t['name']}.{c['name']}")


class TestLoad(unittest.TestCase):
    """The CSVs import into SQLite with every PRIMARY KEY, FOREIGN KEY, UNIQUE, NOT NULL and CHECK enforced."""

    @classmethod
    def setUpClass(cls):
        cls.con = load_sqlite()

    def test_row_counts_survive_the_load(self):
        for t in spec.TABLES:
            n = self.con.execute(f"SELECT count(*) FROM {t['name']}").fetchone()[0]
            self.assertEqual(n, len(rows(t["name"])), t["name"])
            self.assertGreater(n, 0, t["name"])

    def test_no_orphan_keys(self):
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_constraints_reject_bad_rows(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("INSERT INTO tariff VALUES ('x:1', 'nowhere', '1', 'distributor_code')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("INSERT INTO financial_year VALUES ('2030-31', '2031-06-30', '2030-07-01')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("INSERT INTO tariff VALUES ('ausgrid:Z', 'ausgrid', 'Z', 'guess')")
        with self.assertRaises(sqlite3.IntegrityError):  # boolean stored as text
            self.con.execute("UPDATE tou_schedule SET covers_full_day = 'True' WHERE rowid = 1")
        with self.assertRaises(sqlite3.IntegrityError):  # number that is not a number
            self.con.execute("UPDATE charge SET value_num = '1,234' WHERE rowid = 1")

    def test_empty_field_means_null(self):
        """CSV cannot tell '' from NULL, so no column stores an empty string: every '' is NULL and allowed."""
        for t in spec.TABLES:
            nullable = {c["name"] for c in t["columns"] if c["nullable"]}
            for r in rows(t["name"]):
                for c, v in r.items():
                    if v == "":
                        self.assertIn(c, nullable, f"{t['name']} {c}")

    @unittest.skipUnless(os.environ.get("TARIFFDB_PG_BIN"), "set TARIFFDB_PG_BIN to a PostgreSQL bin directory "
                         "(initdb, pg_ctl, psql), e.g. from `pip install pgserver`")
    def test_postgres_load(self):
        """The CSVs load into a throwaway PostgreSQL through load.postgres.sql with every constraint enforced."""
        import subprocess
        import tempfile
        pg = Path(os.environ["TARIFFDB_PG_BIN"])
        with tempfile.TemporaryDirectory() as tmp:
            data, sock = Path(tmp) / "data", Path(tmp) / "sock"
            sock.mkdir()
            run = lambda *a, **kw: subprocess.run([str(x) for x in a], check=True, capture_output=True, text=True, **kw)
            run(pg / "initdb", "-D", data, "-U", "postgres", "-A", "trust")
            run(pg / "pg_ctl", "-D", data, "-o", f"-k {sock} -c listen_addresses=''", "-l", Path(tmp) / "log", "start")
            try:
                psql = [pg / "psql", "-h", sock, "-U", "postgres", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-q"]
                run(*psql, "-f", "load.postgres.sql", cwd=DB_DIR)
                for t in spec.TABLES:
                    n = run(*psql, "-At", "-c", f"SELECT count(*) FROM {t['name']}").stdout.strip()
                    self.assertEqual(int(n), len(rows(t["name"])), t["name"])
            finally:
                run(pg / "pg_ctl", "-D", data, "stop", "-m", "fast")

    def test_postgres_ddl_mirrors_sqlite(self):
        pg = (DB_DIR / "schema.postgres.sql").read_text()
        for t in spec.TABLES:
            self.assertIn(f"CREATE TABLE {t['name']} (", pg)
        self.assertNotIn("GLOB", pg)
        self.assertIn("DATE NOT NULL", pg)


class TestHistory(unittest.TestCase):
    def test_every_value_is_tied_to_a_year_dates_and_a_document_version(self):
        docs = by("source_document", "document_id")
        fy = by("financial_year", "fin_year")
        for l in rows("tariff_listing"):
            d = docs[l["document_id"]]
            self.assertEqual(d["retrieval_status"], "retrieved")
            self.assertTrue(d["sha256"] and d["version_label"])
            self.assertGreaterEqual(l["effective_from"], fy[d["fin_year"]]["start_date"])
            self.assertLessEqual(l["effective_to"], fy[d["fin_year"]]["end_date"])
        listings = by("tariff_listing", "listing_id")
        for c in rows("charge"):
            l = listings[c["listing_id"]]
            self.assertEqual((c["effective_from"], c["effective_to"]), (l["effective_from"], l["effective_to"]))

    def test_no_duplicate_effective_ranges(self):
        """Within one document version a component has one value per (basis, GST, metering, LFiT) for a date range;
        every exception is a catalogued 'component_repeated_in_document' instance."""
        listings = by("tariff_listing", "listing_id")
        groups = defaultdict(list)
        for c in rows("charge"):
            groups[(c["listing_id"], c["price_basis"], c["gst"], c["component_label"], c["unit_published"],
                    c["includes_metering"], c["includes_lfit"], c["season"], c["time_band"])].append(c)
        catalogued = {(i["listing_id"], i["charge_id"]) for i in rows("exception_instance")
                      if i["exception_code"] == "component_repeated_in_document"}
        for k, cs in groups.items():
            if len(cs) < 2:
                continue
            spans = sorted((c["effective_from"], c["effective_to"]) for c in cs)
            if any(b[0] <= a[1] for a, b in zip(spans, spans[1:])):
                self.assertIn((k[0], cs[1]["charge_id"]), catalogued, f"{k} in {listings[k[0]]['document_id']}")
        # one listing per (document, tariff, name, class, region)
        seen = Counter((l["document_id"], l["tariff_id"], l["name_published"], l["class_published"], l["region"],
                        l["code_published"]) for l in rows("tariff_listing"))
        aer_ids = Counter()
        for a in rows("tariff_alias"):
            if a["alias_kind"] == "aer_tariff_id":
                aer_ids[(a["document_id"], a["tariff_id"])] += 1
        for k, n in seen.items():
            if n > 1:  # only where the AER prints one tariff on several rows with different AER tariff IDs
                self.assertGreaterEqual(aer_ids[(k[0], k[1])], n, k)
        # one rule per tariff, schedule and charge kind; one full-day partition per tariff, year and charge kind
        full = Counter()
        sched = by("tou_schedule", "tou_schedule_id")
        for t in rows("tariff_tou"):
            s = sched[t["tou_schedule_id"]]
            if s["covers_full_day"] == "1":
                full[(t["tariff_id"], s["document_id"], t["applies_to"], s["name"])] += 1
        self.assertFalse([k for k, n in full.items() if n > 1])

    def test_versions_are_kept_side_by_side(self):
        series = defaultdict(list)
        for d in rows("source_document"):
            series[d["series_id"]].append(int(d["version_seq"]))
        for s, seqs in series.items():
            self.assertEqual(len(seqs), len(set(seqs)), s)
        v = sorted(series["aer-all-2025-26-consolidated"])
        self.assertEqual(v, [1, 2, 3, 4, 5])

    def test_append_only_check(self):
        import build
        t = spec.BY_NAME["tariff"]
        old = "tariff_id,distributor_id,tariff_code,identity_basis\na:1,a,1,distributor_code\na:2,a,2,distributor_code\n"
        self.assertEqual(build.append_only_violations(t, old, old + "a:3,a,3,distributor_code\n"), [])
        changed = old.replace("a:2,a,2,distributor_code", "a:2,a,2,aer_label")
        self.assertEqual(len(build.append_only_violations(t, old, changed)), 1)
        removed = "tariff_id,distributor_id,tariff_code,identity_basis\na:1,a,1,distributor_code\n"
        self.assertIn("removed", build.append_only_violations(t, old, removed)[0])


class TestTOU(unittest.TestCase):
    @staticmethod
    def minutes(t):
        h, m = t.split(":")
        return int(h) * 60 + int(m)

    def test_full_day_schedules_tile_24_hours_without_overlap(self):
        import curated
        windows = defaultdict(list)
        for w in rows("tou_window"):
            windows[w["tou_schedule_id"]].append({"days": w["day_type"], "start": w["start_time"],
                                                  "end": w["end_time"], "months": w["months"], "period": w["period"]})
        full = [s for s in rows("tou_schedule") if s["covers_full_day"] == "1"]
        self.assertTrue(full)
        for s in full:
            self.assertEqual(curated.coverage_errors(windows[s["tou_schedule_id"]]), [], s["tou_schedule_id"])

    def test_windows_of_one_period_never_overlap(self):
        import curated
        spans = defaultdict(list)
        for w in rows("tou_window"):
            for d in curated.DAY_TYPE_DAYS[w["day_type"]]:
                for m in w["months"].split(","):
                    spans[(w["tou_schedule_id"], w["period"], d, m)].append(
                        (self.minutes(w["start_time"]), self.minutes(w["end_time"])))
        for k, ss in spans.items():
            ss.sort()
            for a, b in zip(ss, ss[1:]):
                self.assertLessEqual(a[1], b[0], k)

    def test_demand_rule_windows_exist(self):
        periods = {(w["tou_schedule_id"], w["period"]) for w in rows("tou_window")}
        for r in rows("demand_rule"):
            if r["window_tou_schedule_id"] and r["window_period"]:
                self.assertIn((r["window_tou_schedule_id"], r["window_period"]), periods, r["demand_rule_id"])


class TestRowCounts(unittest.TestCase):
    def test_every_distributor_and_year_has_both_sides(self):
        docs = by("source_document", "document_id")
        listings = by("tariff_listing", "listing_id")
        attachment_only = {(i["distributor_id"], i["fin_year"]) for i in instances("price_attachment_not_held")}
        n = Counter()
        for c in rows("charge"):
            d = docs[listings[c["listing_id"]]["document_id"]]
            did = d["distributor_id"] or listings[c["listing_id"]]["tariff_id"].split(":")[0]
            n[(did, d["fin_year"], d["author"])] += 1
        for did in by("distributor", "distributor_id"):
            for fy in ("2024-25", "2025-26", "2026-27"):
                self.assertGreater(n[(did, fy, "AER")], 0, (did, fy))
            for fy in ("2023-24", "2024-25", "2025-26", "2026-27"):
                if (did, fy) in attachment_only:
                    continue
                self.assertGreater(n[(did, fy, "distributor")], 0, (did, fy))
        # distributor-years whose only distributor document puts its prices in an attachment that is not held carry
        # no distributor prices, but their rules (eligibility, TOU windows) are still recorded
        self.assertEqual(attachment_only, {("citipower", "2025-26"), ("unitedenergy", "2025-26")})
        for did, fy in attachment_only:
            self.assertEqual(n[(did, fy, "distributor")], 0, (did, fy))

    @unittest.skipUnless((ROOT / "out" / "aer_long.csv").exists(), "parser outputs not built (./run.sh)")
    def test_charge_counts_equal_parser_rows(self):
        """Per distributor, year and document: charges = priced parser rows (+ one copy per extra joint-label
        member), and listings cover every parser row."""
        parser = Counter()
        for p in ["out/aer_long.csv", "out/aer_versions_long.csv"] + sorted(
                os.path.relpath(x, ROOT) for x in glob.glob(str(ROOT / "out" / "dnsp" / "*.csv"))):
            with open(ROOT / p, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if not r["component"].startswith("(no non-zero"):
                        parser[(r["distributor"], r["fin_year"], r["source_file"])] += 1
        docs = by("source_document", "document_id")
        names = {d["distributor_id"]: d["name"] for d in rows("distributor")}
        listings = by("tariff_listing", "listing_id")
        joint = {i["listing_id"] for i in rows("exception_instance") if i["exception_code"] == "joint_code_label"}
        firsts = set()
        db = Counter()
        for c in rows("charge"):
            l = listings[c["listing_id"]]
            if c["listing_id"] in joint:
                base = c["listing_id"].rsplit("/", 1)[0]
                k = (base, c["price_basis"], c["gst"], c["locator"], c["component_label"])
                if k in firsts:
                    continue
                firsts.add(k)
            d = docs[l["document_id"]]
            db[(names[l["tariff_id"].split(":")[0]], d["fin_year"], d["local_path"])] += 1
        self.assertEqual(db, parser)


class TestSourceValues(unittest.TestCase):
    """Every number and every quote is re-read from its source document at its locator."""

    @classmethod
    def setUpClass(cls):
        import locators
        cls.loc = locators
        cls.docs = by("source_document", "document_id")
        cls.listings = by("tariff_listing", "listing_id")

    def present(self, doc_id):
        d = self.docs[doc_id]
        return d["local_path"] and (ROOT / d["local_path"]).exists()

    def test_document_checksums(self):
        import hashlib
        for d in rows("source_document"):
            if d["local_path"] and (ROOT / d["local_path"]).exists() and d["committed_in_repo"] == "1":
                self.assertEqual(hashlib.sha256((ROOT / d["local_path"]).read_bytes()).hexdigest(), d["sha256"],
                                 d["document_id"])

    def test_every_charge_value_is_in_its_source(self):
        bad, n = [], 0
        # rows of the OCR'd Evoenergy tables: NUoS = DUoS + TUoS + JS is the parser's check, so it is re-checked here
        ocr_rows = defaultdict(dict)
        for c in rows("charge"):
            if c["locator_kind"] == "pdf-ocr":
                ocr_rows[(c["listing_id"], c["component_label"], c["gst"], c["locator"])][c["price_basis"]] = c
        for c in rows("charge"):
            doc = self.listings[c["listing_id"]]["document_id"]
            if not self.present(doc):
                continue
            path = str(ROOT / self.docs[doc]["local_path"])
            n += 1
            if c["locator_kind"] == "xlsx":
                raw, shown = self.loc.read_cell_excel(path, c["sheet"], c["cell"])
                if str(shown) != c["value_published"] or (repr(raw) if isinstance(raw, float) else str(raw)) != c["value_raw"]:
                    bad.append((c["charge_id"], c["value_published"], shown))
                continue
            ok, why = self.loc.verify(path, c["locator"], c["value_published"])
            if not ok and c["verification"] == "ocr_sum_check":
                ok = self.ocr_repair_holds(path, c, ocr_rows[(c["listing_id"], c["component_label"], c["gst"],
                                                                c["locator"])])
            if not ok:
                bad.append((c["charge_id"], why))
        self.assertEqual(bad, [])
        print(f"\n  re-read {n} charge values", file=sys.stderr)

    def ocr_repair_holds(self, path, c, row):
        """The scan prints some decimal points too faintly to OCR ('2 311' for 2.311; Evoenergy 2023-24 proposal
        p40-45). Accept the value when its digits appear with the point dropped, or a 400 dpi pass reads it, AND the
        row still satisfies NUoS = DUoS + TUoS + JS."""
        if set(row) != {"NUoS", "DUoS", "TUoS", "JSA"}:
            return False
        parts = sum(Decimal(row[b]["value_published"]) for b in ("DUoS", "TUoS", "JSA"))
        if parts != Decimal(row["NUoS"]["value_published"]):
            return False
        ip, _, fp = c["value_published"].lstrip("-").partition(".")
        text = self.loc.ocr_text(path, int(c["page"]))
        if any(re.search(r"(?<![\d.])" + re.escape(f) + r"(?!\d)", text) for f in (f"{ip} {fp}", f"{ip}{fp}")):
            return True
        tokens = self.loc.ocr_text(path, int(c["page"]), 400).split()
        return any(f in tokens for f in self.loc.renderings(c["value_published"]))

    def test_value_num_and_std_follow_the_published_value(self):
        from units import to_std
        for c in rows("charge"):
            self.assertEqual(Decimal(c["value_num"]), Decimal(c["value_published"]), c["charge_id"])
            if c["value_std"]:
                # unit_interpreted is the published unit as the parser reads it (e.g. $/kVA/M -> $/kVA/month)
                vs, us = to_std(c["value_published"], c["unit_interpreted"] or c["unit_published"] or "",
                                c["component_label"])
                self.assertAlmostEqual(float(c["value_std"]), vs, places=9, msg=c["charge_id"])
                self.assertEqual(us, c["unit_std"], c["charge_id"])

    def test_metering_prices_are_in_their_source(self):
        for m in rows("metering_price"):
            if not self.present(m["document_id"]):
                continue
            raw, shown = self.loc.read_cell_excel(str(ROOT / self.docs[m["document_id"]]["local_path"]), m["sheet"],
                                                  m["cell"])
            self.assertEqual(repr(raw) if isinstance(raw, float) else str(raw), m["value_raw"], m["metering_price_id"])

    def test_every_quote_is_in_its_source(self):
        bad = []
        for table, doc_col in [("tou_schedule", "document_id"), ("tariff_tou", "document_id"),
                               ("demand_rule", "document_id"), ("eligibility_rule", "document_id"),
                               ("tariff_relation", "document_id"), ("price_adjustment", "evidence_document_id"),
                               ("document_coverage", "evidence_document_id")]:
            for r in rows(table):
                if r["quote"] and self.present(r[doc_col]):
                    ok, why = self.loc.verify_quote(str(ROOT / self.docs[r[doc_col]]["local_path"]), r["locator"],
                                                    r["quote"])
                    if not ok:
                        bad.append((table, r["locator"], why, r["quote"][:60]))
        sched = by("tou_schedule", "tou_schedule_id")
        for w in rows("tou_window"):
            doc = sched[w["tou_schedule_id"]]["document_id"]
            if self.present(doc):
                ok, why = self.loc.verify_quote(str(ROOT / self.docs[doc]["local_path"]), w["locator"], w["quote"])
                if not ok:
                    bad.append(("tou_window", w["window_id"], why))
        self.assertEqual(bad, [])

    def test_curated_files_validate(self):
        import curated
        for name, data in curated.load_all().items():
            self.assertEqual(curated.validate(data, check_quotes=False), [], name)


def instances(code):
    return [i for i in rows("exception_instance") if i["exception_code"] == code]


def charges_of(doc_id, tariff_id):
    lids = {l["listing_id"] for l in rows("tariff_listing") if l["document_id"] == doc_id and l["tariff_id"] == tariff_id}
    return [c for c in rows("charge") if c["listing_id"] in lids]


class TestExceptions(unittest.TestCase):
    def test_catalogue_is_complete(self):
        names = {m for m in dir(self) if m.startswith("test_")}
        codes = {e["exception_code"] for e in catalogue.EXCEPTIONS}
        self.assertEqual({r["exception_code"] for r in rows("exception_type")}, codes)
        self.assertEqual({i["exception_code"] for i in rows("exception_instance")}, codes)
        for e in catalogue.EXCEPTIONS:
            self.assertIn(e["test"].rsplit("::", 1)[1], names, e["exception_code"])

    def test_aer_version_differs(self):
        docs = by("source_document", "document_id")
        self.assertEqual(docs["aer-consolidated-2025-26-v1"]["price_status"], "proposed")
        self.assertEqual(docs["aer-consolidated-2025-26-v5"]["price_status"], "approved")
        jem = next(i for i in instances("aer_version_differs") if i["distributor_id"] == "jemena")
        self.assertIn("90 of 95", jem["detail"])
        self.assertAlmostEqual(float(jem["quantity"]), -12.36, places=2)
        cov = {(c["document_id"], c["distributor_id"]): c["price_status"] for c in rows("document_coverage")}
        self.assertEqual(cov[("aer-consolidated-2025-26-v1", "jemena")], "proposed")
        self.assertEqual(cov[("aer-consolidated-2025-26-v3", "jemena")], "approved")
        self.assertEqual(cov[("aer-consolidated-2025-26-v3", "energex")], "proposed")
        self.assertNotIn(("aer-consolidated-2025-26-v1", "energex"), cov)
        # Jemena A100 fixed charge: v1 and v5 values are both kept, keyed by their own document
        v1 = [c for c in charges_of("aer-consolidated-2025-26-v1", "jemena:A100") if c["charge_type"] == "fixed"]
        v5 = [c for c in charges_of("aer-consolidated-2025-26-v5", "jemena:A100") if c["charge_type"] == "fixed"]
        self.assertTrue(v1 and v5)
        self.assertNotEqual(v1[0]["value_published"], v5[0]["value_published"])
        flags = {(f["listing_id"], f["flag"]) for f in rows("listing_flag")}
        self.assertIn((v1[0]["listing_id"], "proposed_price"), flags)

    def test_metering_excluded_by_aer(self):
        adj = {a["adjustment_id"]: a for a in rows("price_adjustment") if a["kind"] == "metering_adder"}
        self.assertEqual({(a["distributor_id"], a["fin_year"]) for a in adj.values()},
                         {("endeavour", y) for y in ("2024-25", "2025-26", "2026-27")}
                         | {("essential", y) for y in ("2024-25", "2025-26", "2026-27")}
                         | {(d, y) for d in ("energex", "ergon") for y in ("2025-26", "2026-27")})
        mp = by("metering_price", "metering_price_id")
        per = Counter()
        for t in rows("price_adjustment_tariff"):
            if t["adjustment_id"] not in adj:
                continue
            a = adj[t["adjustment_id"]]
            m = mp[t["metering_price_id"]]
            expected = float(m["value_c_per_day"]) if m["value_c_per_day"] else float(m["value_num"]) * 100 / 365
            self.assertAlmostEqual(float(t["expected_delta_std"]), expected, places=9)
            aer = [c for c in charges_of(a["aer_document_id"], t["tariff_id"]) if c["charge_type"] == "fixed"
                   and c["price_basis"] == "NUoS" and c["unit_std"] == "c/day"]
            dn = [c for c in charges_of(a["distributor_document_id"], t["tariff_id"]) if c["charge_type"] == "fixed"
                  and c["price_basis"] == "NUoS" and c["unit_std"] == "c/day"]
            delta = float(dn[0]["value_std"]) - float(aer[0]["value_std"])
            self.assertLessEqual(abs(delta - expected), half_unit(aer[0]) + half_unit(dn[0]) + 1e-9, t)
            self.assertEqual(aer[0]["includes_metering"], "no")
            self.assertEqual(dn[0]["includes_metering"], "yes")
            per[a["distributor_id"]] += 1
        self.assertEqual(per, Counter(endeavour=33, essential=49, energex=26, ergon=75))
        # Endeavour 2025-26: 12.65455 $/yr on the AER Metering sheet = 3.4670 c/day
        self.assertEqual(adj["metering_adder/endeavour/2025-26"]["amount"], "3.467")

    def test_act_lfit(self):
        adj = {a["adjustment_id"]: a for a in rows("price_adjustment") if a["distributor_id"] == "evoenergy"}
        self.assertEqual({k: v["amount"] for k, v in adj.items()},
                         {"lfit_adder/evoenergy/2024-25": "0.258", "lfit_adder/evoenergy/2025-26": "1.593",
                          "lfit_adder/evoenergy/2026-27": "3.035", "lfit_rebate/evoenergy/2023-24": ""})
        for a in adj.values():
            if a["amount"]:
                self.assertIn(a["amount"], a["quote"])
            self.assertIn("LFiT", a["quote"])
        n = Counter()
        for t in rows("price_adjustment_tariff"):
            a = adj.get(t["adjustment_id"])
            if not a:
                continue
            aer = [c for c in charges_of(a["aer_document_id"], t["tariff_id"]) if c["charge_type"] == "energy"
                   and c["price_basis"] == "NUoS"]
            dn = [c for c in charges_of(a["distributor_document_id"], t["tariff_id"]) if c["charge_type"] == "energy"
                  and c["price_basis"] == "NUoS"]
            for x in aer:
                for y in dn:
                    if (x["time_band"], x["season"]) != (y["time_band"], y["season"]):
                        continue
                    delta = float(y["value_std"]) - float(x["value_std"])
                    if a["amount"]:
                        self.assertLessEqual(abs(delta - float(a["amount"])), half_unit(x) + half_unit(y) + 1e-9)
                        self.assertEqual(y["includes_lfit"], "yes")
                    else:
                        self.assertLess(delta, 0)
                    self.assertEqual(x["includes_lfit"], "no")
            n[a["adjustment_id"]] += 1
        self.assertTrue(all(n[k] > 0 for k in adj), n)

    def test_zero_priced_placeholder(self):
        flags = defaultdict(set)
        for f in rows("listing_flag"):
            flags[f["listing_id"]].add(f["flag"])
        with_charges = {c["listing_id"] for c in rows("charge")}
        for i in instances("zero_priced_placeholder"):
            self.assertNotIn(i["listing_id"], with_charges)
            self.assertIn("zero_priced_placeholder", flags[i["listing_id"]])
        placeholders = {l["listing_id"] for l in rows("tariff_listing") if l["price_availability"] == "placeholder"}
        self.assertEqual(placeholders, {i["listing_id"] for i in instances("zero_priced_placeholder")})
        for l in rows("tariff_listing"):
            # is_priced and price_availability agree; listings that only carry rules have no charges either
            self.assertEqual(l["is_priced"] == "1", l["price_availability"] == "priced", l["listing_id"])
            if l["price_availability"] == "rules_only":
                self.assertNotIn(l["listing_id"], with_charges)

    def test_withdrawn_tariff_listed(self):
        ev = {(f["listing_id"], f["flag"]): f["evidence"] for f in rows("listing_flag")}
        words = {"withdrawn": "withdrawn|discontinued|ceased|ended on",
                 "closed_to_new": "clos|not available|not applicable to new|no new connections|"
                                  "available to new customers|no longer",
                 "obsolete": "obsolete",
                 "grandfathered": "grandfather"}
        for i in instances("withdrawn_tariff_listed"):
            for flag in i["detail"].removeprefix("flags: ").split(", "):
                self.assertRegex(ev[(i["listing_id"], flag)].lower(), words[flag])

    def test_aer_missing_tariff(self):
        docs = by("source_document", "document_id")
        aer = defaultdict(set)
        for l in rows("tariff_listing"):
            d = docs[l["document_id"]]
            if d["author"] == "AER" and d["price_status"] != "proposed":
                aer[d["fin_year"]].add(l["tariff_id"])
        site = 0
        for i in instances("aer_missing_tariff"):
            self.assertNotIn(i["tariff_id"], aer[i["fin_year"]])
            self.assertEqual(docs[i["document_id"]]["author"], "distributor")
            site += "site_specific" in i["detail"]
        self.assertGreater(site, 0)

    def test_aer_only_tariff(self):
        docs = by("source_document", "document_id")
        dn = defaultdict(set)
        for l in rows("tariff_listing"):
            if docs[l["document_id"]]["author"] == "distributor":
                dn[docs[l["document_id"]]["fin_year"]].add(l["tariff_id"])
        for i in instances("aer_only_tariff"):
            self.assertNotIn(i["tariff_id"], dn[i["fin_year"]])
        basis = {t["tariff_id"]: t["identity_basis"] for t in rows("tariff")}
        self.assertIn("aer_label", {basis[i["tariff_id"]] for i in instances("aer_only_tariff")})

    def test_joint_code_label(self):
        doc = "aer-consolidated-2025-26-v5"
        a = charges_of(doc, "evoenergy:010")
        b = charges_of(doc, "evoenergy:011")
        self.assertTrue(a)
        self.assertEqual(sorted((c["locator"], c["value_published"]) for c in a),
                         sorted((c["locator"], c["value_published"]) for c in b))
        aliases = {(x["alias_label"], x["tariff_id"]) for x in rows("tariff_alias") if x["document_id"] == doc
                   and x["alias_kind"] == "joint_label_member"}
        self.assertIn(("010, 011*", "evoenergy:010"), aliases)
        self.assertIn(("010, 011*", "evoenergy:011"), aliases)

    def test_code_label_quirk(self):
        alias = {(x["document_id"], x["alias_label"], x["tariff_id"]): x["alias_kind"] for x in rows("tariff_alias")}
        self.assertEqual(alias[("aer-stakeholder-report-ergon-2024-25", "EBDEM", "ergon:EBDEMT1")], "regional_suffix")
        rel = {(r["from_tariff_id"], r["to_tariff_id"], r["document_id"]): r for r in rows("tariff_relation")
               if r["relation_type"] == "aer_sibling_code"}
        self.assertEqual(rel[("sapn:LBGF", "sapn:LBGFSA", "aer-consolidated-2025-26-v5")]["quote"], "LBGFSA")
        self.assertIn(("sapn:LBGF", "sapn:LBGFCBD", "aer-consolidated-2026-27-v5"), rel)
        self.assertIn(("sapn:RSR", "sapn:RSRNE", "aer-consolidated-2026-27-v5"), rel)
        basis = {t["tariff_id"]: t["identity_basis"] for t in rows("tariff")}
        self.assertEqual(basis["sapn:LBGFSA"], "aer_label")

    def test_aer_id_changed_between_versions(self):
        got = {i["tariff_id"] for i in instances("aer_id_changed_between_versions")}
        self.assertEqual(got, {"endeavour:N61", "endeavour:N95"})
        v1 = [l for l in rows("tariff_listing") if l["document_id"] == "aer-consolidated-2025-26-v1"]
        self.assertTrue(v1 and all(l["code_published"] == "" for l in v1))
        al = {(x["alias_label"], x["tariff_id"]) for x in rows("tariff_alias")
              if x["document_id"] == "aer-consolidated-2025-26-v1" and x["alias_kind"] == "aer_tariff_id"}
        self.assertIn(("TD-END26oth-Strg", "endeavour:N95"), al)
        self.assertIn(("TD-AGD26res-Flat", "ausgrid:EA010"), al)

    def test_aer_layout_change(self):
        det = {i["document_id"]: i["detail"] for i in instances("aer_layout_change")}
        self.assertIn("#REF!", det["aer-consolidated-2025-26-v1"])
        self.assertIn("code column E", det["aer-consolidated-2025-26-v1"])
        self.assertIn("code column D labelled 'Tariff code', prices from column H", det["aer-consolidated-2025-26-v5"])
        self.assertIn("per-distributor layout", det["aer-stakeholder-report-ausgrid-2024-25"])
        for c in rows("charge"):
            if c["locator_kind"] == "xlsx":
                self.assertTrue(c["sheet"] and c["cell"])

    def test_no_aer_file_2023_24(self):
        docs = rows("source_document")
        self.assertFalse([d for d in docs if d["fin_year"] == "2023-24" and d["author"] == "AER"])
        self.assertEqual(len(instances("no_aer_file_2023_24")), 14)
        hosted = {d["distributor_id"] for d in docs if d["fin_year"] == "2023-24" and d["recon_side"] == "AER_HOSTED"}
        self.assertEqual(len(hosted), 14)

    def test_document_not_retrievable(self):
        docs = by("source_document", "document_id")
        for i in instances("document_not_retrievable"):
            d = docs[i["document_id"]]
            self.assertEqual((d["local_path"], d["sha256"], d["retrieval_status"]), ("", "", "not_retrievable"))
        self.assertEqual(docs["aer-consolidated-2025-26-v3"]["publication_date"], "2025-05-14")
        self.assertEqual(docs["aer-consolidated-2026-27-v4"]["publication_date"], "2026-05-20")
        self.assertFalse([l for l in rows("tariff_listing") if docs[l["document_id"]]["retrieval_status"] != "retrieved"])

    def test_proposed_distributor_document(self):
        (i,) = instances("proposed_distributor_document")
        self.assertEqual((i["distributor_id"], i["fin_year"]), ("sapn", "2025-26"))
        lids = {l["listing_id"] for l in rows("tariff_listing") if l["document_id"] == i["document_id"]}
        flagged = {f["listing_id"] for f in rows("listing_flag") if f["flag"] == "proposed_price"}
        self.assertTrue(lids and lids <= flagged)

    def test_metering_sheet_quirk(self):
        mp = rows("metering_price")
        exit_fees = [m for m in mp if m["charge_basis"] == "per_meter"]
        self.assertTrue(exit_fees)
        self.assertTrue(all(m["tariff_codes_published"] == "" and m["value_c_per_day"] == "" for m in exit_fees))
        self.assertTrue([i for i in instances("metering_sheet_quirk") if "blank" in i["detail"]])
        end = {m["tariff_codes_published"]: m for m in mp if m["document_id"] == "aer-consolidated-2026-27-v5"
               and m["distributor_id"] == "endeavour"}
        self.assertEqual(end["N70,N71,N72,N73"]["value_raw"], "13.293665")

    def test_tool_rounding_artefact(self):
        c = by("charge", "charge_id")
        hits = {i["charge_id"] for i in instances("tool_rounding_artefact")}
        sapn = [c[h] for h in hits if "/STR/" in h and "2025-26-v5" in h]
        self.assertTrue(sapn)
        self.assertEqual((sapn[0]["value_published"], sapn[0]["value_raw"]), ("0.0222", "0.02215"))
        self.assertEqual(len(hits), 84)

    def test_parser_note_page_offset(self):
        c = by("charge", "charge_id")
        ins = instances("parser_note_page_offset")
        self.assertEqual(len(ins), 47)
        for i in ins:
            note_page = int(re.search(r"page (\d+)", c[i["charge_id"]]["note"]).group(1))
            self.assertEqual(note_page, int(c[i["charge_id"]]["page"]) + 1)

    def test_gst_inclusive_prices(self):
        lst = by("tariff_listing", "listing_id")
        incl = {lst[c["listing_id"]]["document_id"] for c in rows("charge") if c["gst"] == "incl"}
        self.assertEqual(incl, {i["document_id"] for i in instances("gst_inclusive_prices")})

    def test_demand_period_unstated(self):
        lst = by("tariff_listing", "listing_id")
        docs = {lst[c["listing_id"]]["document_id"] for c in rows("charge")
                if c["period"] == "unstated" or c["period_inferred"] == "1"}
        self.assertEqual(docs, {i["document_id"] for i in instances("demand_period_unstated")})
        for c in rows("charge"):
            if c["period"] == "unstated":
                self.assertIn(c["quantity"], ("kW", "kVA", "kW_or_kVA"))

    def test_season_months_not_stated(self):
        import locators
        docs = by("source_document", "document_id")
        months = defaultdict(set)
        for m in rows("tou_window_month"):
            months[m["window_id"]].add(m["month"])
        unstated = [w for w in rows("tou_window") if not w["months"]]
        self.assertTrue(unstated)
        self.assertEqual({w["window_id"] for w in unstated},
                         {i["detail"].split(" ", 1)[0] for i in instances("season_months_not_stated")})
        for w in unstated:
            # a named season, no assumed months, and the quote really names the season without listing months
            self.assertTrue(w["season"], w["window_id"])
            self.assertFalse(months[w["window_id"]], w["window_id"])
            s = by("tou_schedule", "tou_schedule_id")[w["tou_schedule_id"]]
            self.assertEqual(s["covers_full_day"], "0", w["window_id"])
            ok, why = locators.verify_quote(str(ROOT / docs[s["document_id"]]["local_path"]), w["locator"], w["quote"])
            self.assertTrue(ok, (w["window_id"], why))
        for w in rows("tou_window"):
            if w["months"]:
                self.assertEqual(months[w["window_id"]], set(w["months"].split(",")), w["window_id"])

    def test_tou_definition_missing(self):
        sched = by("tou_schedule", "tou_schedule_id")
        linked = {(t["tariff_id"], sched[t["tou_schedule_id"]]["fin_year"]) for t in rows("tariff_tou")}
        ins = instances("tou_definition_missing")
        for i in ins:
            self.assertNotIn((i["tariff_id"], i["fin_year"]), linked)
        self.assertIn("energex", {i["distributor_id"] for i in ins})

    def test_medium_business_demand_assignment(self):
        rules = defaultdict(list)
        for r in rows("eligibility_rule"):
            if r["tariff_id"] == "citipower:CMG":
                rules[r["fin_year"]].append(r)

        def has(fy, rule_type, op, num, unit):
            return any(r["rule_type"] == rule_type and r["operator"] == op and float(r["value_num"] or "nan") == num
                       and r["value_unit"] == unit for r in rules[fy])
        self.assertTrue(has("2025-26", "consumption_min", "gt", 40, "MWh/yr"))
        self.assertTrue(has("2025-26", "demand_max", "lt", 120, "kVA"))
        self.assertTrue(has("2026-27", "consumption_min", "ge", 40, "MWh/yr"))
        self.assertTrue(has("2026-27", "consumption_max", "le", 160, "MWh/yr"))
        self.assertFalse([r for r in rules["2026-27"] if r["rule_type"] == "demand_max"])
        for fy in ("2025-26", "2026-27"):
            self.assertTrue([r for r in rules[fy] if r["rule_type"] == "meter_type" and r["value_text"] == "interval"])
            self.assertTrue([r for r in rules[fy] if r["rule_type"] == "opt_out_to"
                             and r["target_tariff_id"] == "citipower:CMGO21"])
        dr = by("demand_rule", "demand_rule_id")
        win = defaultdict(list)
        for w in rows("tou_window"):
            win[(w["tou_schedule_id"], w["period"])].append(w)
        cmg = [dr[t["demand_rule_id"]] for t in rows("tariff_demand_rule") if t["tariff_id"] == "citipower:CMG"]
        windowed = set()
        self.assertTrue(cmg)
        for r in cmg:
            if "30-minute" in r["quote"]:
                self.assertEqual((r["measure"], r["interval_minutes"], r["aggregation"]), ("kW", "30", "monthly_max"))
            else:
                # the 2023-24 proposal prints only the $/kW/month unit for CG/CMG; it never states the interval
                self.assertEqual((r["measure"], r["interval_minutes"], r["aggregation"]), ("kW", "", "not_stated"))
            ws = win[(r["window_tou_schedule_id"], r["window_period"])]
            self.assertTrue(all((w["start_time"], w["end_time"]) == ("10:00", "18:00") for w in ws))
            if ws:
                windowed.add(r["fin_year"])
        # each year whose document states the basis links the 10am-6pm maximum demand period through some rule
        self.assertLessEqual({"2024-25", "2025-26", "2026-27"}, windowed)
        self.assertEqual({r["fin_year"] for r in cmg if r["aggregation"] == "monthly_max"},
                         {"2024-25", "2025-26", "2026-27"})

    def test_price_attachment_not_held(self):
        import build
        import locators
        ins = instances("price_attachment_not_held")
        docs = by("source_document", "document_id")
        self.assertEqual({docs[i["document_id"]]["local_path"] for i in ins},
                         {p for p, _, _ in build.PRICE_ATTACHMENTS_NOT_HELD})
        ingestion = by("document_ingestion", "document_id")
        for path, locator, quote in build.PRICE_ATTACHMENTS_NOT_HELD:
            if (ROOT / path).exists():
                self.assertTrue(locators.verify_quote(str(ROOT / path), locator, quote)[0], path)
        for i in ins:
            self.assertEqual(ingestion[i["document_id"]]["charge_count"], "0")
            self.assertEqual(ingestion[i["document_id"]]["status"], "rules_only")

    def test_quantity_blocks(self):
        steps = defaultdict(list)
        for r in rows("charge_step"):
            steps[(r["tariff_id"], r["document_id"], r["step_group"])].append(r)
        self.assertTrue(steps)
        self.assertEqual({(i["tariff_id"], i["document_id"]) for i in instances("quantity_blocks")},
                         {(r["tariff_id"], r["document_id"]) for r in rows("charge_step")})
        for k, blocks in steps.items():
            blocks.sort(key=lambda r: int(r["step_index"]))
            self.assertEqual([int(r["step_index"]) for r in blocks], list(range(1, len(blocks) + 1)), k)
            self.assertEqual(len({(r["quantity_unit"], r["reset_period"]) for r in blocks}), 1, k)
            self.assertEqual(blocks[-1]["upper_bound"], "", k)
            for a, b in zip(blocks, blocks[1:]):
                # consecutive blocks meet at one boundary that belongs to exactly one of them
                self.assertEqual(Decimal(a["upper_bound"]), Decimal(b["lower_bound"]), k)
                self.assertNotEqual(a["upper_inclusive"], b["lower_inclusive"], k)

    def test_component_repeated_in_document(self):
        c = by("charge", "charge_id")
        for i in instances("component_repeated_in_document"):
            first = c[i["charge_id"]]
            same = [x for x in rows("charge") if x["listing_id"] == first["listing_id"]
                    and x["component_label"] == first["component_label"] and x["price_basis"] == first["price_basis"]
                    and x["gst"] == first["gst"]]
            self.assertGreaterEqual(len(same), int(i["quantity"]))
            self.assertEqual(len({x["locator"] + x["charge_id"] for x in same}), len(same))


if __name__ == "__main__":
    unittest.main()
