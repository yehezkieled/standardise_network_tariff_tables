"""Build the downloadable dataset from one commit and, with --publish, publish it as a GitHub release.

  .venv/bin/python scripts/release.py                  build from origin/main into out/release/<tag>/ (nothing published)
  .venv/bin/python scripts/release.py --ref <commit>   build from another commit
  .venv/bin/python scripts/release.py --publish        fetch origin, then also create the GitHub release (needs gh, logged in)

Assets (same names in every release, so .../releases/latest/download/<name> always points at the newest):
  tariffdb.sqlite    every table, loaded by scripts/tariffdb/load.py with all keys, foreign keys and CHECK constraints
  tariffdb-csv.zip   the tables (data/tariffdb/tables/*.csv) with schema.json, schema.sqlite.sql and docs/schema.md
  SHA256SUMS         checksums of the two files above (sha256sum -c SHA256SUMS)
plus release-notes.md (coverage, known gaps, how it was built, checksums), used as the release body.

Everything comes from the commit (git archive), never from the working tree, so local edits cannot leak into a release
and a rebuild of the same commit gives the same CSV zip byte for byte. The tag is tariffdb-<commit date>; a second
release from a later commit on the same day needs its own --tag (e.g. tariffdb-<date>.2), as the tag already exists.
Needs only git and Python 3.12+ (standard library); the built files stay out of git (out/ is ignored).
"""
import argparse
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "yehezkieled/standardise_network_tariff_tables"
# what a release needs from the commit: the tables and schema, the loader and validator, the documented gaps
PATHS = ["data/tariffdb", "scripts/tariffdb", "docs/schema.md", "sources/archive/gaps.csv"]
ZIP_FILES = ["schema.json", "schema.sqlite.sql"]  # beside tables/*.csv, under data/tariffdb/


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def extract(sha, target):
    with tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", sha, *PATHS))) as tar:
        tar.extractall(target, filter="data")


def write_zip(tree, path, stamp):
    """The CSVs and schema under tariffdb/, in a fixed order with the commit's timestamp: reproducible bytes."""
    data = tree / "data" / "tariffdb"
    members = [(f"tariffdb/tables/{p.name}", p) for p in sorted((data / "tables").glob("*.csv"))]
    members += [(f"tariffdb/{name}", data / name) for name in ZIP_FILES]
    members.append(("tariffdb/schema.md", tree / "docs" / "schema.md"))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, src in members:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, src.read_bytes(), compresslevel=9)


def coverage(db_path):
    con = sqlite3.connect(db_path)
    try:
        def one(query):
            return con.execute(query).fetchone()[0]

        stats = {t: one(f"SELECT count(*) FROM {t}") for t in
                 ("distributor", "tariff", "rate", "tou_window", "eligibility", "source_document")}
        stats["last_day"] = one("SELECT max(effective_to) FROM tariff")
        stats["windows_from"] = one("SELECT min(effective_from) FROM tou_window")
        stats["eligibility_from"] = one("SELECT min(effective_from) FROM eligibility")
        rows = con.execute("""
            SELECT d.name, d.state,
                   (SELECT s.pricing_year FROM tariff t JOIN source_document s USING (document_id)
                     WHERE t.distributor_id = d.distributor_id ORDER BY t.effective_from LIMIT 1),
                   (SELECT s.pricing_year FROM tariff t JOIN source_document s USING (document_id)
                     WHERE t.distributor_id = d.distributor_id ORDER BY t.effective_from DESC LIMIT 1),
                   (SELECT count(*) FROM tariff t WHERE t.distributor_id = d.distributor_id),
                   (SELECT count(*) FROM rate r WHERE r.distributor_id = d.distributor_id AND r.status = 'final'),
                   (SELECT count(*) FROM rate r WHERE r.distributor_id = d.distributor_id
                       AND r.status = 'provisional'),
                   (SELECT group_concat(pricing_year, ', ') FROM (
                       SELECT s.pricing_year FROM rate r JOIN source_document s USING (document_id)
                        WHERE r.distributor_id = d.distributor_id AND r.status = 'provisional'
                        GROUP BY s.pricing_year ORDER BY min(r.effective_from)))
            FROM distributor d ORDER BY d.state, d.name""").fetchall()
        return stats, rows
    finally:
        con.close()


# run with the commit's own build_support (a subprocess, so a copy already imported here cannot answer instead)
STORED_GAPS = """
import csv, json, sys
import build_support as bs
names = {d["distributor_id"]: d["name"] for d in bs.DISTRIBUTORS}
with open(sys.argv[1], newline="", encoding="utf-8") as f:
    rows = [(names[r["distributor_id"]], r["reason"], r["pricing_year"])
            for r in csv.DictReader(f) if bs.stored(r["pricing_year"])]
print(json.dumps({"first_stored": bs.FIRST_STORED_DAY, "gaps": rows}))
"""


def stored_gaps(tree):
    """sources/archive/gaps.csv rows for pricing years the database stores (build_support.stored), by distributor."""
    result = json.loads(subprocess.run([sys.executable, "-c", STORED_GAPS, tree / "sources" / "archive" / "gaps.csv"],
                                       cwd=tree / "scripts" / "tariffdb", check=True, capture_output=True).stdout)
    gaps = {}
    for name, reason, year in result["gaps"]:
        gaps.setdefault((name, reason), []).append(year)
    return result["first_stored"], gaps


