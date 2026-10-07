"""Check the publishers for new or changed source documents. Read-only: it only sends GET requests to the public pages
and files named in sources/watch.csv and sources/inventory.csv, and writes nothing unless asked.

Run monthly by .github/workflows/release-check.yml; anyone can run it locally or from their own cron.

  pages      every page in sources/watch.csv, and every AER report landing page (linked from a watched page, or in
             build_support.LANDING): a document link not in sources/watch_seen.csv is NEW, a recorded one that is no
             longer on its page is GONE
  changelog  each AER landing page's "On <day> we published version <n>" lines not yet in build_support.AER_VERSIONS
  files      every inventory row with a live URL (Wayback copies are skipped): a SHA-256 that differs from the
             inventory is CHANGED (the publisher replaced the document in place)

  .venv/bin/python scripts/check_releases.py                    # report on stdout
  .venv/bin/python scripts/check_releases.py --no-files         # pages and changelog only (seconds)
  .venv/bin/python scripts/check_releases.py --report out/release-check.md
  .venv/bin/python scripts/check_releases.py --archive          # also save new AER files and landing pages under sources/aer/
  .venv/bin/python scripts/check_releases.py --accept           # record the current links in sources/watch_seen.csv

Exit status: 0 nothing new, 2 something new or changed, 1 usage error. Pages or files that cannot be reached (some
publishers block automated access) are listed but do not count as a change.
"""
import argparse, concurrent.futures, csv, datetime, hashlib, html, os, re, sys, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tariffdb"))
from fetch_sources import INV, ROOT  # noqa: E402
import build_support  # noqa: E402

WATCH = os.path.join(ROOT, "sources", "watch.csv")
SEEN = os.path.join(ROOT, "sources", "watch_seen.csv")
LANDING_RE = re.compile(r"^https://www\.aer\.gov\.au/documents/[^?#]*consolidated-stakeholder-report[^?#]*$")
# a document: by file extension, or by the download paths the publishers' sites use
DOC_RE = re.compile(r"\.(pdf|xlsx|xlsm|xls|csv|zip|docx)$", re.I)
DOC_PATH_RE = re.compile(r"download\.jsp|/api/public/content/|/-/media/|/__data/assets/|/system/files/", re.I)
NOT_DOC_RE = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|css|js|ashx)$", re.I)
# no URL in the user agent: the AER's site stalls requests whose user agent carries one
UA = "Mozilla/5.0 (X11; Linux x86_64) standardise_network_tariff_tables/1.0"
CHANGELOG_RE = re.compile(r"On (\d{1,2} [A-Z][a-z]+(?: \d{4})?) we published version (\d+)")
FY_RE = re.compile(r"(20\d\d)\D{1,3}(\d\d)(?!\d)")


def get(url, attempts=2):
    """Return the body of url, or raise the last error."""
    last = None
    for _ in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - reported to the caller
            last = e
    raise last


def links(page_url, body):
    """Absolute document and AER landing-page links on a page, without fragments."""
    out = set()
    for href in re.findall(r'href\s*=\s*"([^"]+)"', body.decode("utf-8", "replace"), re.I):
        url = urllib.parse.urljoin(page_url, html.unescape(href.strip())).split("#")[0]
        path = urllib.parse.urlsplit(url).path
        if url.startswith("http") and not NOT_DOC_RE.search(path) and (
                DOC_RE.search(path) or DOC_PATH_RE.search(url) or LANDING_RE.match(url)):
            out.add(url)
    return out


def fin_year(text):
    m = FY_RE.search(urllib.parse.unquote(text))
    return f"{m.group(1)}-{m.group(2)}" if m else None


def check_pages(watch):
    """Fetch every watched page and AER landing page; return (links by page, bodies of landing pages, unreachable)."""
    current, landing_bodies, unreachable = {}, {}, []
    queue = [(w["page_url"], w["who"]) for w in watch]
    queue += [(url, "AER") for _, url, _ in build_support.LANDING.values()]
    done = set()
    while queue:
        url, who = queue.pop(0)
        if url in done:
            continue
        done.add(url)
        try:
            body = get(url)
        except Exception as e:  # noqa: BLE001
            unreachable.append((who, url, str(e)[:100]))
            continue
        if b"bm-verify" in body or b"<title>Just a moment" in body:
            unreachable.append((who, url, "bot check page"))
            continue
        found = links(url, body)
        current[url] = found
        if LANDING_RE.match(url):
            landing_bodies[url] = body
        queue += [(u, "AER") for u in sorted(found) if LANDING_RE.match(u) and u not in done]
    return current, landing_bodies, unreachable


def check_changelog(landing_bodies):
    """AER landing-page changelog lines whose version is not in build_support.AER_VERSIONS."""
    out = []
    for url, body in sorted(landing_bodies.items()):
        fy = fin_year(urllib.parse.urlsplit(url).path)
        known = {v[0] for v in build_support.AER_VERSIONS.get(fy, [])}
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace"))))
        for day, seq in CHANGELOG_RE.findall(text):
            if int(seq) not in known:
                out.append((fy, int(seq), day, url))
    return sorted(set(out))


