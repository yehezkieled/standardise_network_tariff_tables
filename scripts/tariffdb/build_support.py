"""Reference data for the tariff database: distributors, the document registry (series, versions, statuses, dates)
and which distributors' prices each AER version carries.

Documents come from sources/inventory.csv plus EXTRA_DOCUMENTS (AER versions known from the AER changelog that are
login-gated and so not retrievable, and the saved AER landing pages that hold those changelogs).
"""
import csv
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INVENTORY = os.path.join(ROOT, "sources", "inventory.csv")
INVENTORY_COMMIT_DATE = "2026-10-05"  # date sources/inventory.csv (with its sha256 values) was committed

DISTRIBUTORS = [
    # distributor_id, name (scripts/schema.py CANON), aer_label, state, iana_timezone, observes_dst
    ("ausgrid", "Ausgrid", "Ausgrid", "NSW", "Australia/Sydney", 1),
    ("ausnet", "AusNet Services", "AusNet Services", "VIC", "Australia/Melbourne", 1),
    ("citipower", "CitiPower", "CitiPower", "VIC", "Australia/Melbourne", 1),
    ("endeavour", "Endeavour Energy", "Endeavour Energy", "NSW", "Australia/Sydney", 1),
    ("energex", "Energex", "Energex", "QLD", "Australia/Brisbane", 0),
    ("ergon", "Ergon Energy", "Ergon Energy", "QLD", "Australia/Brisbane", 0),
    ("essential", "Essential Energy", "Essential Energy", "NSW", "Australia/Sydney", 1),
    ("evoenergy", "Evoenergy", "Evoenergy", "ACT", "Australia/Sydney", 1),
    ("jemena", "Jemena", "Jemena", "VIC", "Australia/Melbourne", 1),
    ("powerwater", "Power and Water Corporation", "Power and Water Corporation", "NT", "Australia/Darwin", 0),
    ("powercor", "Powercor", "Powercor", "VIC", "Australia/Melbourne", 1),
    ("sapn", "SA Power Networks", "SA Power Networks", "SA", "Australia/Adelaide", 1),
    ("tasnetworks", "TasNetworks", "TasNetworks", "TAS", "Australia/Hobart", 1),
    ("unitedenergy", "United Energy", "United Energy", "VIC", "Australia/Melbourne", 1),
]
DISTRIBUTORS = [dict(zip(("distributor_id", "name", "aer_label", "state", "iana_timezone", "observes_dst"), d))
                for d in DISTRIBUTORS]
ID_BY_NAME = {d["name"]: d["distributor_id"] for d in DISTRIBUTORS}
BY_STATE = {}
for _d in DISTRIBUTORS:
    BY_STATE.setdefault(_d["state"], []).append(_d["distributor_id"])

FIN_YEAR_DATES = {"2023-24": ("2023-07-01", "2024-06-30"), "2024-25": ("2024-07-01", "2025-06-30"),
                  "2025-26": ("2025-07-01", "2026-06-30"), "2026-27": ("2026-07-01", "2027-06-30")}

LANDING = {
    "2025-26": ("sources/aer/landing/AER_Consolidated_stakeholder_report_2025-26_landing_20261006.html",
                "https://www.aer.gov.au/documents/aer-consolidated-stakeholder-report-2025-26",
                "f42984f5cb152afa95613fa3fb44fbeaae808c0d926b3bd2c1687181e7da4b2b"),
    "2026-27": ("sources/aer/landing/AER_Consolidated_stakeholder_report_2026-27_landing_20261006.html",
                "https://www.aer.gov.au/documents/aer-2026-27-consolidated-stakeholder-report-20-may-2026",
                "f836ea1623018129b5c40fd4052c5b7cfda05a030702274de95a21eefbf51fee"),
}

