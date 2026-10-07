"""Write the schema reference from the table spec and the data: docs/schema.md and docs/schema-erd.svg.

  .venv/bin/python scripts/tariffdb/schema_doc.py           rewrite docs/schema.md and docs/schema-erd.svg
  .venv/bin/python scripts/tariffdb/schema_doc.py --check   exit 1 when either file is not what this script writes

Everything comes from data/tariffdb/schema.json (generated from scripts/tariffdb/spec.py) and the committed tables:
every table and column, row counts, a real example value per column, the diagrams and the walk-through, whose SQL runs
against the SQLite database loaded from the CSVs. tests/test_tariffdb.py fails when either file is stale.
"""
import argparse
import csv
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import load  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
DB_DIR = os.path.join(ROOT, "data", "tariffdb")
MD_PATH = os.path.join(ROOT, "docs", "schema.md")
SVG_PATH = os.path.join(ROOT, "docs", "schema-erd.svg")
REPO = "yehezkieled/standardise_network_tariff_tables"

# The walk-through tariff: an LV storage tariff with demand and export TOU windows, eligibility criteria and three AER
# and distributor document versions in its year. Its rows also supply the example values.
WALK = {"distributor_id": "essential", "tariff_code": "BLND4SB", "fin_year": "2025-26", "date": "2025-10-01"}

TYPES = [  # (spec type, SQLite storage, how values look)
    ("text", "TEXT", "UTF-8 text"),
    ("integer", "INTEGER", "whole number"),
    ("numeric", "NUMERIC", "decimal number"),
    ("date", "TEXT", "YYYY-MM-DD; effective_to is inclusive"),
    ("time", "TEXT", "HH:MM local clock time; start inclusive, end exclusive, 24:00 = end of day"),
    ("boolean", "INTEGER", "0 or 1"),
]


def md_cell(s):
    return str("" if s is None else s).replace("|", "\\|").replace("\n", " ")


def short(s, n=60):
    s = str(s)
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def md_table(header, rows):
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + [
        "| " + " | ".join(md_cell(x) for x in r) + " |" for r in rows]


class Data:
    def __init__(self):
        with open(os.path.join(DB_DIR, "schema.json"), encoding="utf-8") as f:
            self.tables = json.load(f)["tables"]
        self.rows = {}
        for t in self.tables:
            with open(os.path.join(DB_DIR, t["file"]), newline="", encoding="utf-8") as f:
                self.rows[t["name"]] = list(csv.DictReader(f))
        self.db = load.load()

    def edges(self):
        """(child table, [child columns], parent table, nullable) for every foreign key."""
        out = []
        for t in self.tables:
            for c in t["columns"]:
                if c["references"]:
                    out.append((t["name"], [c["name"]], c["references"].split(".")[0], c["nullable"]))
            for fk in t["foreign_keys"]:
                out.append((t["name"], fk["columns"], fk["references"], False))
        return out

    def query(self, sql):
        cur = self.db.execute(sql)
        return [d[0] for d in cur.description], cur.fetchall()

    def example_row(self, name):
        """The table's row closest to the walk-through tariff (first in file order on a tie)."""
        start = f"{WALK['fin_year'][:4]}-07-01"
        want = {"distributor_id": WALK["distributor_id"], "tariff_code": WALK["tariff_code"], "effective_from": start,
                "fin_year": WALK["fin_year"]}
        doc = next((r["document_id"] for r in self.rows["tariff"]
                    if all(r[k] == want[k] for k in ("distributor_id", "tariff_code", "effective_from"))), None)
        if name == "source_document":
            return next((r for r in self.rows[name] if r["document_id"] == doc), None)
        best, best_score = None, -1
        for r in self.rows[name]:
            score = sum(r.get(k) == v for k, v in want.items())
            if score > best_score:
                best, best_score = r, score
        return best

    def example(self, name, column, row):
        """A real value of the column: from the example row, else the first non-empty one; None when always NULL."""
        if row and row[column] != "":
            return row[column]
        return next((r[column] for r in self.rows[name] if r[column] != ""), None)


