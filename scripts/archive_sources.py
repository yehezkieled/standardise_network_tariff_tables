"""Archive of historical network tariff source documents (pricing years before 2023-24).

Every document is committed under sources/archive/<distributor_id>/<pricing year>/ and registered, one row per file,
in sources/archive/inventory.csv. sources/archive/coverage.csv records, per distributor and pricing year, what is held
and, where nothing is held, why. The current pipeline (scripts/tariffdb) does not read the archive yet.

    .venv/bin/python scripts/archive_sources.py add --distributor TasNetworks --year 2019-20 --side AER_HOSTED \
        --kind pricing_proposal --title "TasNetworks - AER approved REVISED Annual Distribution Pricing Proposal 2019-20" \
        --url "https://www.aer.gov.au/system/files/....pdf" [--landing-page URL] [--version-label "AER approved (revised)"] \
        [--price-status approved] [--publication-date 2019-05-29 --publication-date-basis aer_page] [--note ...]
    .venv/bin/python scripts/archive_sources.py check     # every registered file exists and matches its sha256
    .venv/bin/python scripts/archive_sources.py coverage  # rewrite sources/archive/coverage.csv

Wayback copies: pass the capture URL with 'id_' after the timestamp
(https://web.archive.org/web/20140213205358id_/http://www.aer.gov.au/...) so the file comes back unmodified; the
capture date becomes retrieved_on. Columns are described in sources/archive/README.md.
"""
import argparse
import csv
import fcntl
import hashlib
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "sources", "archive")
INVENTORY = os.path.join(ARCHIVE, "inventory.csv")
GAPS = os.path.join(ARCHIVE, "gaps.csv")
COVERAGE = os.path.join(ARCHIVE, "coverage.csv")
LAST_YEAR = "2022-23"  # 2023-24 onward is in sources/inventory.csv and the tariff database
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MAX_BYTES = 95 * 1024 * 1024  # GitHub rejects files over 100 MB

COLUMNS = ["distributor_id", "distributor", "pricing_year", "year_kind", "side", "document_kind", "title",
           "version_label", "price_status", "publication_date", "publication_date_basis", "landing_page", "source_url",
           "retrieved_via", "retrieved_on", "local_path", "bytes", "sha256", "note"]
# AER_HOSTED: distributor document on aer.gov.au; AER: AER-authored; DNSP: the distributor's own site;
# REGULATOR_HOSTED: a state regulator before the AER (IPART, ICRC, ESC, QCA, ESCOSA, OTTER, NT Utilities Commission)
SIDES = ["AER_HOSTED", "AER", "DNSP", "REGULATOR_HOSTED"]
KINDS = ["price_list", "pricing_proposal", "tariff_summary", "tariff_schedule", "price_guide", "annual_tariff_report",
         "tariff_structure_statement", "metering_price_list", "alternative_control_services", "pricing_model",
         "statement_of_reasons", "enforceable_undertaking", "other"]
# not_applicable: the document prints no prices (statement of reasons, tariff structure statement)
PRICE_STATUS = ["proposed", "approved", "published", "unverified", "not_applicable"]
PRICE_KINDS = ["price_list", "pricing_proposal", "tariff_summary", "tariff_schedule", "price_guide",
               "annual_tariff_report", "pricing_model"]
STATUS_RANK = ["published", "approved", "proposed", "unverified"]  # best first: the distributor's own list is final
VIC = {"citipower", "powercor", "unitedenergy", "jemena", "ausnet"}  # calendar years to 2020, then 2021-H1
DISTRIBUTORS = {  # canonical name (scripts/schema.py CANON) -> distributor_id (scripts/tariffdb/build_support.py)
    "Ausgrid": "ausgrid", "AusNet Services": "ausnet", "CitiPower": "citipower", "Endeavour Energy": "endeavour",
    "Energex": "energex", "Ergon Energy": "ergon", "Essential Energy": "essential", "Evoenergy": "evoenergy",
    "Jemena": "jemena", "Power and Water Corporation": "powerwater", "Powercor": "powercor",
    "SA Power Networks": "sapn", "TasNetworks": "tasnetworks", "United Energy": "unitedenergy",
}


