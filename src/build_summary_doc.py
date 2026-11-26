"""Write docs/EXCEPTION_SUMMARY.md, a one-page summary for a non-technical reader.

Every number is read from data/fatca_crs.duckdb, so the summary always matches
the outputs. Run after checks.py:

    .venv\\Scripts\\python src\\build_summary_doc.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from reference import REPORTING_DATE

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "EXCEPTION_SUMMARY.md"

PLAIN = {
    "R01": "Possible duplicate customers (same person under two IDs)",
    "R02": "Missing tax identification number (TIN)",
    "R03": "TIN in the wrong format",
    "R04": "Country of residence differs from the declared tax residency",
    "R05": "US indicators with no US person declaration",
    "R06": "Accounts not linked to any customer",
    "R07": "Customers with no accounts",
    "R08": "Missing or unusable self-certification",
    "R09": "Values that could not be standardised",
}


def n(x) -> str:
    return f"{x:,}"


def money(x) -> str:
    return f"{x:,.2f}"


def build(con) -> str:
    one = lambda sql: con.execute(sql).fetchone()
    counts = {r[0]: r for r in con.execute("SELECT * FROM recon_counts").fetchall()}
    acc, cus, sc = counts["accounts"], counts["customer_master"], counts["self_certification"]
    recon_ok = all(r[-1] == "OK" for r in counts.values())
    totals = con.execute("SELECT currency, result FROM recon_control_totals").fetchall()
    totals_ok = all(r[1] == "OK" for r in totals)
    currencies = [r[0] for r in totals if r[0] != "UNMAPPED"]
    unmapped = one("""SELECT source_accounts, source_balance FROM recon_control_totals
                      WHERE currency = 'UNMAPPED'""") or (0, 0)
    orphans = one("""SELECT count(*), sum(balance_ye), string_agg(DISTINCT currency, ', ')
                     FROM stg_account a WHERE a.src_row IN (
                       SELECT src_row FROM exc_transform WHERE exception_type = 'ORPHAN_ACCOUNT')""")
    total_exc = one("SELECT count(*) FROM exception_register")[0]
    by_sev = dict(con.execute("SELECT severity, count(*) FROM exception_register GROUP BY 1").fetchall())
    cust_with = one("""SELECT count(DISTINCT e.customer_id) FROM exception_register e
                       JOIN stg_customer c USING (customer_id)""")[0]
    rules = con.execute("""
        SELECT k.rule_id, k.severity, count(e.exception_id),
               count(*) FILTER (WHERE e.severity = 'High'),
               count(*) FILTER (WHERE e.severity = 'Medium'),
               count(*) FILTER (WHERE e.severity = 'Low')
        FROM rule_catalogue k LEFT JOIN exception_register e USING (rule_id)
        GROUP BY ALL ORDER BY 1""").fetchall()
    r = {x[0]: x for x in rules}
    us_accounts = one("""SELECT count(*) FROM tgt_reportable_account WHERE customer_id IN
                         (SELECT customer_id FROM exception_register WHERE rule_id = 'R05')""")[0]
    sc_missing = one("""SELECT count(*) FROM exception_register
                        WHERE rule_id = 'R08' AND severity = 'High'""")[0]
    sc_dates = r["R08"][2] - sc_missing
    complete = one("""
        SELECT count(*), count(*) FILTER (WHERE has_selfcert AND customer_id NOT IN (
                   SELECT customer_id FROM exception_register WHERE customer_id IS NOT NULL
                   AND (rule_id IN ('R02', 'R03', 'R08')
                        OR (rule_id = 'R09' AND source_system = 'self_certification'))))
        FROM tgt_reportable_account""")
    pct = complete[1] / complete[0]
    score = {row[0]: row[1:] for row in con.execute("""
        SELECT dataset, sum(injected), sum(caught), sum(false_positives)
        FROM rule_score GROUP BY 1""").fetchall()}
    r2 = score.get("unseen_round2", (0, 0, 0))

    rule_rows = "\n".join(
        f"| {rid} | {PLAIN[rid]} | {n(x[2])} | "
        + ", ".join(f"{s} {n(v)}" for s, v in zip(("High", "Medium", "Low"), x[3:]) if v)
        + " |" for rid, x in r.items())

    return f"""# FATCA and CRS data review: exception summary