# AER consolidated stakeholder report versions, from the changelog on each landing page (quoted verbatim).
# coverage: state -> price status of that state's distributors in the version.
AER_VERSIONS = {
    "2025-26": [
        (1, "2025-04-08", "On 8 April we published version 1 with: Proposed prices for ACT, NSW, NT, TAS, and VIC.",
         {s: "proposed" for s in ("ACT", "NSW", "NT", "TAS", "VIC")}),
        (2, "2025-04-10", "On 10 April we published version 2 with: Updated price movement analysis for AusNet Services "
         "and United Energy to correct errors.", {s: "proposed" for s in ("ACT", "NSW", "NT", "TAS", "VIC")}),
        (3, "2025-05-14", "On 14 May we published version 3 with: Approved prices for ACT, NSW, TAS, and VIC (including "
         "updated prices for Essential Energy, Jemena, and TasNetworks, and a single additional price for Ausgrid). Added "
         "proposed prices for QLD and SA. Retained proposed prices for NT.",
         {**{s: "approved" for s in ("ACT", "NSW", "TAS", "VIC")}, **{s: "proposed" for s in ("QLD", "SA", "NT")}}),
        (4, "2025-05-16", "On 16 May we published version 4 with: Approved prices for NT (including updated prices).",
         {**{s: "approved" for s in ("ACT", "NSW", "TAS", "VIC", "NT")}, **{s: "proposed" for s in ("QLD", "SA")}}),
        (5, "2025-05-26", "On 26 May we published version 5 with: Approved prices for QLD and SA (including updated prices "
         "for Energex public lighting). Tariff codes for standard network tariffs.",
         {s: "approved" for s in ("ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC")}),
    ],
    "2026-27": [
        (1, "2026-04-02", "On 2 April we published version 1 with: Proposed prices for ACT, NSW, NT, QLD, SA, and TAS.",
         {s: "proposed" for s in ("ACT", "NSW", "NT", "QLD", "SA", "TAS")}),
        (2, "2026-04-24", "On 24 April we published version 2 with: Approved prices for ACT, NSW, NT, QLD, SA, and TAS. "
         "Updated prices for Endeavour Energy from 14 April resubmission.",
         {s: "approved" for s in ("ACT", "NSW", "NT", "QLD", "SA", "TAS")}),
        (3, "2026-05-08", "On 8 May we published version 3 with: Approved prices for ACT, NSW, NT, QLD, SA and TAS "
         "(unchanged from version 2). Proposed prices for VIC.",
         {**{s: "approved" for s in ("ACT", "NSW", "NT", "QLD", "SA", "TAS")}, "VIC": "proposed"}),
        (4, "2026-05-20", "On 20 May we published version 4 with: Approved prices for all jurisdictions.",
         {s: "approved" for s in ("ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC")}),
        (5, "2026-08-14", "On 14 August we published version 5 with: For AusNet Services, updated tariff codes for the "
         "following public lighting services",
         {s: "approved" for s in ("ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC")}),
    ],
}

AER_CONSOLIDATED_FILES = {
    ("2025-26", 1): "sources/aer/AER_Consolidated_stakeholder_report_2025-26_v1_wayback.xlsx",
    ("2025-26", 5): "sources/aer/AER_Consolidated_stakeholder_report_2025-26_v5.xlsx",
    ("2026-27", 5): "sources/aer/AER_Consolidated_stakeholder_report_2026-27_26Aug2026.xlsx",
}

def version_date_text(fy, seq):
    """Publication date of an AER consolidated version as '8 Apr 2025' (from the changelog)."""
    y, m, d = (int(x) for x in dict((v[0], v[1]) for v in AER_VERSIONS[fy])[seq].split("-"))
    return f"{d} {['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][m - 1]} {y}"


def version_status_text(fy, seq):
    """'proposed' / 'approved' when every distributor the version carries has that status, else 'mixed'."""
    statuses = set(dict((v[0], v[3]) for v in AER_VERSIONS[fy])[seq].values())
    return statuses.pop() if len(statuses) == 1 else "mixed"


def version_coverage_status(fy, seq):
    """{distributor_id: price status} of the distributors an AER consolidated version carries."""
    cov = dict((v[0], v[3]) for v in AER_VERSIONS[fy])[seq]
    return {did: status for state, status in cov.items() for did in BY_STATE[state]}


