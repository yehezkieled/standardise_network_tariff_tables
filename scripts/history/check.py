"""Check one historical parser's output (out/history/<slug>.csv) the way the tariff database will use it, without
touching data/tariffdb/: build the database from the 2023-24 onward parser outputs plus this file into a scratch
directory, run every rule check of scripts/tariffdb/validate.py, list what the build leaves out, and with --sources
re-read every value of this parser's documents at its locator.

  .venv/bin/python scripts/history/check.py <slug> [--sources]

Exit status 1 when a check fails.
"""
import argparse
import csv
import os
import sys
import tempfile
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tariffdb"))
import build  # noqa: E402
import load as loader  # noqa: E402
import locators  # noqa: E402
import validate  # noqa: E402

ROOT = build.ROOT


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("slug")
    ap.add_argument("--sources", action="store_true", help="also re-read every value at its locator")
    a = ap.parse_args(argv)
    path = os.path.join(ROOT, "out", "history", f"{a.slug}.csv")
    mine = build.read_csv(path)
    print(f"{path}: {len(mine)} rows")
    left_out = Counter()
    for r in mine:
        if r["gst"] != "excl":
            left_out["GST inclusive"] += 1
        elif r["basis"] not in build.BASIS_ORDER:
            left_out[f"basis {r['basis']} (only a printed total network price is stored)"] += 1
        elif build.REPEATED_PRINTING in (r["note"] or ""):
            left_out["repeated printing"] += 1
    for why, n in sorted(left_out.items()):
        print(f"  left out by the build: {n} rows, {why}")

    parsed = build.Builder.parser_rows("out/aer_long.csv", "out/dnsp/*.csv") + mine
    b = build.Builder(parsed=parsed).build()
    out = tempfile.mkdtemp(prefix=f"history-{a.slug}-")
    build.write_all(b, out)
    db = loader.load(out)
    paths = {r["source_file"] for r in mine}
    docs = {d["document_id"]: d for d in validate.rows(db, "SELECT * FROM source_document")}
    mine_docs = {k for k, d in docs.items() if d["local_path"] in paths}

    summary = defaultdict(Counter)
    for t in validate.rows(db, "SELECT * FROM tariff"):
        if t["document_id"] in mine_docs:
            summary[(t["distributor_id"], docs[t["document_id"]]["pricing_year"], t["status"], t["document_id"])][
                "tariffs"] += 1
    for r in validate.rows(db, "SELECT document_id, tariff_code FROM rate"):
        if r["document_id"] in mine_docs:
            for k in summary:
                if k[3] == r["document_id"]:
                    summary[k]["rates"] += 1
    print("stored:")
    for (did, year, status, doc), c in sorted(summary.items()):
        print(f"  {did:13} {year:8} {status:11} {c['tariffs']:4} tariffs {c['rates']:5} rates  {doc}")
    unused = sorted(paths - {docs[k]["local_path"] for k in {k[3] for k in summary}})
    for p in unused:
        print(f"  parsed but not stored (another document prices that year): {p}")

    failed = False
    for name, check in validate.CHECKS:  # the committed 2023-24 onward data passes them all: a finding is this file's
        bad = check(db)
        failed |= bool(bad)
        print(f"{'FAIL' if bad else 'PASS'} {name}" + (f": {len(bad)}" if bad else ""))
        for x in bad[:30]:
            print("  " + x)
    if a.sources:
        bad, n = [], 0
        for r in validate.rows(db, """SELECT r.rate_id, r.locator, r.value_published, d.local_path FROM rate r
                                      JOIN source_document d USING (document_id) ORDER BY d.local_path, r.locator"""):
            if r["local_path"] not in paths:
                continue
            n += 1
            ok, why = locators.verify(os.path.join(ROOT, r["local_path"]), r["locator"], r["value_published"])
            if not ok:
                bad.append(f"rate {r['rate_id']}: {why}")
        failed |= bool(bad)
        print(f"{'FAIL' if bad else 'PASS'} values ({n} checked)" + (f": {len(bad)}" if bad else ""))
        for x in bad[:50]:
            print("  " + x)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
