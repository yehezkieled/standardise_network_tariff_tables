"""Fetch the source documents that are not in the checkout.

sources/inventory.csv records every document the reconciliation reads: its local path, the exact URL it was
retrieved from, an access note and the SHA-256 of the file that was used. The Wayback Machine copies (rows whose
URL is on web.archive.org, i.e. documents the distributor's site blocks or no longer serves) are committed; every
other document is re-downloaded from its recorded URL by this script and verified against the recorded checksum.

  .venv/bin/python scripts/fetch_sources.py            # download whatever is missing, verify, summarise
  .venv/bin/python scripts/fetch_sources.py --check    # only report what is missing or differs; download nothing
  .venv/bin/python scripts/fetch_sources.py --hash     # (maintenance) fill the sha256 column from the local files

A file whose checksum differs from the inventory is kept but reported: the publisher replaced the document in
place, which is exactly the situation the inventory exists to make visible.
"""
import csv, hashlib, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INV = os.path.join(ROOT, "sources", "inventory.csv")
UA = "Mozilla/5.0 (X11; Linux x86_64) aer-tariff-recon/1.0 (+https://github.com/yehezkieled/aer-tariff-recon)"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_archived(row):
    return "web.archive.org" in (row.get("source_url") or "")


def download(url, dest, attempts=3):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=120) as r, open(dest + ".part", "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(dest + ".part", dest)
            return None
        except Exception as e:  # noqa: BLE001 - report and retry
            last = e
            time.sleep(2 * (i + 1))
    return last


def main(argv):
    with open(INV, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = reader.fieldnames
    if "--hash" in argv:
        if "sha256" not in fields:
            fields = fields + ["sha256"]
        for r in rows:
            p = r.get("local_path")
            r["sha256"] = sha256(os.path.join(ROOT, p)) if p and os.path.exists(os.path.join(ROOT, p)) else ""
        with open(INV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print("sha256 recorded for", sum(1 for r in rows if r["sha256"]), "files")
        return 0
    check = "--check" in argv
    missing, fetched, failed, differs, ok = [], [], [], [], 0
    for r in rows:
        p = r.get("local_path")
        if not p:
            continue  # document known to be unavailable (see access_note)
        path = os.path.join(ROOT, p)
        url = r.get("source_url") or ""
        if not os.path.exists(path):
            if check or not url:
                missing.append(p)
                continue
            err = download(url, path)
            if err:
                failed.append((p, url, str(err)[:120]))
                continue
            fetched.append(p)
        want = (r.get("sha256") or "").strip()
        if want and os.path.exists(path):
            got = sha256(path)
            if got != want:
                differs.append((p, want[:12], got[:12]))
                continue
        ok += 1
    print(f"sources: {ok} verified, {len(fetched)} downloaded, {len(missing)} missing, {len(failed)} failed, {len(differs)} differ from inventory checksum")
    for p in missing:
        print("  MISSING ", p)
    for p, url, e in failed:
        print("  FAILED  ", p, "<-", url, "|", e)
    for p, want, got in differs:
        print("  DIFFERS ", p, f"inventory {want}.. local {got}.. (publisher replaced the document in place?)")
    archived_missing = [p for p in missing if any(r.get("local_path") == p and is_archived(r) for r in rows)]
    if archived_missing:
        print("  Wayback copies are committed to the repository; a missing one means an incomplete checkout:", archived_missing)
    return 1 if (missing or failed) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