**Fictional financial institution. Year ended {REPORTING_DATE:%d %B %Y}.**
Synthetic data in a simplified educational model. This is not tax advice.

## Scope

We reviewed the three data extracts used to prepare FATCA and CRS customer
reporting, to find records that are missing, duplicated or inconsistent
before reporting. We did not decide which accounts are legally reportable.

| Source system | Records received |
|---|---|
| Core banking customer master | {n(cus[1])} customers |
| Account system | {n(acc[1])} accounts, balances in {len(currencies)} currencies |
| Onboarding self-certifications | {n(sc[1])} self-certifications |

## What we checked

Nine checks, each based on public OECD and FATCA agreement guidance or on
basic data quality, cover duplicates, tax identification numbers, residence,
US indicators, account links, self-certifications and data formats. Full
definitions are in docs/RULES.md.

## Results by check

| Check | What it finds | Exceptions | By severity |
|---|---|---|---|
{rule_rows}
| | **Total** | **{n(total_exc)}** | High {n(by_sev.get('High', 0))}, Medium {n(by_sev.get('Medium', 0))}, Low {n(by_sev.get('Low', 0))} |

{n(cust_with)} of {n(cus[1])} customers ({cust_with / cus[1]:.1%}) have at least one exception.

## Reconciliation

{'Every record is accounted for.' if recon_ok else 'Record counts DO NOT reconcile.'}
Of {n(acc[1])} accounts, {n(acc[3])} are in the reporting dataset and {n(acc[4])} are held
out; of {n(cus[1])} customers, {n(cus[3])} have accounts and {n(cus[4])} do not; of
{n(sc[1])} self-certifications, {n(sc[3])} relate to reported accounts and {n(sc[4])} do not.
Year-end balances {'agree' if totals_ok else 'DO NOT agree'} between the source and the
reporting dataset in every currency, after allowing for the held-out accounts.
{n(unmapped[0])} accounts have a currency that could not be read, so their balances
(totalling {money(unmapped[1])}) cannot be stated in a currency until corrected.

## Top 3 issues

1. **{n(r['R05'][2])} customers show US indicators but have not declared US
   status** ({n(us_accounts)} accounts). Unless curing documents are held,
   these accounts may need to be treated as US reportable.
2. **Self-certifications are incomplete.** {n(sc_missing)} customers have no
   self-certification, {n(sc_dates)} have one with an unusable date,
   {n(r['R02'][2])} declared residencies have no TIN and {n(r['R03'][2])} TINs are
   in the wrong format. Only {pct:.1%} of reported accounts ({n(complete[1])} of
   {n(complete[0])}) have a complete self-certification.
3. **{n(orphans[0])} accounts ({orphans[2]} {money(orphans[1])}) are not linked to
   any customer**, so they cannot be assessed or reported until the link is
   corrected.

## Recommended next steps

1. Request curing documents or US status declarations for the {n(r['R05'][2])} US
   indicator cases, starting with the highest balances.
2. Contact customers for missing self-certifications and TINs; correct keying
   errors against the signed forms.
3. Correct the {n(orphans[0])} account links in the account system and confirm
   the {n(r['R07'][2])} customers with no accounts are expected.
4. Review the {n(r['R04'][2])} residence mismatches and the {n(r['R01'][2])} possible
   duplicates, then re-run the checks.
5. Assign an owner and status to each exception in the workbook register.

## Limitations

- Simplified model on synthetic data, not a complete implementation of FATCA
  or CRS. No balance thresholds and no documentary evidence are considered.
- On data it had not seen before, the checks caught {r2[1]} of {r2[0]} planted
  issues and raised {r2[2]} false alarms. Some issues will be missed.
- A balance written in European style (12.345,67) is misread without warning.
  Balances should be checked for this before reporting.
- Duplicate matching uses name and date of birth only, so twins are flagged
  and need a manual check.
"""


def main() -> None:
    con = duckdb.connect(str(ROOT / "data" / "fatca_crs.duckdb"), read_only=True)
    OUT.write_text(build(con), encoding="utf-8")
    con.close()
    print(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
