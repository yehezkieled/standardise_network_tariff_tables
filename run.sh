#!/usr/bin/env bash
# Reproduce the whole reconciliation: fetch any source document that is not in the checkout (from the URL recorded in
# sources/inventory.csv), parse the AER files and every distributor file, reconcile, and assemble the report.
#   ./run.sh                 -> writes out/ and refreshes REPORT.md and discrepancies.csv at the repository root
#   ./run.sh --no-fetch      -> skip the download step (offline; fails later if a source file is missing)
# Requires: uv (https://docs.astral.sh/uv/).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  uv venv --python 3.12 .venv
fi
uv pip install --python .venv/bin/python openpyxl pandas pdfplumber xlrd rapidocr_onnxruntime markdown pyyaml
PY=.venv/bin/python
mkdir -p out/dnsp
if [ "${1:-}" != "--no-fetch" ]; then echo "== sources"; $PY scripts/fetch_sources.py; fi
echo "== AER side"; $PY scripts/parse_aer.py 2>&1 | grep -v -e UserWarning -e "warn(msg)" | tail -3
for s in scripts/dnsp/*.py; do
  [ "$(basename "$s")" = "__init__.py" ] && continue
  echo "== DNSP side: $s"; $PY "$s" 2>&1 | grep -v -e UserWarning -e "warn(msg)" | tail -3
done
echo "== reconcile"; $PY scripts/reconcile.py
echo "== report"; $PY scripts/report_tables.py && $PY scripts/write_report.py
cp out/report.md REPORT.md && cp out/discrepancies.csv discrepancies.csv
echo "done: REPORT.md discrepancies.csv (full outputs in out/: discrepancies.xlsx recon_detail.csv match_grid.csv format_timeline.csv recon_summary.json aer_long.csv dnsp/*.csv)"