def notes(sha, stats, rows, first_stored, gaps, sums):
    base = f"https://github.com/{REPO}/blob/{sha}"
    out = [(f"Australian electricity network tariffs: every tariff code of all {stats['distributor']} distributors, "
            f"every pricing year in effect on or after {first_stored}, through {stats['last_day']}."), "",
           "| Tariffs | Rates | TOU windows | Eligibility criteria | Source documents |",
           "|---:|---:|---:|---:|---:|",
           (f"| {stats['tariff']:,} | {stats['rate']:,} | {stats['tou_window']:,} | {stats['eligibility']:,} "
            f"| {stats['source_document']:,} |"), "",
           "## Files", "", "| File | Contents | SHA-256 |", "|---|---|---|",
           f"| `tariffdb.sqlite` | SQLite database, every table with its keys and constraints | `{sums['tariffdb.sqlite']}` |",
           f"| `tariffdb-csv.zip` | the same tables as CSV, with the schema | `{sums['tariffdb-csv.zip']}` |", "",
           f"Columns, units and a worked tariff: [docs/schema.md]({base}/docs/schema.md).", "",
           "## Coverage", "",
           ("Rates are `final` (the distributor's own list or a state regulator's schedule) or `provisional` (the AER "
            "report, where the distributor's own list is not loaded)."), "",
           "| State | Distributor | Years | Tariffs | Final rates | Provisional rates | Provisional in |",
           "|---|---|---|---:|---:|---:|---|"]
    out += [f"| {state} | {name} | {first} to {last} | {tariffs:,} | {final:,} | {prov:,} | {prov_years or ''} |"
            for name, state, first, last, tariffs, final, prov, prov_years in rows]
    out += ["", "## Known gaps", ""]
    out += [f"- **{name} {years[0]}" + (f" to {years[-1]}" if len(years) > 1 else "") + f"** not stored: {reason}."
            for (name, reason), years in gaps.items()]
    out += [(f"- **TOU windows** start {stats['windows_from']} and **eligibility** {stats['eligibility_from']}: "
             "earlier tariffs have none; `scripts/billcalc.py sweep` lists each tariff-period a bill cannot yet price "
             "and what is missing.")]
    out += ["", "## How it was built", "",
            (f"From commit [`{sha[:12]}`]({base}) with `scripts/release.py --ref {sha[:12]}`: "
             "the structure checks of `scripts/tariffdb/validate.py` (all PASS), then `scripts/tariffdb/load.py` into "
             "SQLite. The CSVs are the committed tables, generated from the source documents by "
             "`scripts/tariffdb/build.py`; CI checks them value by value against those documents "
             "(`validate.py --sources`), not this build."), "",
            "Check a download: `sha256sum -c SHA256SUMS`.", ""]
    return "\n".join(out)


ASSETS = ["tariffdb.sqlite", "tariffdb-csv.zip", "SHA256SUMS"]


def build(ref, out_root, tag=None):
    """Validate the commit's tables and write the assets and release-notes.md; returns (sha, tag, title, directory)."""
    sha = git("rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    date = git("show", "-s", "--format=%cd", "--date=format:%Y %m %d %H %M %S", sha).decode().split()
    day = "-".join(date[:3])
    tag = tag or f"tariffdb-{day}"
    out = Path(out_root) / tag
    out.mkdir(parents=True, exist_ok=True)
    print(f"release {tag} from {sha}")

    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        extract(sha, tree)
        tools = tree / "scripts" / "tariffdb"
        subprocess.run([sys.executable, tools / "validate.py"], check=True)
        db = out / "tariffdb.sqlite"
        subprocess.run([sys.executable, tools / "load.py", "--out", db], check=True, stdout=subprocess.DEVNULL)
        write_zip(tree, out / "tariffdb-csv.zip", tuple(int(x) for x in date))
        first_stored, gaps = stored_gaps(tree)

    sums = {name: sha256(out / name) for name in ASSETS[:2]}
    (out / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sums.items()))
    stats, rows = coverage(out / "tariffdb.sqlite")
    (out / "release-notes.md").write_text(notes(sha, stats, rows, first_stored, gaps, sums))
    for name in ASSETS + ["release-notes.md"]:
        print(f"  {out / name}  ({os.path.getsize(out / name):,} bytes)")
    return sha, tag, f"Network tariff tables {day}", out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default="origin/main", help="commit to release (default origin/main)")
    parser.add_argument("--tag", help="release tag (default tariffdb-<commit date>)")
    parser.add_argument("--out", default=str(ROOT / "out" / "release"), help="output directory (default out/release)")
    parser.add_argument("--publish", action="store_true", help="create the GitHub release (needs gh)")
    args = parser.parse_args()
    if args.publish and args.ref == parser.get_default("ref"):
        git("fetch", "origin")
    sha, tag, title, out = build(args.ref, args.out, args.tag)
    if args.publish:
        if not git("branch", "-r", "--contains", sha).strip():
            sys.exit(f"{sha} is not on any remote branch: push it before publishing")
        subprocess.run(["gh", "release", "create", tag, "--repo", REPO, "--target", sha, "--title", title,
                        "--notes-file", out / "release-notes.md", *[out / name for name in ASSETS]], check=True)


if __name__ == "__main__":
    main()
