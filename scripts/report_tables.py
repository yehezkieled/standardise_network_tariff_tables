"""Generate the data-driven markdown sections of the report from out/*.csv and sources/inventory.csv.

Writes out/report_tables.md with: headline grid, per distributor x year findings, explanation glossary counts,
source inventory with URLs. scripts/write_report.py embeds these sections in the report.
"""
import csv, os, re, json, sys
from collections import Counter, defaultdict
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from tariffdb import build_support as bs
YEARS = ["2023-24", "2024-25", "2025-26", "2026-27"]

def rd(p):
    with open(os.path.join(ROOT, p)) as f:
        return list(csv.DictReader(f))

grid = rd("out/match_grid.csv"); det = rd("out/recon_detail.csv"); inv = rd("sources/inventory.csv"); tl = rd("out/format_timeline.csv")
notes = json.load(open(os.path.join(ROOT, "notes/format_notes.json")))
out = []
W = out.append

def pct(x):
    return "n/a" if x in ("", None) else f"{float(x):.1f}%"

W("## Headline grid: reconciled rate (exact-equal rate) and components compared\n")
W("Reconciled = components equal, equal after rounding, or whose difference has a documented explanation. Cells marked `AER-hosted` use the AER-hosted copy of the distributor's document as the distributor side.\n")
W("| Distributor | " + " | ".join(YEARS) + " |")
W("|---|" + "---|" * len(YEARS))
G = {(g["distributor"], g["fin_year"]): g for g in grid}
for d in dict.fromkeys(g["distributor"] for g in grid):
    cells = []
    for y in YEARS:
        g = G[(d, y)]
        if not g["components_compared"] or g["components_compared"] == "0":
            cells.append("n/a (" + (g["note"] or "no data") + ")")
        else:
            tag = " AER-hosted" if g["dnsp_side"] == "AER_HOSTED" else ""
            cells.append(f"{pct(g['reconciled_rate'])} ({pct(g['match_rate'])}) n={g['components_compared']}{tag}")
    W(f"| {d} | " + " | ".join(cells) + " |")
W("")

W("## Per distributor and year\n")
by = defaultdict(list)
for r in det:
    by[(r["distributor"], r["fin_year"])].append(r)
for d in dict.fromkeys(g["distributor"] for g in grid):
    W(f"### {d}\n")
    for y in YEARS:
        g = G[(d, y)]; rs = by[(d, y)]
        W(f"**{y}** - AER side: {g['aer_side']} ({g['aer_basis'] or '-'}); distributor side: {g['dnsp_side']} ({g['dnsp_basis'] or '-'}). "
          f"Codes: AER {g['aer_codes']}, distributor {g['dnsp_codes']}, matched {g['codes_matched']} (by name {g['codes_mapped_by_name']}, joint-code variants {g.get('codes_joint_variant', 0)}). "
          f"Components compared {g['components_compared']}: equal {g['equal']}, after rounding {g['rounding']}, explained {g['explainable']}, unexplained {g['unexplained']}; AER-only components {g['components_aer_only']}, distributor-only components {g['components_dnsp_only']}.")
        if g["note"]:
            W(f"  Note: {g['note']}")
        n = notes.get(f"{d}|{y}")
        if n:
            W(f"  Distributor format: {n}")
        vd = [r for r in rs if r["status"] == "value_differs"]
        if vd:
            ex = Counter(r["explanation"] for r in vd)
            parts = []
            for e, c in ex.most_common():
                sub = [r for r in vd if r["explanation"] == e]
                diffs = [float(r["abs_diff"]) for r in sub if r["abs_diff"] != ""]
                units = Counter(r["unit_std"] for r in sub).most_common(1)[0][0]
                parts.append(f"{e} x{c} (typical diff {sorted(diffs)[len(diffs)//2]:+.4f} {units}; e.g. {sub[0]['tariff_code']} {sub[0]['component']}: AER {sub[0]['aer_value']} {sub[0]['aer_unit']} vs distributor {sub[0]['dnsp_value']} {sub[0]['dnsp_unit']})")
            W("  Value differences: " + "; ".join(parts))
        ao = [r for r in rs if r["status"] == "aer_only_code"]
        if ao:
            W("  AER-only codes: " + "; ".join(f"{r['tariff_code']} ({r['tariff_name'][:40]}){' [' + r['explanation'] + ']' if r['explanation'] else ''}" for r in ao[:40]) + (" ..." if len(ao) > 40 else ""))
        do = [r for r in rs if r["status"] == "dnsp_only_code"]
        if do:
            ex = Counter(r["explanation"] or "no explanation" for r in do)
            W("  Distributor-only codes (" + ", ".join(f"{k} x{v}" for k, v in ex.most_common()) + "): " + "; ".join(f"{r['tariff_code']} ({r['tariff_name'][:35]})" for r in do[:30]) + (" ..." if len(do) > 30 else ""))
        ca = [r for r in rs if r["status"] == "aer_only_component"]
        if ca:
            W("  AER-only components: " + "; ".join(f"{r['tariff_code']} {r['component']} {r['aer_value']} {r['aer_unit']}" for r in ca[:12]) + (f" ... ({len(ca)} total)" if len(ca) > 12 else ""))
        cd = [r for r in rs if r["status"] == "dnsp_only_component"]
        if cd:
            W("  Distributor-only components: " + "; ".join(f"{r['tariff_code']} {r['dnsp_component']} {r['dnsp_value']} {r['dnsp_unit']}{' [' + r['explanation'] + ']' if r['explanation'] else ''}" for r in cd[:12]) + (f" ... ({len(cd)} total)" if len(cd) > 12 else ""))
        W("")

