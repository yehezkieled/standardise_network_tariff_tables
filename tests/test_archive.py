"""Historical source archive (sources/archive/): every file registered, intact and within GitHub's size limit, and
the coverage table current, with a reason for every distributor-year that has no price document."""
import csv
import hashlib
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "tariffdb"))
import archive_sources as A  # noqa: E402

ARCHIVE = ROOT / "sources" / "archive"
OWN_FILES = {"inventory.csv", "gaps.csv", "coverage.csv", "README.md"}


class ArchiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = A.read_rows()

    def test_columns_and_values(self):
        with open(A.INVENTORY, newline="", encoding="utf-8") as f:
            self.assertEqual(csv.DictReader(f).fieldnames, A.COLUMNS)
        for r in self.rows:
            with self.subTest(r["local_path"]):
                self.assertEqual(A.DISTRIBUTORS[r["distributor"]], r["distributor_id"])
                self.assertEqual(A.year_kind(r["pricing_year"]), r["year_kind"])
                self.assertIn(r["side"], A.SIDES)
                self.assertIn(r["document_kind"], A.KINDS)
                self.assertIn(r["price_status"], A.PRICE_STATUS)
                self.assertTrue(r["title"] and r["source_url"])
                for field in ("publication_date", "effective_from", "retrieved_on"):
                    if r[field]:
                        self.assertRegex(r[field], f"^{A.DATE_RE}$", field)
                if r["effective_from"]:
                    import build_support
                    start, end = build_support.year_dates(r["pricing_year"])
                    self.assertTrue(start < r["effective_from"] <= end, "effective_from inside its pricing year")
                self.assertEqual(r["retrieved_via"] == "wayback", "web.archive.org/web/" in r["source_url"])
                if r["retrieved_via"] == "wayback":
                    self.assertRegex(r["source_url"], r"web\.archive\.org/web/\d+id_/")
                self.assertTrue(r["local_path"].startswith(f"sources/archive/{r['distributor_id']}/"
                                                           f"{r['pricing_year']}/"))
        self.assertEqual(len({r["local_path"] for r in self.rows}), len(self.rows))

    def test_files_match_inventory(self):
        for r in self.rows:
            with self.subTest(r["local_path"]):
                p = ROOT / r["local_path"]
                self.assertTrue(p.exists())
                self.assertEqual(p.stat().st_size, int(r["bytes"]))
                self.assertLess(p.stat().st_size, A.MAX_BYTES)
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                self.assertEqual(h, r["sha256"])
                self.assertEqual(A.damage(p), "", "replace the cut-off capture or remove it")
                self.assertRegex(p.name, r"^[A-Za-z0-9._-]+$")

    def test_every_file_is_registered(self):
        held = {r["local_path"] for r in self.rows}
        for d, _, files in os.walk(ARCHIVE):
            for f in files:
                rel = str((Path(d) / f).relative_to(ROOT))
                if Path(d) == ARCHIVE and f in OWN_FILES or f.endswith(".lock"):
                    continue
                self.assertIn(rel, held, "file not in sources/archive/inventory.csv")

    def test_coverage_is_current(self):
        with open(A.COVERAGE, newline="", encoding="utf-8") as f:
            committed = list(csv.DictReader(f))
        self.assertEqual(committed, A.coverage_rows(),
                         "run: .venv/bin/python scripts/archive_sources.py coverage")

    def test_every_gap_has_a_reason(self):
        for r in A.coverage_rows():
            if r["price_documents"] == "0":
                with self.subTest(f"{r['distributor']} {r['pricing_year']}"):
                    self.assertTrue(r["gap_reason"], "no price document: add a sources/archive/gaps.csv row")
        for (did, y), g in A.read_gaps().items():
            with self.subTest(f"gap {did} {y}"):
                self.assertIn(did, A.DISTRIBUTORS.values())
                A.year_kind(y)
                self.assertTrue(g["reason"] and g["evidence"])

    def test_year_keys(self):
        self.assertEqual(A.year_keys("citipower", "2019"), ["2019", "2020", "2021-H1", "2021-22", "2022-23"])
        self.assertEqual(A.year_keys("ausgrid", "2020-21"), ["2020-21", "2021-22", "2022-23"])
        self.assertEqual(A.year_keys("tasnetworks", "2007")[:3], ["2007", "2008-H1", "2008-09"])
        self.assertEqual(A.year_keys("powercor", "1999-00")[:3], ["1999-00", "2000-H2", "2001"])

    def test_file_name(self):
        self.assertEqual(A.file_name("https://web.archive.org/web/2004id_/http://x.au/a/Price%20List%202004.pdf"),
                         "Price_List_2004.pdf")
        self.assertEqual(A.file_name("http://x.au/download.jsp?id=11938", b"%PDF-1.4"), "download.jsp_11938.pdf")
        self.assertEqual(A.file_name("http://x.au/../../etc/passwd"), "passwd.bin")


if __name__ == "__main__":
    unittest.main()