# ------------------------------------------------------------------------------------------------------ markdown
def glance(data):
    years = sorted({r["fin_year"] for r in data.rows["source_document"]})
    status = {s: sum(r["status"] == s for r in data.rows["tariff"]) for s in ("final", "provisional")}
    return md_table(["", ""], [
        ("Stores", "the network price charged per tariff code: daily, usage, demand, capacity, export and metering "
                   "rates, TOU windows, eligibility criteria"),
        ("Not stored", "retail plans; the DUoS / TUoS / jurisdictional breakdown"),
        ("Years", f"{years[0]} to {years[-1]}"),
        ("Distributors", f"{len(data.rows['distributor'])}"),
        ("Tariff-years", f"{len(data.rows['tariff']):,} ({status['final']:,} final, {status['provisional']:,} "
                         "provisional)"),
        ("Rates", f"{len(data.rows['rate']):,}"),
        ("Data (canonical)", "`data/tariffdb/tables/<table>.csv`, committed"),
        ("SQLite", "`.venv/bin/python scripts/tariffdb/load.py --out out/tariffdb.sqlite` (built, not committed: a "
                   "binary does not diff and would drift from the CSVs)"),
        ("Schema source", "`scripts/tariffdb/spec.py` → `schema.json`, `schema.sqlite.sql`, this page"),
        ("Update and validate", "[update-and-validate.md](update-and-validate.md)"),
    ])


def mermaid_erd(data):
    out = ["```mermaid", "erDiagram"]
    for child, cols, parent, nullable in data.edges():
        out.append(f"    {parent} {'|o' if nullable else '||'}--o{{ {child} : \"{', '.join(cols)}\"")
    for t in data.tables:
        fk_cols = {c for child, cols, _, _ in data.edges() if child == t["name"] for c in cols}
        out.append(f"    {t['name']} {{")
        for c in t["columns"]:
            keys = [k for k, on in (("PK", c["primary_key"]), ("FK", c["name"] in fk_cols)) if on]
            if keys:
                out.append(f"        {c['type']} {c['name']} {', '.join(keys)}")
        out.append("    }")
    return out + ["```"]


def flow():
    return ["```mermaid", "flowchart LR",
            "    A[\"AER report v1<br/>(first out)\"] -->|later version| B[\"AER report vN\"]",
            "    A --> P((provisional))",
            "    B --> P",
            "    D[\"Distributor's own<br/>price list\"] --> F((final))",
            "    P -.->|replaced per tariff code| F",
            "    D --> W[\"TOU windows<br/>eligibility\"]",
            "```"]


def table_summary(data):
    return md_table(["Table", "One row is", "Key", "Rows"], [
        (f"[`{t['name']}`](#{t['name']})", t["grain"], ", ".join(t["primary_key"]), f"{len(data.rows[t['name']]):,}")
        for t in data.tables])


def history():
    return [
        "## History", "",
        *md_table(["What", "How"], [
            ("A price over time", "one `tariff` + `rate` rows per period (`effective_from` .. `effective_to`, "
                                  "inclusive)"),
            ("A new year", "new rows; earlier years stay"),
            ("A mid-year change", "the old period ends the day before; a new period starts that day"),
            ("AER → distributor", "the distributor's list replaces the AER rows of each code it prices (`status` "
                                  "provisional → final)"),
            ("Replaced provisional rates", "in git history of `rate.csv`"),
            ("Every document version", "a `source_document` row, kept for good"),
        ]), "",
        *flow(), ""]