def unheld_aer_documents(source_documents):
    """(fin_year, document, publication_date, price_status) of every AER-authored version that is not held."""
    return [(d["fin_year"], d["title"].split(" - superseded")[0], d["publication_date"], d["price_status"])
            for d in source_documents if d["author"] == "AER" and d["retrieval_status"] == "not_retrievable"]


DOC_TYPE_RULES = [
    (r"consolidated stakeholder report", "aer_consolidated_stakeholder_report"),
    (r"per-DNSP stakeholder report", "aer_stakeholder_report"),
    (r"Pricing Proposal Overview", "pricing_proposal_overview"),
    (r"Tariff Summary", "tariff_summary"),
    (r"Statement of Tariff Classes", "statement_of_tariff_classes"),
    (r"[Ss]chedule of [Cc]harges", "schedule_of_charges"),
    (r"tariff application and price guide", "price_guide"),
    (r"Pricing Schedule", "pricing_schedule"),
    (r"Schedule of tariffs|Tariff Schedule", "tariff_schedule"),
    (r"[Pp]ricing [Pp]roposal|Pricing PDF|Final Pricing", "pricing_proposal"),
    (r"Price List|Standard Control Service Tariffs|NOT AVAILABLE", "price_list"),
]

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def document_type(role):
    for rx, t in DOC_TYPE_RULES:
        if re.search(rx, role):
            return t
    raise ValueError(f"no document type for {role!r}")


