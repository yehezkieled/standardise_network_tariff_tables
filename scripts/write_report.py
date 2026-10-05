"""Assemble the final report: notes/report_head.md with placeholders filled from notes/report_sections/*.md,
the generated tables (out/report_tables.md) and the final grid print. Writes out/report.md and the Lavish-compatible out/report.html page."""
import os, json
import markdown
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
head = open(os.path.join(ROOT, "notes/report_head.md")).read()
summary = json.load(open(os.path.join(ROOT, "out/recon_summary.json")))
grid_rows = summary["grid"]
sc = summary["status_counts"]
nums = {
    "components_compared": sum(int(g["components_compared"] or 0) for g in grid_rows),
    "cells_compared": sum(1 for g in grid_rows if int(g["components_compared"] or 0) > 0),
    "na_cells": sum(1 for g in grid_rows if int(g["components_compared"] or 0) == 0),
    "equal": sc.get("equal", 0), "rounding": sc.get("equal_after_rounding", 0), "value_differs": sc.get("value_differs", 0),
    "unexplained": sum(int(g["unexplained"] or 0) for g in grid_rows),
    "aer_only_codes": sc.get("aer_only_code", 0), "dnsp_only_codes": sc.get("dnsp_only_code", 0),
    "hosted_cells": sum(1 for g in grid_rows if g["dnsp_side"] == "AER_HOSTED" and int(g["components_compared"] or 0) > 0),
}
for key in ("SUMMARY", "EXPLAINED", "GENUINE", "FORMAT", "UNSURE", "RECS"):
    p = os.path.join(ROOT, "notes/report_sections", key.lower() + ".md")
    txt = open(p).read().strip() if os.path.exists(p) else "(not written)"
    if key == "SUMMARY":
        txt = txt.format(**nums)
    head = head.replace("__" + key + "__", txt)
head = head.replace("__GRIDPRINT__", "See the generated per-distributor tables in section 8.")
tables = open(os.path.join(ROOT, "out/report_tables.md")).read()
report = head + "\n\n## 8. Detail per distributor and year, and source inventory\n\n" + tables
out = os.path.join(ROOT, "out/report.md")
open(out, "w").write(report)
print("wrote", out, len(report), "chars")

page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>AER tariff reconciliation</title><style>body{font:16px system-ui;line-height:1.6;margin:2rem auto;padding:0 1rem;max-width:90rem;color:#172033}table{display:block;overflow:auto;border-collapse:collapse;max-width:100%}td,th{padding:.5rem;border:1px solid #d8dee8;text-align:left}pre{white-space:pre-wrap}a,code{overflow-wrap:anywhere}h1,h2,h3{line-height:1.2}</style><body>' + markdown.markdown(report, extensions=["tables", "fenced_code"]) + '</body></html>'
open(os.path.join(ROOT, "out/report.html"), "w").write(page)