def walk(data):
    did, code, fy, date = WALK["distributor_id"], WALK["tariff_code"], WALK["fin_year"], WALK["date"]
    key = f"distributor_id = '{did}' AND tariff_code = '{code}'"
    on = f"'{date}' BETWEEN effective_from AND effective_to"
    steps = [
        ("Distributor", f"SELECT name, state, iana_timezone, observes_dst FROM distributor WHERE distributor_id = "
                        f"'{did}';"),
        ("Tariff, every year", f"SELECT effective_from, effective_to, tariff_name, status, document_id\n"
                               f"FROM tariff WHERE {key} ORDER BY effective_from;"),
        (f"Documents for {fy}", f"SELECT document_id, publisher, version_label, price_status, published_on\n"
                                f"FROM source_document WHERE fin_year = '{fy}'\n"
                                f"  AND (distributor_id = '{did}' OR distributor_id IS NULL) ORDER BY published_on;"),
        (f"Rates on {date}", f"SELECT charge_type, tou_period, value, unit, component, status, locator\n"
                             f"FROM rate WHERE {key} AND {on} ORDER BY charge_type, tou_period;"),
        (f"TOU windows on {date}", f"SELECT applies_to, tou_period, day_type, start_time, end_time, months\n"
                                   f"FROM tou_window WHERE {key} AND {on} ORDER BY applies_to, start_time;"),
        (f"Eligibility on {date}", f"SELECT criterion, operator, value_num, value_unit, value_text, quote\n"
                                   f"FROM eligibility WHERE {key} AND {on} ORDER BY criterion_id;"),
    ]
    out = ["## Walk-through", "",
           f"`{code}`, {data.db.execute('SELECT name FROM distributor WHERE distributor_id = ?', (did,)).fetchone()[0]}"
           f", on {date}. Real output of each query on `out/tariffdb.sqlite`.", ""]
    for i, (title, sql) in enumerate(steps, 1):
        cols, rows = data.query(sql)
        if not rows:
            raise SystemExit(f"walk-through step '{title}' returns no rows: choose another WALK tariff")
        out += [f"### {i}. {title}", "", "```sql", sql, "```", "",
                *md_table(cols, [[short("NULL" if v is None else v, 60) for v in r] for r in rows]), ""]
    return out


def reference(data):
    out = ["## Table reference", ""]
    referenced_by = {}
    for child, cols, parent, _ in data.edges():
        referenced_by.setdefault(parent, []).append(f"`{child}` ({', '.join(cols)})")
    for t in data.tables:
        name = t["name"]
        fks = [(cols, parent) for child, cols, parent, _ in data.edges() if child == name]
        meta = [("One row is", t["grain"]), ("Primary key", ", ".join(f"`{k}`" for k in t["primary_key"])),
                ("Rows", f"{len(data.rows[name]):,}"), ("Source", t["source"]),
                ("File", f"`data/tariffdb/{t['file']}`")]
        meta += [("References", "; ".join(f"({', '.join(c)}) → [`{p}`](#{p})" for c, p in fks) or "none"),
                 ("Referenced by", "; ".join(referenced_by.get(name, [])) or "none")]
        meta += [("Check", f"`{c}`") for c in t["checks"]]
        out += [f"### {name}", "", t["description"], "", *md_table(["", ""], meta), ""]
        row = data.example_row(name)
        fk_of = {c: p for cols, p in fks for c in cols}
        cols = []
        for c in t["columns"]:
            keys = ", ".join(k for k, on in (("PK", c["primary_key"]), (f"FK → {fk_of.get(c['name'])}",
                                                                           c["name"] in fk_of)) if on)
            allowed = ", ".join(c["enum"]) if c["enum"] else (
                "0, 1" if c["type"] == "boolean" else "YYYY-MM-DD" if c["type"] == "date" else
                "HH:MM" if c["type"] == "time" else "")
            if c["unit"]:
                allowed = (allowed + "; " if allowed else "") + f"unit: {c['unit']}"
            ex = data.example(name, c["name"], row)
            cols.append((f"`{c['name']}`", c["type"], "yes" if c["nullable"] else "no", keys, allowed,
                         c["description"], "*always NULL*" if ex is None else f"`{short(ex)}`"))
        out += md_table(["Column", "Type", "Null", "Key", "Allowed values / unit", "Meaning", "Example"], cols)
        out.append("")
    return out