def date_in_name(path):
    """Publication date printed in a file name (e.g. _31Mar2023, _07May2026, _20260515, _210525); None if absent."""
    b = os.path.basename(path)
    m = re.search(r"_(\d{1,2})(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(20\d\d)", b)
    if m:
        return f"{m.group(3)}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = re.search(r"_(20\d\d)(\d\d)(\d\d)\b", b)
    if m and "wayback" not in b:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"_(\d\d)(\d\d)(\d\d)_wayback", b)
    if m:
        return f"20{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def wayback_date(url):
    m = re.search(r"web\.archive\.org/web/(\d{4})(\d{2})(\d{2})", url or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def version_label(role, path):
    m = re.search(r"\b(v\d+(?:\.\d+)?)\b", role)
    if m:
        return m.group(1)
    m = re.search(r"updated (\d+ \w+ \d{4})", role)
    if m:
        return f"updated {m.group(1)}"
    return "as published"


# Series that hold more than one document: (series key) -> {local_path or extra id: (version_seq, version_label)}
EXTRA_DOCUMENTS = []
for _fy, _versions in AER_VERSIONS.items():
    for _seq, _date, _quote, _cov in _versions:
        if (_fy, _seq) in AER_CONSOLIDATED_FILES:
            continue
        EXTRA_DOCUMENTS.append({
            "document_id": f"aer-consolidated-{_fy}-v{_seq}", "series_id": f"aer-all-{_fy}-consolidated",
            "version_label": f"v{_seq}", "version_seq": _seq, "author": "AER", "distributor_id": None, "fin_year": _fy,
            "document_type": "aer_consolidated_stakeholder_report", "recon_side": "AER",
            "title": f"AER consolidated stakeholder report {_fy} v{_seq} - superseded; the AER takes superseded files "
                     f"private (login-gated) and no archived copy exists",
            "publication_date": _date, "publication_date_basis": "aer_changelog", "retrieval_status": "not_retrievable",
            "local_path": None, "source_url": LANDING[_fy][1], "access_note": "superseded version, login-gated on aer.gov.au",
            "sha256": None, "retrieved_on": None, "retrieved_on_basis": None, "committed_in_repo": 0,
        })
EXTRA_DOCUMENTS.append({
    "document_id": "aer-stakeholder-sapn-2024-25-original", "series_id": "aer-sapn-2024-25-aer-stakeholder-report",
    "version_label": "original (3 May 2024)", "version_seq": 1, "author": "AER", "distributor_id": "sapn",
    "fin_year": "2024-25", "document_type": "aer_stakeholder_report", "recon_side": "AER",
    "title": "AER stakeholder report SA Power Networks 2024-25, original release replaced on 17 Jul 2024 (login-gated)",
    "publication_date": "2024-05-03", "publication_date_basis": "aer_pricing_page", "retrieval_status": "not_retrievable",
    "local_path": None, "source_url": "https://www.aer.gov.au/industry/networks/pricing-proposals/sa-power-networks-2024-25-pricing-proposal",
    "access_note": "superseded version, login-gated on aer.gov.au", "sha256": None, "retrieved_on": None,
    "retrieved_on_basis": None, "committed_in_repo": 0,
})
for _fy, (_path, _url, _sha) in LANDING.items():
    EXTRA_DOCUMENTS.append({
        "document_id": f"aer-consolidated-{_fy}-landing-page", "series_id": f"aer-all-{_fy}-landing-page",
        "version_label": "as retrieved 2026-10-06", "version_seq": 1, "author": "AER", "distributor_id": None,
        "fin_year": _fy, "document_type": "aer_consolidated_stakeholder_report", "recon_side": "AER",
        "title": f"AER landing page of the {_fy} consolidated stakeholder report (version changelog)",
        "publication_date": None, "publication_date_basis": None, "retrieval_status": "retrieved", "local_path": _path,
        "source_url": _url, "access_note": "web page saved as HTML; holds the version changelog quoted in version_coverage",
        "sha256": _sha, "retrieved_on": "2026-10-06", "retrieved_on_basis": "saved_page", "committed_in_repo": 1,
    })

# Several documents per (author, distributor, year, type) that are distinct publications, not versions of one another.
SERIES_VARIANTS = {
    "sources/dnsp/evoenergy/Evoenergy_Schedule_of_Charges_2026-27_AER_approved_April2026.xlsx": "excl-lfit",
    "sources/dnsp/evoenergy/Evoenergy_Schedule_of_Charges_2026-27_incl_LFiT_June2026.xlsx": "incl-lfit",
}
VERSIONS = {
    "sources/aer/AER_Consolidated_stakeholder_report_2025-26_v1_wayback.xlsx": (1, "v1"),
    "sources/aer/AER_Consolidated_stakeholder_report_2025-26_v5.xlsx": (5, "v5"),
    "sources/aer/AER_Consolidated_stakeholder_report_2026-27_26Aug2026.xlsx": (5, "v5"),
    "sources/aer/2024-25_stakeholder_reports/AER_Stakeholder_report_SAPN_2024-25_updated17Jul2024.xlsx":
        (2, "updated 17 Jul 2024"),
}
PRICE_STATUS_OVERRIDES = {
    "sources/aer/AER_Consolidated_stakeholder_report_2025-26_v1_wayback.xlsx": "proposed",
    # the distributor-side 2025-26 SAPN document is the Initial Pricing Proposal Overview, not a price list
    "sources/dnsp/sapn/SAPN_Initial_Pricing_Proposal_Overview_2025-26_id333252.pdf": "proposed",
}
PUBLICATION_DATES = {  # from the AER changelogs
    "sources/aer/AER_Consolidated_stakeholder_report_2025-26_v1_wayback.xlsx": ("2025-04-08", "aer_changelog"),
    "sources/aer/AER_Consolidated_stakeholder_report_2025-26_v5.xlsx": ("2025-05-26", "aer_changelog"),
    "sources/aer/AER_Consolidated_stakeholder_report_2026-27_26Aug2026.xlsx": ("2026-08-14", "aer_changelog"),
}


def read_inventory():
    with open(INVENTORY, newline="") as f:
        return list(csv.DictReader(f))


def documents():
    """All source_document rows (inventory + extras), each with its series."""
    out = []
    for r in read_inventory():
        path = r["local_path"] or None
        did = ID_BY_NAME.get(r["distributor"])
        author = "AER" if r["side"] == "AER" else "distributor"
        dtype = document_type(r["role"])
        fy = r["fin_year"]
        seq, label = VERSIONS.get(path, (1, version_label(r["role"], path or "")))
        variant = SERIES_VARIANTS.get(path)
        if dtype == "aer_consolidated_stakeholder_report":
            doc_id, series = f"aer-consolidated-{fy}-v{seq}", f"aer-all-{fy}-consolidated"
        else:
            doc_id = slug(os.path.splitext(os.path.basename(path))[0]) if path else \
                f"{did}-{fy}-{dtype.replace('_', '-')}-not-retrievable"
            # the AER-hosted copy of a distributor proposal and the distributor's own publication are separate series
            series = f"{r['side'].lower().replace('_', '')}-{did or 'all'}-{fy}-{dtype.replace('_', '-')}" + (
                f"-{variant}" if variant else "")
        if r["side"] == "AER":
            status = "approved"
        elif r["side"] == "AER_HOSTED":
            status = "unverified"  # hosting says nothing about whether these prices were proposed or approved
        else:
            status = "published"
        status = PRICE_STATUS_OVERRIDES.get(path, status)
        pub, pub_basis = PUBLICATION_DATES.get(path, (None, None))
        if pub is None and path and date_in_name(path):
            pub, pub_basis = date_in_name(path), "file_name"
        wb = wayback_date(r["source_url"])
        out.append({
            "document_id": doc_id, "series_id": series, "version_label": label, "version_seq": seq, "author": author,
            "distributor_id": did, "fin_year": fy, "document_type": dtype, "recon_side": r["side"],
            "price_status": status, "title": r["role"], "publication_date": pub, "publication_date_basis": pub_basis,
            "retrieval_status": "retrieved" if path else "not_retrievable", "local_path": path,
            "source_url": r["source_url"] or None, "access_note": r["access_note"] or None,
            "sha256": r["sha256"] or None,
            "retrieved_on": (wb or INVENTORY_COMMIT_DATE) if path else None,
            "retrieved_on_basis": ("wayback_capture" if wb else "inventory_commit") if path else None,
            "committed_in_repo": 1 if (path and (wb or r["side"] == "AER")) else 0,
        })
    out += [dict(d) for d in EXTRA_DOCUMENTS]
    for d in out:
        if d["document_type"] == "aer_consolidated_stakeholder_report" and "landing" not in d["document_id"]:
            cov = dict((v[0], v[3]) for v in AER_VERSIONS[d["fin_year"]])[d["version_seq"]]
            statuses = set(cov.values())
            d["price_status"] = statuses.pop() if len(statuses) == 1 else "mixed"
        elif "price_status" not in d:
            # the superseded SAPN 2024-25 report carried approved prices; a landing page carries none
            d["price_status"] = "approved" if d["document_type"] == "aer_stakeholder_report" else "published"
    return out


def series_rows(docs):
    seen = {}
    for d in docs:
        if d["series_id"] in seen:
            continue
        title = d["title"]
        if d["document_type"] == "aer_consolidated_stakeholder_report" and "landing" not in d["series_id"]:
            title = f"AER consolidated stakeholder report {d['fin_year']}"
        seen[d["series_id"]] = {"series_id": d["series_id"], "author": d["author"], "distributor_id": d["distributor_id"],
                                "fin_year": d["fin_year"], "title": title}
    return list(seen.values())


def version_coverage(docs):
    """Which distributors' prices each AER consolidated version carries, and with what status (from the changelog)."""
    by_series_seq = {(d["series_id"], d["version_seq"]): d for d in docs}
    rows = []
    for fy, versions in AER_VERSIONS.items():
        landing = f"aer-consolidated-{fy}-landing-page"
        for seq, _date, quote, cov in versions:
            doc = by_series_seq[(f"aer-all-{fy}-consolidated", seq)]
            for state, status in sorted(cov.items()):
                for did in BY_STATE[state]:
                    rows.append({"document_id": doc["document_id"], "distributor_id": did, "price_status": status,
                                 "evidence_document_id": landing, "locator": "html:text", "quote": quote})
    return rows
