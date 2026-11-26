"""Run the full pipeline: transform, rules R01 to R09, reconciliation, scoring.

Run from the project folder:

    .venv\\Scripts\\python src\\checks.py                   main dataset
    .venv\\Scripts\\python src\\checks.py --unseen round1   unseen-variant round 1
    .venv\\Scripts\\python src\\checks.py --unseen round2   unseen-variant round 2

The main run writes data/fatca_crs.duckdb and outputs/exception_register.csv.
An unseen run works in memory; it only stores its score in the main database.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import duckdb

from transform import ROOT, build, score_staging

RULES_SQL = Path(__file__).resolve().parent / "rules.sql"
RULE_IDS = [f"R0{i}" for i in range(1, 10)]


def run_rules(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(RULES_SQL.read_text(encoding="utf-8"))


def rows(con, sql, params=None):
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ------------------------------------------------------------------ scoring
def match_key(rule: str, source: str, record_key: str, customer_id: str,
              related: str, field: str):
    """Key used to match a flag to an answer-key issue for one rule."""
    if rule == "R01":
        return frozenset({record_key, related})
    if rule in ("R02", "R03"):
        return (customer_id, field)
    if rule == "R06":
        return record_key
    if rule == "R09":
        return (source, customer_id if source == "self_certification" else record_key, field)
    return customer_id


def score_rules(con: duckdb.DuckDBPyConnection, key_path: Path) -> list[dict]:
    with key_path.open(encoding="utf-8", newline="") as fh:
        issues = [i for i in csv.DictReader(fh) if i["expected_rule"] in RULE_IDS]
    flags = rows(con, "SELECT * FROM rule_flags")

    expected = defaultdict(set)
    for i in issues:
        rule = i["expected_rule"]
        expected[rule].add(match_key(rule, i["source_system"], i["record_key"],
                                     i["record_key"], i["related_key"], i["field"]))
    flagged = defaultdict(set)
    for f in flags:
        flagged[f["rule_id"]].add(match_key(f["rule_id"], f["source_system"], f["record_key"],
                                            f["customer_id"], f["related_key"], f["field"]))

    # Records touched by any flag, to report issues that a different rule raised.
    touched = {f["customer_id"] for f in flags} | {f["record_key"] for f in flags}

    results = []
    for rule in RULE_IDS:
        exp, got = expected[rule], flagged[rule]
        missed_issues = [i for i in issues if i["expected_rule"] == rule and
                         match_key(rule, i["source_system"], i["record_key"], i["record_key"],
                                   i["related_key"], i["field"]) not in got]
        results.append({
            "rule_id": rule,
            "injected": len(exp),
            "caught": len(exp & got),
            "missed": len(exp - got),
            "flags": len(got),
            "false_positives": len(got - exp),
            "missed_but_raised_elsewhere": sum(
                (i["record_key"] in touched) for i in missed_issues),
            "missed_detail": [(i["sub_type"] or i["issue_type"], i["record_key"],
                               i["injected_value"]) for i in missed_issues],
            "fp_detail": sorted(map(str, got - exp))[:5],
        })
    return results


def print_score(results: list[dict], title: str) -> None:
    print(f"\n{title}")
    print(f"  {'rule':<6}{'injected':>9}{'caught':>8}{'missed':>8}{'flags':>7}{'false pos':>11}")
    tot = Counter()
    for r in results:
        print(f"  {r['rule_id']:<6}{r['injected']:>9}{r['caught']:>8}{r['missed']:>8}"
              f"{r['flags']:>7}{r['false_positives']:>11}")
        for k in ("injected", "caught", "missed", "flags", "false_positives"):
            tot[k] += r[k]
    print(f"  {'total':<6}{tot['injected']:>9}{tot['caught']:>8}{tot['missed']:>8}"
          f"{tot['flags']:>7}{tot['false_positives']:>11}")
    elsewhere = sum(r["missed_but_raised_elsewhere"] for r in results)
    print(f"  missed by the expected rule but raised on the same record by another rule: "
          f"{elsewhere}; raised by no rule at all: {tot['missed'] - elsewhere}")
    for r in results:
        for d in r["missed_detail"]:
            print(f"    {r['rule_id']} missed: {d}")
        for d in r["fp_detail"]:
            print(f"    {r['rule_id']} false positive: {d}")
        if r["missed_but_raised_elsewhere"]:
            print(f"    {r['rule_id']}: {r['missed_but_raised_elsewhere']} of the misses were "
                  "still raised on the same record by another rule")


def store_score(con, results, label):
    con.execute("""CREATE TABLE IF NOT EXISTS rule_score (
        dataset VARCHAR, rule_id VARCHAR, injected INT, caught INT, missed INT,
        flags INT, false_positives INT)""")
    con.execute("DELETE FROM rule_score WHERE dataset = ?", [label])
    con.executemany("INSERT INTO rule_score VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(label, r["rule_id"], r["injected"], r["caught"], r["missed"],
                      r["flags"], r["false_positives"]) for r in results])


# ----------------------------------------------------------- reconciliation
def raw_balance_total(raw_dir: Path) -> Decimal:
    """Independent control total straight from the raw file (Python, not SQL)."""
    total = Decimal("0")
    with (raw_dir / "accounts.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            total += Decimal(row["balance_ye"].strip().replace(",", ""))
    return total


def print_reconciliation(con, raw_dir: Path) -> None:
    print("Reconciliation: record counts")
    print(f"  {'source':<20}{'source':>8}{'staging':>9}{'target':>8}{'held out':>10}{'diff':>6}  result")
    for r in rows(con, "SELECT * FROM recon_counts"):
        print(f"  {r['source_system']:<20}{r['source_rows']:>8}{r['staging_rows']:>9}"
              f"{r['target_rows']:>8}{r['held_out_rows']:>10}{r['difference']:>6}  {r['result']}")
    print("\nReconciliation: year-end balance control totals by currency")
    print(f"  {'currency':<10}{'accounts':>9}{'source':>16}{'target':>16}{'held out':>13}{'diff':>7}  result")
    for r in rows(con, "SELECT * FROM recon_control_totals"):
        print(f"  {r['currency']:<10}{r['source_accounts']:>9}{r['source_balance']:>16,.2f}"
              f"{r['target_balance']:>16,.2f}{r['held_out_balance']:>13,.2f}"
              f"{r['difference']:>7}  {r['result']}")
    sql_total = con.execute("SELECT sum(source_balance) FROM recon_control_totals").fetchone()[0]
    raw_total = raw_balance_total(raw_dir)
    print(f"  raw file total (independent Python sum): {raw_total:,.2f}  "
          f"vs staging {sql_total:,.2f}  "
          f"{'OK' if raw_total == sql_total else 'BREAK'}")


def print_register(con) -> None:
    print("\nException register by rule and severity")
    for r in rows(con, """SELECT rule_id, rule_name, severity, count(*) AS n
                          FROM exception_register GROUP BY ALL ORDER BY rule_id, severity"""):
        print(f"  {r['rule_id']}  {r['rule_name']:<64}{r['severity']:<8}{r['n']:>5}")
    print(f"  total exceptions: {con.execute('SELECT count(*) FROM exception_register').fetchone()[0]}")


def export_register(con, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = rows(con, "SELECT * FROM exception_register ORDER BY exception_id")
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--unseen", choices=["round1", "round2"],
                        help="score an unseen-variant dataset instead of the main one")
    args = parser.parse_args()

    if args.unseen:
        raw = ROOT / "data" / "unseen" / args.unseen / "raw"
        key = ROOT / "data" / "unseen" / args.unseen / "injected_issues.csv"
        con, label = build(None, raw), f"unseen_{args.unseen}"
    else:
        raw = ROOT / "data" / "raw"
        key = ROOT / "data" / "injected_issues.csv"
        con, label = build(ROOT / "data" / "fatca_crs.duckdb", raw), "main"

    run_rules(con)
    print(f"Dataset: {label}  ({raw.relative_to(ROOT)})\n")
    print_reconciliation(con, raw)
    print_register(con)
    score_staging(con, key)
    results = score_rules(con, key)
    print_score(results, f"Rule score against the {label} answer key")
    if args.unseen:
        main_db = ROOT / "data" / "fatca_crs.duckdb"
        if main_db.exists():           # keep both scores side by side for the workbook
            with duckdb.connect(str(main_db)) as main_con:
                store_score(main_con, results, label)
            print("\nUnseen score stored in data/fatca_crs.duckdb (table rule_score)")
    else:
        store_score(con, results, label)
        export_register(con, ROOT / "outputs" / "exception_register.csv")
        print("\nRegister written to outputs/exception_register.csv")
    con.close()


if __name__ == "__main__":
    main()