def conventions():
    return ["## Conventions", "",
            *md_table(["Type", "SQLite", "Values"], TYPES), "",
            *md_table(["", ""], [
                ("Empty CSV field", "NULL"),
                ("Prices", "total network price, GST exclusive, in cents: c/day, c/kWh, c/kVAh, c/kW/month ... (`?` = "
                           "the source states no billing period); `value_published` / `unit_published` as printed"),
                ("Negative price", "a reward paid to the customer (export rebates)"),
                ("Keys", "built from content (distributor, code, period, component), never row order, so a rebuild "
                         "is byte-identical"),
                ("`locator`", "`xlsx:<sheet>!<cell>`, `pdf:p<page>` (`scripts/tariffdb/locators.py`)"),
            ]), ""]


def markdown(data):
    out = ["# Network tariff database: schema", "",
           "<!-- Generated by scripts/tariffdb/schema_doc.py from data/tariffdb/schema.json and the tables; do not "
           "edit by hand. -->", "",
           f"[{REPO}](https://github.com/{REPO})", "",
           "![Every table with its keys and the tables they reference](schema-erd.svg)", "",
           *glance(data), "",
           "## Tables", "", *table_summary(data), "",
           *history(),
           *walk(data),
           *reference(data),
           *conventions(),
           "## Diagram as Mermaid", "", *mermaid_erd(data), ""]
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------------------------------------- svg
# Light, self-contained palette: the SVG is shown as an image, so it carries its own background in either theme.
COLOURS = {"distributor": "#2563eb", "source_document": "#d97706", "tariff": "#059669"}


