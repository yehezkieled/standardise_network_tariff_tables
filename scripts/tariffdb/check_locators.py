"""Re-read every value of one or more parser outputs at its locator.

  .venv/bin/python scripts/tariffdb/check_locators.py out/dnsp/cp_pc_ue.csv [more.csv ...] [--show 20]

Exit status 1 when any row has no locator, a malformed one, or a value that is not at the locator.
"""
import argparse
import csv
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import locators  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check(paths, show=20):
    bad = []
    stats = Counter()
    for p in paths:
        with open(p, newline="") as f:
            for i, r in enumerate(csv.DictReader(f), 2):
                stats["rows"] += 1
                loc = r.get("locator", "")
                if not loc:
                    bad.append((p, i, r, "no locator"))
                    continue
                try:
                    kind = locators.parse(loc)["kind"]
                except ValueError as e:
                    bad.append((p, i, r, str(e)))
                    continue
                stats[kind] += 1
                if r.get("component") == "(no non-zero components)":
                    continue  # placeholder row: the locator points at the tariff's row, there is no value to re-read
                ok, why = locators.verify(os.path.join(ROOT, r["source_file"]), loc, r["value"])
                if not ok:
                    bad.append((p, i, r, why))
    print(f"checked {stats['rows']} rows ({', '.join(f'{k}={v}' for k, v in sorted(stats.items()) if k != 'rows')}); {len(bad)} failures")
    by_file = Counter((b[0], b[2]["source_file"]) for b in bad)
    for (p, sf), n in by_file.most_common():
        print(f"  {n:6d}  {os.path.basename(p)}  {sf}")
    for p, i, r, why in bad[:show]:
        print(f"  {os.path.basename(p)}:{i} {r['distributor']} {r['fin_year']} {r['tariff_code']} {r['component']!r} "
              f"value={r['value']!r} locator={r.get('locator')!r}: {why}")
    return not bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--show", type=int, default=20)
    a = ap.parse_args()
    sys.exit(0 if check(a.csv, a.show) else 1)
