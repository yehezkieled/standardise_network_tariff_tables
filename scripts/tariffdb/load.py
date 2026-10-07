"""Load the tables (data/tariffdb/tables/*.csv) into SQLite with every key, foreign key and CHECK constraint.

  .venv/bin/python scripts/tariffdb/load.py                             load in memory and report row counts
  .venv/bin/python scripts/tariffdb/load.py --out out/tariffdb.sqlite   also save the database to a file

The CSVs are the committed copy; the .sqlite file is built from them and never committed (a binary does not diff,
and a committed copy could drift from the CSVs). No database server is needed.
"""
import csv
import json
import sqlite3
from pathlib import Path

DEFAULT_DATA = Path(__file__).resolve().parents[2] / 'data' / 'tariffdb'


def load(directory=DEFAULT_DATA):
    directory = Path(directory)
    schema = json.loads((directory / 'schema.json').read_text())
    con = sqlite3.connect(':memory:')
    con.execute('PRAGMA foreign_keys = ON')
    try:
        con.executescript((directory / 'schema.sqlite.sql').read_text())
        for table in schema['tables']:
            columns = [c['name'] for c in table['columns']]
            with (directory / table['file']).open(newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                if reader.fieldnames != columns:
                    raise ValueError(f"{table['name']}: CSV header differs from schema")
                for number, row in enumerate(reader, 2):
                    try:
                        con.execute(f"INSERT INTO {table['name']} ({', '.join(columns)}) "
                                    f"VALUES ({', '.join('?' for _ in columns)})",
                                    [None if row[c] == '' else row[c] for c in columns])
                    except sqlite3.IntegrityError as exc:
                        raise ValueError(f"{table['file']}:{number}: {exc}") from exc
        con.commit()
        if con.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Foreign key check failed')
        return con
    except Exception:
        con.close()
        raise


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', help='also save the loaded database to this SQLite file (e.g. out/tariffdb.sqlite)')
    args = parser.parse_args()
    with load() as db:
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).unlink(missing_ok=True)
            with sqlite3.connect(args.out) as target:
                db.backup(target)
            print(f'saved {args.out}')
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables:
            print(f"{table}: {db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]}")
        print('SQLite import passed; foreign keys and constraints enabled'
              + ('' if args.out else '; no database file written') + '.')