W("## AER consolidated report versions\n")
W("The AER reissued each consolidated report five times; the landing page only ever links the latest file and the AER "
  "takes each superseded file private. Every held version is reconciled against the distributor documents the same "
  "way as the latest (`.venv/bin/python scripts/reconcile.py --aer-version <document_id>`, outputs in "
  "`out/versions/<document_id>/`; the default run writes all of them to `out/version_grid.csv`). Distributors a "
  "version does not carry are not compared for it.\n")
W("| Version | Published | Prices | Held copy | Compared | Equal | Rounding | Explained | Unexplained | AER-only codes |")
W("|---|---|---|---|---|---|---|---|---|---|")
vg = defaultdict(list)
for g in rd("out/version_grid.csv"):
    vg[g["document_id"]].append(g)
for (fy, seq), held in bs.AER_CONSOLIDATED_FILES.items():
    doc = f"aer-consolidated-{fy}-v{seq}"
    tot = lambda k: sum(int(g[k] or 0) for g in vg[doc])
    W(f"| {fy} v{seq} | {bs.version_date_text(fy, seq)} | {bs.version_status_text(fy, seq)} | `{held}` | "
      + " | ".join(str(tot(k)) for k in ("components_compared", "equal", "rounding", "explainable", "unexplained",
                                        "codes_aer_only")) + " |")
W("")
W("AER versions not held, so not reconciled:\n")
W("| Financial year | Document | Published | Prices | Why not held |")
W("|---|---|---|---|---|")
for fy, doc, date, status, reason in bs.unheld_aer_documents(rd("data/tariffdb/tables/source_document.csv")):
    W(f"| {fy} | {doc} | {date} | {status} | {reason} |")
W("")
W("Proposed (v1) against approved prices: the 2025-26 v1 file carries proposed prices for ACT, NSW, NT, TAS and VIC "
  "only, and prints no tariff codes ('#REF!'), so each v1 row takes its code from the latest version through the AER "
  "tariff ID both print. Per distributor:\n")
W("| Version | Distributor | Prices | Compared | Equal | Rounding | Explained | Unexplained |")
W("|---|---|---|---|---|---|---|---|")
for doc, gs in vg.items():
    for g in gs:
        if int(g["components_compared"] or 0):
            W(f"| {g['fin_year']} {g['version']} | {g['distributor']} | {g['price_status']} | {g['components_compared']} | "
              f"{g['equal']} | {g['rounding']} | {g['explainable']} | {g['unexplained']} |")
W("")

W("## Source inventory (exact URLs)\n")
W("| Side | Distributor | FY | Document | Local file | URL | Access note |")
W("|---|---|---|---|---|---|---|")
for r in sorted(inv, key=lambda r: (r["distributor"], r["fin_year"], r["side"])):
    W(f"| {r['side']} | {r['distributor']} | {r['fin_year']} | {r['role']} | `{r['local_path']}` | {r['source_url'] or '-'} | {r['access_note']} |")
W("")
with open(os.path.join(ROOT, "out/report_tables.md"), "w") as f:
    f.write("\n".join(out))
print("wrote out/report_tables.md", sum(len(x) for x in out), "chars")
