"""Build the downloadable dataset from one commit and, with --publish, publish it as a GitHub release.

  .venv/bin/python scripts/release.py                  build from origin/main into out/release/<tag>/ (nothing published)
  .venv/bin/python scripts/release.py --ref <commit>   build from another commit
  .venv/bin/python scripts/release.py --publish        fetch origin, then also create the GitHub release (needs gh, logged in)

Assets (same names in every release, so .../releases/latest/download/<name> always points at the newest):
  tariffdb.sqlite    every table, loaded by scripts/tariffdb/load.py with all keys, foreign keys and CHECK constraints
  tariffdb-csv.zip   the tables (data/tariffdb/tables/*.csv) with schema.json, schema.sqlite.sql and docs/schema.md
  tariffdb.xlsx      one sheet per view (tariff_flat, tou_flat, unit_spelling) and a columns sheet describing them
  SHA256SUMS         checksums of the three files above (sha256sum -c SHA256SUMS)
plus release-notes.md (coverage, known gaps, how it was built, checksums), used as the release body.

Everything comes from the commit (git archive), never from the working tree, so local edits cannot leak into a release
and a rebuild of the same commit gives the same CSV zip and .xlsx byte for byte. The tag is tariffdb-<commit date>; a second
release from a later commit on the same day needs its own --tag (e.g. tariffdb-<date>.2), as the tag already exists.
Needs only git and Python 3.12+ (standard library); the built files stay out of git (out/ is ignored).
"""
import argparse
import hashlib
import io
import json
import os
import re
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


# ------------------------------------------------------------------------------------------------------------ xlsx
# A minimal Office Open XML workbook written with the standard library: inline strings, a bold frozen header with a
# filter, fixed zip timestamps (so the same commit gives the same bytes).
XLSX_STATIC = {
    "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
{sheets}</Types>""",
    "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
    "xl/styles.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>""",
}
XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def xml_text(value):
    return XML_BAD.sub("", str(value)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def column_letter(n):
    letters = ""
    while n:
        n, r = divmod(n - 1, 26)
        letters = chr(65 + r) + letters
    return letters


def sheet_xml(header, rows):
    def cell(ref, value, style=""):
        if value is None:
            return ""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"{style}><v>{value!r}</v></c>'
        return f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">{xml_text(value)}</t></is></c>'
    last = column_letter(len(header))
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           f'<dimension ref="A1:{last}{len(rows) + 1}"/>'
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
           'state="frozen"/></sheetView></sheetViews><sheetData>']
    for i, row in enumerate([header, *rows], 1):
        style = ' s="1"' if i == 1 else ""
        out.append(f'<row r="{i}">' + "".join(cell(f"{column_letter(j)}{i}", v, style)
                                              for j, v in enumerate(row, 1)) + "</row>")
    out.append(f'</sheetData><autoFilter ref="A1:{last}{len(rows) + 1}"/></worksheet>')
    return "".join(out)


def write_xlsx(db_path, path, stamp):
    """tariffdb.xlsx: each view of the database on its own sheet, then a columns sheet describing every view column."""
    con = sqlite3.connect(db_path)
    try:
        views = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'view' ORDER BY rowid")]
        sheets = []
        for view in views:
            cur = con.execute(f"SELECT * FROM {view}")
            sheets.append((view, [d[0] for d in cur.description], cur.fetchall()))
        cur = con.execute("""SELECT object_name AS view, column_name AS "column", data_type, value_list, unit,
                             definition FROM data_dictionary WHERE object_type = 'view' ORDER BY
                             (SELECT rowid FROM sqlite_master m WHERE m.name = object_name), ordinal""")
        sheets.append(("columns", [d[0] for d in cur.description], cur.fetchall()))
    finally:
        con.close()
    parts = dict(XLSX_STATIC)
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace("{sheets}", "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
        for i in range(1, len(sheets) + 1)))
    parts["xl/workbook.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
        + "".join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _, _) in enumerate(sheets, 1))
        + "</sheets><definedNames>" + "".join(
            f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
            f"'{name}'!$A$1:${column_letter(len(header))}${len(rows) + 1}</definedName>"
            for i, (name, header, rows) in enumerate(sheets)) + "</definedNames></workbook>")
    parts["xl/_rels/workbook.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                  f'relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
        + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
          'relationships/styles" Target="styles.xml"/></Relationships>')
    for i, (_, header, rows) in enumerate(sheets, 1):
        parts[f"xl/worksheets/sheet{i}.xml"] = sheet_xml(header, rows)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, text in parts.items():
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text.encode("utf-8"), compresslevel=9)


def coverage(db_path):
    con = sqlite3.connect(db_path)
    try:
        def one(query):
            return con.execute(query).fetchone()[0]

        stats = {t: one(f"SELECT count(*) FROM {t}") for t in
                 ("distributor", "tariff", "rate", "time_window", "eligibility", "source_document")}
        stats["last_day"] = one("SELECT max(effective_to) FROM tariff")
        stats["windows_from"] = one("SELECT min(effective_from) FROM tariff_window_set")
        stats["eligibility_from"] = one("SELECT min(effective_from) FROM eligibility")
        rows = con.execute("""
            SELECT d.name, d.state,
                   (SELECT s.pricing_year FROM tariff t JOIN source_document s USING (document_id)
                     WHERE t.distributor_id = d.distributor_id ORDER BY t.effective_from LIMIT 1),
                   (SELECT s.pricing_year FROM tariff t JOIN source_document s USING (document_id)
                     WHERE t.distributor_id = d.distributor_id ORDER BY t.effective_from DESC LIMIT 1),
                   (SELECT count(*) FROM tariff t WHERE t.distributor_id = d.distributor_id),
                   (SELECT count(*) FROM rate r JOIN tariff t USING (distributor_id, tariff_code, effective_from)
                     WHERE r.distributor_id = d.distributor_id AND t.status = 'final'),
                   (SELECT count(*) FROM rate r JOIN tariff t USING (distributor_id, tariff_code, effective_from)
                     WHERE r.distributor_id = d.distributor_id AND t.status = 'provisional'),
                   (SELECT group_concat(pricing_year, ', ') FROM (
                       SELECT s.pricing_year FROM rate r
                         JOIN tariff t USING (distributor_id, tariff_code, effective_from)
                         JOIN source_document s ON s.document_id = t.document_id
                        WHERE r.distributor_id = d.distributor_id AND t.status = 'provisional'
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
           (f"| {stats['tariff']:,} | {stats['rate']:,} | {stats['time_window']:,} | {stats['eligibility']:,} "
            f"| {stats['source_document']:,} |"), "",
           "## Files", "", "| File | Contents | SHA-256 |", "|---|---|---|",
           f"| `tariffdb.sqlite` | SQLite database, every table with its keys and constraints | `{sums['tariffdb.sqlite']}` |",
           f"| `tariffdb-csv.zip` | the same tables as CSV, with the schema | `{sums['tariffdb-csv.zip']}` |",
           (f"| `tariffdb.xlsx` | the flat views (`tariff_flat`: every tariff with its rates, windows and demand rules; "
            f"`tou_flat`: every tariff's TOU windows; `unit_spelling`), one sheet each, and a `columns` sheet "
            f"| `{sums['tariffdb.xlsx']}` |"), "",
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


ASSETS = ["tariffdb.sqlite", "tariffdb-csv.zip", "tariffdb.xlsx", "SHA256SUMS"]


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
        write_xlsx(db, out / "tariffdb.xlsx", tuple(int(x) for x in date))
        first_stored, gaps = stored_gaps(tree)

    sums = {name: sha256(out / name) for name in ASSETS[:3]}
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
