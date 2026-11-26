"""Load the raw extracts into DuckDB and run src/transform.sql.

Run from the project folder:

    .venv\\Scripts\\python src\\transform.py

Creates data/fatca_crs.duckdb (rebuilt from scratch on every run), prints
record counts, and scores the staging step against the answer key:
  * every STAGING_FIX value should come out of staging as its clean value
  * every R09 value should be raised in exc_transform
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from reference import (
    ACCOUNT_TYPES,
    COUNTRIES,
    COUNTRY_ALIASES,
    CURRENCIES,
    CURRENCY_ALIASES,
    REPORTING_DATE,
    TIN_PLACEHOLDERS,
    tin_pattern,
)

ROOT = Path(__file__).resolve().parent.parent
SQL_FILE = Path(__file__).resolve().parent / "transform.sql"

SOURCES = {
    "src_customer_master": "customer_master.csv",
    "src_accounts": "accounts.csv",
    "src_self_certifications": "self_certifications.csv",
}

# answer key source_system -> (staging table, column holding the answer key's record_key)
STAGING = {
    "customer_master": ("stg_customer", "customer_id"),
    "accounts": ("stg_account", "account_id"),
    "self_certification": ("stg_selfcert", "customer_id"),
}


def country_aliases() -> list[tuple[str, str]]:
    rows = {}
    for iso2, info in COUNTRIES.items():
        for alias in (iso2, info[1], info[0].upper()):
            rows[alias] = iso2
    rows.update(COUNTRY_ALIASES)
    return sorted(rows.items())


def currency_aliases() -> list[tuple[str, str]]:
    rows = {code: code for code in CURRENCIES}
    rows.update(CURRENCY_ALIASES)
    return sorted(rows.items())


def load_reference(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE OR REPLACE TABLE ref_country_alias (alias VARCHAR PRIMARY KEY, iso2 VARCHAR)")
    con.executemany("INSERT INTO ref_country_alias VALUES (?, ?)", country_aliases())
    con.execute("CREATE OR REPLACE TABLE ref_currency_alias (alias VARCHAR PRIMARY KEY, code VARCHAR)")
    con.executemany("INSERT INTO ref_currency_alias VALUES (?, ?)", currency_aliases())
    con.execute("CREATE OR REPLACE TABLE ref_tin_placeholder (value VARCHAR PRIMARY KEY)")
    con.executemany("INSERT INTO ref_tin_placeholder VALUES (?)", [(v,) for v in TIN_PLACEHOLDERS])
    con.execute("CREATE OR REPLACE TABLE ref_account_type (alias VARCHAR PRIMARY KEY, value VARCHAR)")
    con.executemany("INSERT INTO ref_account_type VALUES (?, ?)",
                    [(t.upper(), t) for t in ACCOUNT_TYPES])
    # RE2 full-match patterns (no ^ or $), used by rule R03.
    con.execute("CREATE OR REPLACE TABLE ref_tin_format (iso2 VARCHAR PRIMARY KEY, pattern VARCHAR)")
    con.executemany("INSERT INTO ref_tin_format VALUES (?, ?)",
                    [(c, tin_pattern(c).strip("^$")) for c in COUNTRIES])
    con.execute("CREATE OR REPLACE TABLE ref_parameter (name VARCHAR PRIMARY KEY, value DATE)")
    con.execute("INSERT INTO ref_parameter VALUES ('reporting_date', ?)", [REPORTING_DATE])


def load_sources(con: duckdb.DuckDBPyConnection, raw_dir: Path) -> None:
    for table, file in SOURCES.items():
        path = (raw_dir / file).as_posix()
        con.execute(f"""
            CREATE OR REPLACE TABLE {table} AS
            SELECT row_number() OVER () AS src_row, *
            FROM read_csv('{path}', all_varchar = true, header = true)
        """)


def run_transform(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SQL_FILE.read_text(encoding="utf-8"))


def build(db_path: Path | None, raw_dir: Path) -> duckdb.DuckDBPyConnection:
    if db_path is not None and db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path) if db_path else ":memory:")
    load_reference(con)
    load_sources(con, raw_dir)
    run_transform(con)
    return con


def as_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    return str(value)


def score_staging(con: duckdb.DuckDBPyConnection, key_path: Path) -> None:
    key = con.execute(
        "SELECT * FROM read_csv(?, all_varchar = true, header = true)",
        [key_path.as_posix()]).fetchall()
    cols = [d[0] for d in con.description]
    issues = [dict(zip(cols, row)) for row in key]

    exc = con.execute("""
        SELECT source_system,
               CASE source_system WHEN 'self_certification' THEN customer_id
                    ELSE record_key END AS key, field
        FROM exc_transform WHERE record_in_target_scope
    """).fetchall()
    raised = set(exc)

    fixed = Counter()
    problems = []
    for issue in issues:
        if issue["expected_rule"] != "STAGING_FIX":
            continue
        table, key_col = STAGING[issue["source_system"]]
        staged = con.execute(
            f'SELECT "{issue["field"]}" FROM {table} WHERE {key_col} = ?',
            [issue["record_key"]]).fetchone()
        value = as_text(staged[0]) if staged else None
        ok = value == issue["clean_value"]
        flagged = (issue["source_system"], issue["record_key"], issue["field"]) in raised
        fixed["correct" if ok and not flagged else "wrong"] += 1
        if not ok or flagged:
            problems.append((issue["issue_id"], issue["field"],
                             issue["injected_value"], issue["clean_value"], value))

    r09 = [i for i in issues if i["expected_rule"] == "R09"]
    r09_keys = {(i["source_system"], i["record_key"], i["field"]) for i in r09}
    caught = sum(k in raised for k in r09_keys)
    unexpected = sorted(raised - r09_keys)

    print("\nStaging score against the answer key")
    print(f"  STAGING_FIX values standardised to the clean value: "
          f"{fixed['correct']} of {fixed['correct'] + fixed['wrong']}")
    for p in problems[:10]:
        print(f"    not fixed: {p}")
    print(f"  R09 unfixable values raised in exc_transform:     {caught} of {len(r09_keys)}")
    print(f"  Field exceptions not in the answer key:           {len(unexpected)}")
    for u in unexpected[:10]:
        print(f"    unexpected: {u}")


def print_counts(con: duckdb.DuckDBPyConnection) -> None:
    q = lambda sql: con.execute(sql).fetchone()[0]
    print("Record counts")
    print(f"  {'':<20}{'source':>8}{'staging':>9}{'target':>8}{'held out':>10}")
    rows = [
        ("accounts", "src_accounts", "stg_account",
         "SELECT count(*) FROM tgt_reportable_account", "accounts"),
        ("customers", "src_customer_master", "stg_customer",
         "SELECT count(DISTINCT customer_id) FROM tgt_reportable_account", "customer_master"),
        ("self-certifications", "src_self_certifications", "stg_selfcert",
         "SELECT count(DISTINCT selfcert_id) FROM tgt_reportable_account", "self_certification"),
    ]
    for label, src, stg, tgt_sql, source in rows:
        held = q(f"""SELECT count(DISTINCT src_row) FROM exc_transform
                     WHERE source_system = '{source}' AND NOT record_in_target_scope""")
        print(f"  {label:<20}{q(f'SELECT count(*) FROM {src}'):>8}"
              f"{q(f'SELECT count(*) FROM {stg}'):>9}{q(tgt_sql):>8}{held:>10}")
    print("\nTransformation exceptions (exc_transform)")
    for source, etype, field, n in con.execute("""
            SELECT source_system, exception_type, field, count(*)
            FROM exc_transform GROUP BY ALL ORDER BY ALL""").fetchall():
        print(f"  {source:<20}{etype:<22}{field:<22}{n:>5}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, default=ROOT / "data" / "raw")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "fatca_crs.duckdb")
    parser.add_argument("--answer-key", type=Path, default=ROOT / "data" / "injected_issues.csv")
    args = parser.parse_args()
    con = build(args.db, args.raw)
    print_counts(con)
    if args.answer_key.exists():
        score_staging(con, args.answer_key)
    con.close()
    print(f"\nDatabase written to {args.db}")


if __name__ == "__main__":
    main()
