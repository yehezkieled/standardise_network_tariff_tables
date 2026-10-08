"""Tests of scripts/release.py: the release assets built from the current commit.

  .venv/bin/python -m unittest tests/test_release.py
"""
import contextlib
import csv
import hashlib
import io
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release  # noqa: E402

TABLES = ROOT / "data" / "tariffdb" / "tables"


def build(out):
    with contextlib.redirect_stdout(io.StringIO()):
        return release.build("HEAD", out)


class TestRelease(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.sha, cls.tag, cls.title, cls.out = build(Path(cls.tmp.name) / "a")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_sqlite_holds_every_csv_row(self):
        con = sqlite3.connect(self.out / "tariffdb.sqlite")
        try:
            for path in sorted(TABLES.glob("*.csv")):
                with path.open(newline="", encoding="utf-8") as f:
                    expected = sum(1 for _ in csv.DictReader(f))
                with self.subTest(table=path.stem):
                    self.assertEqual(con.execute(f"SELECT count(*) FROM {path.stem}").fetchone()[0], expected)
        finally:
            con.close()

    def test_zip_holds_the_committed_tables_and_schema(self):
        with zipfile.ZipFile(self.out / "tariffdb-csv.zip") as z:
            names = set(z.namelist())
            for path in TABLES.glob("*.csv"):
                self.assertEqual(z.read(f"tariffdb/tables/{path.name}"), path.read_bytes(), path.name)
        self.assertLessEqual({"tariffdb/schema.json", "tariffdb/schema.sqlite.sql", "tariffdb/schema.md"}, names)

    def test_checksums_and_notes(self):
        sums = dict(reversed(line.split("  ")) for line in (self.out / "SHA256SUMS").read_text().splitlines())
        self.assertEqual(sorted(sums), ["tariffdb-csv.zip", "tariffdb.sqlite"])
        notes = (self.out / "release-notes.md").read_text()
        for name, digest in sums.items():
            self.assertEqual(hashlib.sha256((self.out / name).read_bytes()).hexdigest(), digest)
            self.assertIn(digest, notes)
        self.assertIn(self.sha[:12], notes)
        self.assertIn("Ergon Energy 2016-17 to 2019-20", notes)
        self.assertRegex(notes, r"\| QLD \| Energex \| \d{4}-\d\d to \d{4}-\d\d \|")

    def test_rebuild_gives_the_same_zip(self):
        _, _, _, again = build(Path(self.tmp.name) / "b")
        self.assertEqual((again / "tariffdb-csv.zip").read_bytes(), (self.out / "tariffdb-csv.zip").read_bytes())


if __name__ == "__main__":
    unittest.main()