def file_sha(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=120) as r:
        for chunk in iter(lambda: r.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_files(rows):
    """Re-download every inventory document with a live URL; return (changed, unreachable, number checked)."""
    todo = {}
    for r in rows:
        url, want = (r.get("source_url") or "").strip(), (r.get("sha256") or "").strip()
        if r.get("local_path") and url and want and "web.archive.org" not in url:
            todo.setdefault(url, r)
    changed, unreachable = [], []

    def one(url):
        try:
            return url, file_sha(url), None
        except Exception as e:  # noqa: BLE001
            try:
                return url, file_sha(url), None
            except Exception:  # noqa: BLE001
                return url, None, str(e)[:100]

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for url, got, err in pool.map(one, sorted(todo)):
            r = todo[url]
            if err:
                unreachable.append((r["distributor"], r["local_path"], err))
            elif got != r["sha256"].strip():
                changed.append((r["distributor"], r["fin_year"], r["local_path"], url, r["sha256"][:12], got[:12]))
    return changed, unreachable, len(todo)


def archive(new_by_page, landing_bodies):
    """Save each new AER file linked from a report landing page, and that landing page, under sources/aer/."""
    today = datetime.date.today()
    saved = []
    for page, new in sorted(new_by_page.items()):
        if not LANDING_RE.match(page):
            continue
        fy = fin_year(urllib.parse.urlsplit(page).path) or "unknown"
        files = [u for u in sorted(new) if re.search(r"\.xlsx?$", urllib.parse.urlsplit(u).path, re.I)]
        for i, url in enumerate(files):
            suffix = f"_{i + 1}" if len(files) > 1 else ""
            path = f"sources/aer/AER_Consolidated_stakeholder_report_{fy}_retrieved{today:%Y%m%d}{suffix}.xlsx"
            with open(os.path.join(ROOT, path), "wb") as f:
                f.write(get(url))
            saved.append((path, url))
        if files:
            path = f"sources/aer/landing/AER_Consolidated_stakeholder_report_{fy}_landing_{today:%Y%m%d}.html"
            with open(os.path.join(ROOT, path), "wb") as f:
                f.write(landing_bodies[page])
            saved.append((path, page))
    return saved


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def report(new, gone, changelog, changed, unreachable_pages, unreachable_files, saved, files_checked):
    md = [f"# Source release check, {datetime.date.today():%Y-%m-%d}", "",
          "| Check | Found |", "|---|---|",
          f"| New AER versions (landing-page changelog) | {len(changelog)} |",
          f"| New document links on watched pages | {sum(len(v) for v in new.values())} |",
          f"| Links gone from watched pages | {sum(len(v) for v in gone.values())} |",
          f"| Files changed in place (SHA-256) | {f'{len(changed)} of {files_checked}' if files_checked else 'not checked'} |",
          f"| Pages or files not reachable | {len(unreachable_pages) + len(unreachable_files)} |", ""]
    if changelog:
        md += ["## New AER versions", "", "| Year | Version | Published | Landing page |", "|---|---|---|---|"]
        md += [f"| {fy} | v{seq} | {day} | {url} |" for fy, seq, day, url in changelog] + [""]
    for title, by_page in (("New document links", new), ("Links gone", gone)):
        if any(by_page.values()):
            md += [f"## {title}", ""]
            for page, urls in sorted(by_page.items()):
                if urls:
                    md += [f"- {page}"] + [f"  - {u}" for u in sorted(urls)]
            md += [""]
    if changed:
        md += ["## Files changed in place", "", "| Distributor | Year | Local path | Inventory | Now |", "|---|---|---|---|---|"]
        md += [f"| {d} | {fy} | [{p}]({u}) | `{a}` | `{b}` |" for d, fy, p, u, a, b in changed] + [""]
    if saved:
        md += ["## Archived by this run", ""] + [f"- `{p}` from {u}" for p, u in saved] + [""]
    if unreachable_pages or unreachable_files:
        md += ["## Not reachable (check by hand or with a real browser)", ""]
        md += [f"- {who}: {url} ({err})" for who, url, err in unreachable_pages]
        md += [f"- {who}: `{p}` ({err})" for who, p, err in unreachable_files] + [""]
    md += ["Next: follow `docs/update-and-validate.md` (\"Find and load new releases\")."]
    return "\n".join(md) + "\n"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-files", action="store_true", help="skip re-downloading inventory files")
    ap.add_argument("--report", help="also write the Markdown report to this file")
    ap.add_argument("--archive", action="store_true", help="save new AER files and their landing page under sources/aer/")
    ap.add_argument("--accept", action="store_true", help="record the current links in sources/watch_seen.csv")
    args = ap.parse_args(argv)

    watch = read_csv(WATCH)
    seen = {}
    for r in read_csv(SEEN):
        seen.setdefault(r["page_url"], set()).add(r["link"])
    current, landing_bodies, unreachable_pages = check_pages(watch)
    new = {p: found - seen.get(p, set()) for p, found in current.items()}
    gone = {p: seen.get(p, set()) - found for p, found in current.items()}
    changelog = check_changelog(landing_bodies)
    changed, unreachable_files, files_checked = ([], [], 0) if args.no_files else check_files(read_csv(INV))
    saved = archive(new, landing_bodies) if args.archive else []

    md = report(new, gone, changelog, changed, unreachable_pages, unreachable_files, saved, files_checked)
    sys.stdout.write(md)
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w") as f:
            f.write(md)
    if args.accept:
        # pages that could not be reached keep their recorded links
        merged = {p: v for p, v in seen.items() if p not in current} | current
        with open(SEEN, "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["page_url", "link"])
            w.writerows((p, u) for p in sorted(merged) for u in sorted(merged[p]))
    found = changelog or changed or any(new.values()) or any(gone.values())
    return 2 if found and not args.accept else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