def erd_svg(data):
    """A single-column ER diagram that reads on a phone: one box per table (name, rows, key lines), and one coloured
    line per referenced table in the right-hand gutter, from each foreign key (dot) to its table (arrow)."""
    box_x, box_w, head_h, line_h, pad, gap = 16, 330, 28, 18, 7, 18
    y, fits = 16, int((box_w - 48) // 6.95)  # characters per key line (11.5 px monospace glyphs are ~6.95 px)
    boxes, key_y, parts = {}, {}, []
    for t in data.tables:
        fks = [(cols, parent) for child, cols, parent, _ in data.edges() if child == t["name"]]
        pk = [c["name"] for c in t["columns"] if c["primary_key"]]
        lines = []  # (tag, text, colour of, arrow to): a key wider than the box wraps between its columns
        for tag, cols, parent in [("PK", pk, None)] + [("FK", cols, parent) for cols, parent in fks]:
            rows, cur = [], ""
            for c in cols:
                nxt = f"{cur}, {c}" if cur else c
                if cur and len(nxt) + 1 > fits:
                    rows.append(cur + ",")
                    nxt = c
                cur = nxt
            arrow = f" → {parent}" if parent else ""
            if len(cur) + len(arrow) > fits:
                rows, cur, arrow = rows + [cur], "", arrow.strip()
            rows.append(cur + arrow)
            lines += [(tag if i == 0 else "", text, parent, parent if i == len(rows) - 1 else None)
                      for i, text in enumerate(rows)]
        height = head_h + pad + line_h * (len(lines) + 1) + pad - 4
        boxes[t["name"]] = y
        parts.append(f'<g><title>{html.escape(t["description"])}</title>'
                     f'<rect x="{box_x}" y="{y}" width="{box_w}" height="{height}" rx="7" class="box"/>'
                     f'<path d="M{box_x},{y + head_h} V{y + 7} q0,-7 7,-7 H{box_x + box_w - 7} q7,0 7,7 '
                     f'V{y + head_h} z" class="head"/>'
                     f'<text x="{box_x + 10}" y="{y + 19}" class="n">{html.escape(t["name"])}</text>'
                     f'<text x="{box_x + box_w - 10}" y="{y + 19}" class="r" text-anchor="end">'
                     f'{len(data.rows[t["name"]]):,} rows</text>')
        ly = y + head_h + pad + 12
        for tag, text, colour_of, arrow_to in lines:
            colour = f' style="fill:{COLOURS[colour_of]}"' if colour_of else ""
            head, _, tail = text.partition("→")
            parts.append(f'<text x="{box_x + 10}" y="{ly}" class="k"{colour}>{tag}</text>'
                         f'<text x="{box_x + 38}" y="{ly}" class="c">{html.escape(head)}'
                         + (f'<tspan class="t">→{html.escape(tail)}</tspan>' if arrow_to else "") + "</text>")
            if arrow_to:
                key_y.setdefault(arrow_to, []).append(ly - 4)
            ly += line_h
        n_other = len(t["columns"]) - len(set(pk) | {c for cols, _ in fks for c in cols})
        parts.append(f'<text x="{box_x + 38}" y="{ly}" class="m">+ {n_other} more columns</text></g>')
        y += height + gap
    height = y
    lane_w, right = 14, box_x + box_w
    order = sorted(key_y, key=lambda p: list(COLOURS).index(p))
    width = right + 18 + lane_w * len(order) + 6
    edges = []
    for lane, parent in enumerate(order):
        colour, x = COLOURS[parent], right + 18 + lane_w * lane
        py = boxes[parent] + head_h / 2
        ys = key_y[parent]
        e = [f'<g stroke="{colour}" fill="{colour}"><title>references {html.escape(parent)}</title>',
             f'<path d="M{x},{min(ys + [py])} V{max(ys + [py])}" class="bus"/>',
             f'<path d="M{x},{py} H{right + 8}" class="bus"/>',
             f'<path d="M{right + 8},{py - 4.5} L{right + 1},{py} L{right + 8},{py + 4.5} z" stroke="none"/>']
        for cy in ys:
            e += [f'<path d="M{right},{cy} H{x}" class="tick"/>', f'<circle cx="{x}" cy="{cy}" r="3" stroke="none"/>']
        edges.append("".join(e) + "</g>")
    style = ("<style>"
             "text{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;fill:#0f172a}"
             ".box{fill:#ffffff;stroke:#94a3b8}.head{fill:#e2e8f0}"
             ".n{font-size:14px;font-weight:700}.r{font-size:11px;fill:#475569}"
             ".k{font-size:10px;font-weight:700;fill:#64748b}.c{font-size:11.5px}.t{fill:#64748b}"
             ".m{font-size:10.5px;fill:#94a3b8;font-style:italic}"
             ".bus{fill:none;stroke-width:1.8}.tick{fill:none;stroke-width:1.1;stroke-opacity:.75}"
             "</style>")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="erd-title">'
            f'<title id="erd-title">Network tariff database: every table, its keys and the tables they reference'
            f'</title>{style}<rect width="{width}" height="{height}" fill="#f8fafc"/>{"".join(parts)}'
            f'{"".join(edges)}</svg>\n')


def outputs():
    data = Data()
    return {MD_PATH: markdown(data), SVG_PATH: erd_svg(data)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail when a file differs from what this script writes")
    args = ap.parse_args()
    stale = []
    for path, text in outputs().items():
        rel = os.path.relpath(path, ROOT)
        if args.check:
            current = open(path, encoding="utf-8").read() if os.path.exists(path) else None
            if current != text:
                stale.append(rel)
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {rel}")
    if stale:
        sys.exit(f"stale: {', '.join(stale)} (run .venv/bin/python scripts/tariffdb/schema_doc.py)")
    if args.check:
        print("docs/schema.md and docs/schema-erd.svg are current")


if __name__ == "__main__":
    main()