def year_kind(key):
    if re.fullmatch(r"20\d\d-\d\d", key) and int(key[5:]) == (int(key[2:4]) + 1) % 100:
        return "financial_year"
    if re.fullmatch(r"20\d\d", key):
        return "calendar_year"
    if re.fullmatch(r"20\d\d-H1", key):
        return "half_year"
    raise SystemExit(f"pricing year {key!r}: use 2014-15 (financial), 2019 (calendar) or 2021-H1 (half year)")


def download(url, dest, attempts=5):
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read()
            head = data[:800].lstrip().lower()
            if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
                raise ValueError("got an HTML page, not the document (bot challenge or a removed file)")
            if len(data) > MAX_BYTES:
                raise SystemExit(f"{url}: {len(data) / 1e6:.0f} MB, over the GitHub file limit")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest + ".part", "wb") as f:
                f.write(data)
            os.replace(dest + ".part", dest)
            return
        except SystemExit:
            raise
        except Exception as e:  # network errors and Wayback rate limits: retry with back-off
            last = e
            time.sleep(10 * (i + 1))
    raise SystemExit(f"download failed: {url}: {last}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_name(url):
    name = urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[-1])
    stem, ext = os.path.splitext(name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_")[:120]
    return stem + ext.lower()


def read_rows():
    if not os.path.exists(INVENTORY):
        return []
    with open(INVENTORY, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_rows(rows):
    rows.sort(key=lambda r: (r["distributor_id"], r["pricing_year"], r["side"], r["document_kind"], r["local_path"]))
    with open(INVENTORY, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def add(a):
    if a.distributor not in DISTRIBUTORS:
        raise SystemExit(f"--distributor {a.distributor!r}: one of {sorted(DISTRIBUTORS)}")
    did = DISTRIBUTORS[a.distributor]
    kind = year_kind(a.year)
    m = re.search(r"web\.archive\.org/web/(\d{4})(\d\d)(\d\d)\d*(id_)?/", a.url)
    if m and not m.group(4):
        raise SystemExit("Wayback URL without 'id_': the archive would rewrite the file")
    rel = a.path or f"sources/archive/{did}/{a.year}/{file_name(a.url)}"
    taken = {r["local_path"]: r["source_url"] for r in read_rows()}
    stem, ext = os.path.splitext(rel)
    n = 1
    while taken.get(rel, a.url) != a.url:  # another document already uses this file name
        n += 1
        rel = f"{stem}_{n}{ext}"
    dest = os.path.join(ROOT, rel)
    if not os.path.exists(dest):
        download(a.url, dest)
    size = os.path.getsize(dest)
    row = {"distributor_id": did, "distributor": a.distributor, "pricing_year": a.year, "year_kind": kind,
           "side": a.side, "document_kind": a.kind, "title": a.title, "version_label": a.version_label,
           "price_status": a.price_status, "publication_date": a.publication_date,
           "publication_date_basis": a.publication_date_basis if a.publication_date else "",
           "landing_page": a.landing_page, "source_url": a.url, "retrieved_via": "wayback" if m else "direct",
           "retrieved_on": f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else date.today().isoformat(),
           "local_path": rel, "bytes": str(size), "sha256": sha256(dest), "note": a.note}
    with open(INVENTORY + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # several documents may be added at once
        rows = [r for r in read_rows() if r["local_path"] != rel]
        same = [r["local_path"] for r in rows if r["sha256"] == row["sha256"]]
        if same and not a.allow_duplicate:
            os.remove(dest)
            print(f"{rel}: skipped, the same file is registered as {same[0]} (pass --allow-duplicate if it is a "
                  f"separately published version)")
            return
        write_rows(rows + [row])
    print(f"{rel}: {size:,} bytes")


def year_keys(did, first):
    """Every pricing year key for a distributor from `first` to LAST_YEAR, in order."""
    fy = [f"{y}-{(y + 1) % 100:02d}" for y in range(2000, 2023)]
    keys = [str(y) for y in range(2000, 2021)] + ["2021-H1"] + fy[21:] if did in VIC else fy
    start = keys.index(first) if first in keys else 0
    return keys[start:keys.index(LAST_YEAR) + 1]


def read_gaps():
    if not os.path.exists(GAPS):
        return {}
    with open(GAPS, newline="", encoding="utf-8") as f:
        return {(r["distributor_id"], r["pricing_year"]): r for r in csv.DictReader(f)}


def coverage_rows():
    docs, gaps = read_rows(), read_gaps()
    out = []
    for name, did in sorted(DISTRIBUTORS.items(), key=lambda kv: kv[1]):
        mine = [r for r in docs if r["distributor_id"] == did]
        years = [r["pricing_year"] for r in mine] + [y for d, y in gaps if d == did]
        keys = year_keys(did, min(years, key=lambda y: (y[:4], len(y))) if years else LAST_YEAR)
        for y in keys:
            held = [r for r in mine if r["pricing_year"] == y]
            price = [r for r in held if r["document_kind"] in PRICE_KINDS]
            statuses = {r["price_status"] for r in price}
            gap = gaps.get((did, y), {})
            out.append({
                "distributor_id": did, "distributor": name, "pricing_year": y, "year_kind": year_kind(y),
                "documents": str(len(held)), "price_documents": str(len(price)),
                "metering_documents": str(sum(r["document_kind"] == "metering_price_list" for r in held)),
                "sides": " ".join(sorted({r["side"] for r in held})),
                "best_price_status": next((s for s in STATUS_RANK if s in statuses), ""),
                "gap_reason": gap.get("reason", ""), "gap_evidence": gap.get("evidence", ""),
            })
    return out


COVERAGE_COLUMNS = ["distributor_id", "distributor", "pricing_year", "year_kind", "documents", "price_documents",
                    "metering_documents", "sides", "best_price_status", "gap_reason", "gap_evidence"]


def coverage(_a):
    rows = coverage_rows()
    with open(COVERAGE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COVERAGE_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    missing = [f"{r['distributor']} {r['pricing_year']}" for r in rows if r["price_documents"] == "0"
               and not r["gap_reason"]]
    print(f"{len(rows)} distributor-years, {len(missing)} without price documents or a gap reason: {missing}")


def check(_a):
    bad = 0
    for r in read_rows():
        p = os.path.join(ROOT, r["local_path"])
        if not os.path.exists(p):
            print("MISSING ", r["local_path"]); bad += 1
        elif sha256(p) != r["sha256"]:
            print("DIFFERS ", r["local_path"]); bad += 1
        elif r["pricing_year"] and year_kind(r["pricing_year"]) != r["year_kind"]:
            print("YEARKIND", r["local_path"]); bad += 1
    print(f"{len(read_rows())} archived documents, {bad} problems")
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="download one document and register it")
    p.add_argument("--distributor", required=True)
    p.add_argument("--year", required=True, help="pricing year: 2014-15, 2019 or 2021-H1")
    p.add_argument("--side", required=True, choices=SIDES)
    p.add_argument("--kind", required=True, choices=KINDS)
    p.add_argument("--title", required=True, help="document title as published")
    p.add_argument("--url", required=True, help="exact URL the file is retrieved from")
    p.add_argument("--path", default="", help="repo-relative destination (default sources/archive/<id>/<year>/<file>)")
    p.add_argument("--landing-page", default="")
    p.add_argument("--version-label", default="as published")
    p.add_argument("--price-status", default="unverified", choices=PRICE_STATUS)
    p.add_argument("--publication-date", default="")
    p.add_argument("--publication-date-basis", default="landing_page")
    p.add_argument("--note", default="")
    p.add_argument("--allow-duplicate", action="store_true")
    p.set_defaults(fn=add)
    sub.add_parser("check", help="verify every registered file").set_defaults(fn=check)
    sub.add_parser("coverage", help="rewrite sources/archive/coverage.csv").set_defaults(fn=coverage)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
