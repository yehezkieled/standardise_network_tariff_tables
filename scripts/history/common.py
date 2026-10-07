"""Shared helpers of the historical parsers (scripts/history/<slug>.py, contract in scripts/history/CONTRACT.md): the
archived documents of a distributor, and the parser row (scripts/schema.py COLUMNS) built from what a page prints."""
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import schema  # noqa: E402
import units  # noqa: E402

ARCHIVE_INVENTORY = os.path.join(ROOT, "sources", "archive", "inventory.csv")
OUT_DIR = os.path.join(ROOT, "out", "history")
NAMES = {v: k for k, v in __import__("archive_sources").DISTRIBUTORS.items()}  # distributor_id -> canonical name
GST_START = "2000-07-01"  # GST began on 1 July 2000: earlier prices carry none


def archived(distributor_id, year=None):
    """Archive inventory rows of one distributor (and pricing year), in file order."""
    with open(ARCHIVE_INVENTORY, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["distributor_id"] == distributor_id
                and (year is None or r["pricing_year"] == year)]


def document(local_path):
    """The archive inventory row of one file."""
    with open(ARCHIVE_INVENTORY, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["local_path"] == local_path:
                return r
    raise SystemExit(f"{local_path}: not in sources/archive/inventory.csv")


def row(doc, code, component, value, unit, locator, *, name="", customer_class="", basis="NUoS", gst="excl",
        note="", charge_type=None, time_band=None, season=None):
    """One parser row for a price printed in an archived document.

    doc: the archive inventory row (document()); value: the number exactly as printed (text, no '$' or ','); unit: as
    printed or as the column/table heading states it; locator: locators.pdf(page) / locators.xlsx(sheet, cell).
    charge_type / time_band / season default to the scripts/schema.py helpers on the component label."""
    value = str(value).strip()
    std, unit_std = units.to_std(value, unit, component)
    if std is None:
        raise ValueError(f"{doc['local_path']} {locator}: {value!r} is not a number")
    return {
        "side": doc["side"], "distributor": NAMES[doc["distributor_id"]], "fin_year": doc["pricing_year"],
        "tariff_code": code.strip(), "tariff_name": name.strip(), "customer_class": customer_class.strip(),
        "component": component.strip(), "charge_type": charge_type or schema.charge_type_from_label(component, unit),
        "time_band": schema.time_band_from_label(component) if time_band is None else time_band,
        "season": schema.season_from_label(component) if season is None else season,
        "unit": unit, "value": value, "value_std": repr(std), "unit_std": unit_std, "gst": gst, "basis": basis,
        "source_file": doc["local_path"], "source_url": doc["source_url"], "note": note, "locator": locator,
    }


def write(slug, rows):
    """Write out/history/<slug>.csv (rows in the order given)."""
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{slug}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=schema.COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{path}: {len(rows)} rows")
