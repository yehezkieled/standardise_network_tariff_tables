"""Offline tests for scripts/check_releases.py: link and changelog parsing on the committed AER landing pages, and the
watch files' consistency. Nothing is downloaded."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_releases
import build_support


class CheckReleasesTest(unittest.TestCase):
    def test_landing_page_links_include_the_inventory_file(self):
        inventory = {r['source_url'] for r in check_releases.read_csv(ROOT / 'sources/inventory.csv')}
        for fy, (path, url, _) in build_support.LANDING.items():
            found = check_releases.links(url, (ROOT / path).read_bytes())
            self.assertTrue(found & inventory, f'{fy}: no inventory file among {sorted(found)}')
            self.assertTrue(all(not u.endswith(('.png', '.css', '.js')) for u in found))

    def test_committed_changelogs_are_all_registered(self):
        bodies = {url: (ROOT / path).read_bytes() for path, url, _ in build_support.LANDING.values()}
        self.assertEqual(check_releases.check_changelog(bodies), [])

    def test_unregistered_version_is_reported(self):
        url = 'https://www.aer.gov.au/documents/aer-2026-27-consolidated-stakeholder-report-20-may-2026'
        body = b'<p>On <strong>3 September</strong> we published version 6 with: updated prices.</p>'
        self.assertEqual(check_releases.check_changelog({url: body}), [('2026-27', 6, '3 September', url)])

    def test_document_links_are_filtered_and_resolved(self):
        body = (b'<a href="/system/files/2027-04/Report%202027%E2%80%9328.xlsx">x</a><a href="/about">y</a>'
                b'<a href="https://x.example/-/media/logo.png">z</a><a href="list.pdf?rev=1#p2">w</a>')
        self.assertEqual(check_releases.links('https://www.aer.gov.au/page/', body), {
            'https://www.aer.gov.au/system/files/2027-04/Report%202027%E2%80%9328.xlsx',
            'https://www.aer.gov.au/page/list.pdf?rev=1'})
        self.assertEqual(check_releases.fin_year('Report%202027%E2%80%9328.xlsx'), '2027-28')

    def test_watch_files_agree(self):
        watched = {r['page_url'] for r in check_releases.read_csv(ROOT / 'sources/watch.csv')}
        seen = {r['page_url'] for r in check_releases.read_csv(ROOT / 'sources/watch_seen.csv')}
        stray = {p for p in seen - watched if not check_releases.LANDING_RE.match(p)}
        self.assertEqual(stray, set(), 'pages in watch_seen.csv that sources/watch.csv no longer watches')


if __name__ == '__main__':
    unittest.main()
