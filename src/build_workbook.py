"""Build outputs/exception_workpaper.xlsx from the DuckDB results.

Run after checks.py (main, round1, round2) and build_rules_doc.py:

    .venv\\Scripts\\python src\\build_workbook.py

The workbook uses real Excel formulas (COUNTIFS, SUMIFS, XLOOKUP, INDEX-MATCH)
over data sheets, so the reconciliation and summaries recalculate when the
data or the exception statuses change. Expected values for key cells are
written to data/workbook_expected.json for src/verify_workbook.py.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import duckdb
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.table import Table, TableStyleInfo

from reference import REPORTING_DATE
from score_history import UNSEEN_ROUND1_BEFORE_FIXES

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "fatca_crs.duckdb"
OUT = ROOT / "outputs" / "exception_workpaper.xlsx"
EXPECTED = ROOT / "data" / "workbook_expected.json"

STATUSES = ["Open", "Investigating", "Resolved"]
OWNERS = ["Tax analyst", "Client onboarding", "Data engineering", "Operations",
          "Relationship manager"]
SEVERITIES = ["High", "Medium", "Low"]
LOOKUP_ROWS = 15

NAVY = "1F3864"
HEAD_FILL = PatternFill("solid", fgColor=NAVY)
HEAD_FONT = Font(bold=True, color="FFFFFF")
SUB_FILL = PatternFill("solid", fgColor="D9E1F2")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
RED = PatternFill("solid", fgColor="F8CBAD")
AMBER = PatternFill("solid", fgColor="FFE699")
GREEN = PatternFill("solid", fgColor="C6EFCE")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MONEY = "#,##0.00"


def q(con, sql, params=None):
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def header(ws, row, values, col=1):
    for i, v in enumerate(values):
        c = ws.cell(row=row, column=col + i, value=v)
        c.fill, c.font, c.border = HEAD_FILL, HEAD_FONT, BOX
        c.alignment = Alignment(wrap_text=True, vertical="center")


PRINT_SHEETS = ["Summary", "Exception register", "Lookup", "Rule x country", "Check scores"]


def print_setup(ws, repeat_header: bool = False) -> None:
    """Landscape A4, one page wide (any height), footer with sheet and page."""
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_margins = PageMargins(left=0.5, right=0.5, top=0.6, bottom=0.7,
                                  header=0.3, footer=0.3)
    ws.oddFooter.left.text = "&A"
    ws.oddFooter.right.text = "Page &P of &N"
    if repeat_header:
        ws.print_title_rows = "1:1"


def title(ws, text, sub=None):
    ws["A1"] = text
    ws["A1"].font = Font(bold=True, size=14, color=NAVY)
    if sub:
        ws["A2"] = sub
        ws["A2"].font = Font(italic=True, color="595959")


def widths(ws, values):
    for i, w in enumerate(values, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def write_table(ws, name, columns, rows, style="TableStyleMedium2"):
    header(ws, 1, columns)
    for r, row in enumerate(rows, start=2):
        for c, v in enumerate(row, start=1):
            ws.cell(row=r, column=c, value=v)
    ref = f"A1:{get_column_letter(len(columns))}{len(rows) + 1}"
    table = Table(displayName=name, ref=ref)
    table.tableStyleInfo = TableStyleInfo(name=style, showRowStripes=True)
    ws.add_table(table)
    ws.freeze_panes = "A2"
    return len(rows) + 1                       # last row


# ---------------------------------------------------------------- data sheets
def sheet_accounts(wb, con):
    ws = wb.create_sheet("Accounts")
    rows = q(con, """
        SELECT a.account_id, a.customer_id, a.account_type, a.open_date, a.balance_ye,
               coalesce(a.currency, 'UNMAPPED') AS currency, a.status,
               CASE WHEN t.account_id IS NOT NULL THEN 'Y' ELSE 'N' END AS in_target,
               e.exception_type AS held_out_reason
        FROM stg_account a
        LEFT JOIN tgt_reportable_account t ON t.account_id = a.account_id
        LEFT JOIN (SELECT src_row, any_value(exception_type) AS exception_type
                   FROM exc_transform
                   WHERE source_system = 'accounts' AND NOT record_in_target_scope
                   GROUP BY src_row) e ON e.src_row = a.src_row
        ORDER BY a.account_id""")
    n = len(rows) + 1
    cols = ["account_id", "customer_id", "account_type", "open_date", "balance_ye", "currency",
            "status", "in_target", "held_out_reason", "customer_name", "country_of_residence",
            "has_selfcert", "selfcert_exceptions", "selfcert_complete"]
    data = []
    for i, r in enumerate(rows, start=2):
        data.append([
            r["account_id"], r["customer_id"], r["account_type"], r["open_date"],
            float(r["balance_ye"]) if r["balance_ye"] is not None else None,
            r["currency"], r["status"], r["in_target"], r["held_out_reason"],
            f'=IFERROR(INDEX(Customers!$B:$B,MATCH(B{i},Customers!$A:$A,0)),"Not in customer master")',
            f'=IFERROR(INDEX(Customers!$D:$D,MATCH(B{i},Customers!$A:$A,0)),"")',
            f'=IF(COUNTIF(\'Self-certs\'!$B:$B,B{i})>0,"Y","N")',
            f"=SUM(COUNTIFS('Exception register'!$H:$H,B{i},'Exception register'!$B:$B,"
            f'{{"R02","R03","R08"}}))'
            f"+COUNTIFS('Exception register'!$H:$H,B{i},'Exception register'!$B:$B,\"R09\","
            f"'Exception register'!$F:$F,\"self_certification\")",
            f'=IF(AND(L{i}="Y",M{i}=0),"Y","N")',
        ])
    write_table(ws, "tblAccounts", cols, data)
    for i in range(2, n + 1):
        ws.cell(row=i, column=4).number_format = "yyyy-mm-dd"
        ws.cell(row=i, column=5).number_format = MONEY
    widths(ws, [12, 12, 14, 12, 15, 11, 9, 10, 22, 26, 12, 12, 12, 13])
    return n


def sheet_customers(wb, con):
    ws = wb.create_sheet("Customers")
    rows = q(con, """
        SELECT c.customer_id, c.full_name, c.date_of_birth, c.country_of_residence,
               e.exception_type AS held_out_reason
        FROM stg_customer c
        LEFT JOIN (SELECT src_row, any_value(exception_type) AS exception_type FROM exc_transform
                   WHERE source_system = 'customer_master' AND NOT record_in_target_scope
                   GROUP BY src_row) e ON e.src_row = c.src_row
        ORDER BY c.customer_id""")
    data = [[r["customer_id"], r["full_name"], r["date_of_birth"], r["country_of_residence"],
             f'=IF(COUNTIFS(Accounts!$B:$B,A{i},Accounts!$H:$H,"Y")>0,"Y","N")',
             r["held_out_reason"]] for i, r in enumerate(rows, start=2)]
    n = write_table(ws, "tblCustomers", ["customer_id", "full_name", "date_of_birth",
                                      "country_of_residence", "in_target", "held_out_reason"], data)
    for i in range(2, n + 1):
        ws.cell(row=i, column=3).number_format = "yyyy-mm-dd"
    widths(ws, [12, 28, 13, 12, 10, 22])
    return n


def sheet_selfcerts(wb, con):
    ws = wb.create_sheet("Self-certs")
    rows = q(con, """
        SELECT s.selfcert_id, s.customer_id, s.tax_residency_1, s.tin_1, s.tax_residency_2,
               s.tin_2, s.tax_residency_3, s.tin_3, s.us_person, s.date_signed,
               e.exception_type AS held_out_reason
        FROM stg_selfcert s
        LEFT JOIN (SELECT src_row, any_value(exception_type) AS exception_type FROM exc_transform
                   WHERE source_system = 'self_certification' AND NOT record_in_target_scope
                   GROUP BY src_row) e ON e.src_row = s.src_row
        ORDER BY s.selfcert_id""")
    data = [[r["selfcert_id"], r["customer_id"], r["tax_residency_1"], r["tin_1"],
             r["tax_residency_2"], r["tin_2"], r["tax_residency_3"], r["tin_3"], r["us_person"],
             r["date_signed"],
             f'=IF(COUNTIFS(Accounts!$B:$B,B{i},Accounts!$H:$H,"Y")>0,"Y","N")',
             r["held_out_reason"]] for i, r in enumerate(rows, start=2)]
    n = write_table(ws, "tblSelfCerts", ["selfcert_id", "customer_id", "tax_residency_1", "tin_1",
                                      "tax_residency_2", "tin_2", "tax_residency_3", "tin_3",
                                      "us_person", "date_signed", "in_target",
                                      "held_out_reason"], data)
    for i in range(2, n + 1):
        ws.cell(row=i, column=10).number_format = "yyyy-mm-dd"
    widths(ws, [12, 12, 10, 22, 10, 22, 10, 22, 9, 12, 10, 22])
    return n


# ---------------------------------------------------------- exception register
def sheet_register(wb, con):
    ws = wb.create_sheet("Exception register", 1)
    rows = q(con, "SELECT * FROM exception_register ORDER BY exception_id")
    cols = ["exception_id", "rule_id", "rule_name", "severity", "severity_reason",
            "source_system", "record_key", "customer_id", "related_key", "field", "country",
            "description", "recommended_action", "status", "owner", "notes", "lookup_match"]
    data = []
    for i, r in enumerate(rows, start=2):
        data.append([r["exception_id"], r["rule_id"], r["rule_name"], r["severity"],
                     r["severity_reason"], r["source_system"], r["record_key"], r["customer_id"],
                     r["related_key"], r["field"], r["country"], r["description"],
                     r["recommended_action"], "Open", None, None,
                     # Helper for the Lookup sheet: numbers the rows that match the
                     # looked-up account or its customer, 1, 2, 3 ...
                     f'=IF(OR(G{i}=Lookup!$C$4,H{i}=Lookup!$D$5),MAX(Q$1:Q{i - 1})+1,"")'])
    n = write_table(ws, "tblExceptionRegister", cols, data)

    status_dv = DataValidation(type="list", formula1="=Lists!$A$2:$A$4", allow_blank=False,
                               showErrorMessage=True, errorTitle="Status",
                               error="Choose Open, Investigating or Resolved")
    owner_dv = DataValidation(type="list", formula1=f"=Lists!$B$2:$B${len(OWNERS) + 1}",
                              allow_blank=True, showErrorMessage=True, errorTitle="Owner",
                              error="Choose an owner from the list")
    ws.add_data_validation(status_dv)
    ws.add_data_validation(owner_dv)
    status_dv.add(f"N2:N{n}")
    owner_dv.add(f"O2:O{n}")

    rng = f"A2:Q{n}"
    ws.conditional_formatting.add(rng, FormulaRule(formula=['$D2="High"'], fill=RED))
    ws.conditional_formatting.add(rng, FormulaRule(formula=['$D2="Medium"'], fill=AMBER))
    ws.conditional_formatting.add(rng, FormulaRule(formula=['$D2="Low"'], fill=GREEN))
    widths(ws, [11, 7, 34, 9, 40, 18, 12, 12, 12, 18, 9, 60, 60, 13, 20, 24, 9])
    for row in ws.iter_rows(min_row=2, max_row=n):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=c.column in (5, 12, 13))
    return n


# ------------------------------------------------------------------- summary
def sheet_summary(wb, con, n_acc, n_cust, n_sc, n_reg, expected):
    ws = wb.active
    ws.title = "Summary"
    title(ws, "FATCA and CRS data checks: exception workpaper",
          f"Synthetic data for a fictional institution. Reporting date {REPORTING_DATE}. "
          "Simplified educational model, not tax advice.")
    reg = "'Exception register'"

    # ---- record counts
    r = 4
    ws.cell(row=r, column=1, value="1. Record count reconciliation").font = Font(bold=True, size=12)
    header(ws, r + 1, ["Source", "Control count (from extract)", "Rows in workbook",
                       "In target", "Held out", "Difference", "Result"])
    src = {x["source_system"]: x for x in q(con, "SELECT * FROM recon_counts")}
    specs = [("Accounts", "accounts", "Accounts", n_acc, "H"),
             ("Customers", "customer_master", "Customers", n_cust, "E"),
             ("Self-certifications", "self_certification", "'Self-certs'", n_sc, "K")]
    for k, (label, key, sheet, n, flag) in enumerate(specs):
        row = r + 2 + k
        ws.cell(row=row, column=1, value=label)
        ws.cell(row=row, column=2, value=src[key]["source_rows"]).fill = INPUT_FILL
        ws.cell(row=row, column=3, value=f"=COUNTA({sheet}!$A$2:$A${n})")
        ws.cell(row=row, column=4, value=f'=COUNTIF({sheet}!${flag}$2:${flag}${n},"Y")')
        ws.cell(row=row, column=5, value=f'=COUNTIF({sheet}!${flag}$2:${flag}${n},"N")')
        ws.cell(row=row, column=6, value=f"=C{row}-D{row}-E{row}")
        ws.cell(row=row, column=7, value=f'=IF(AND(B{row}=C{row},F{row}=0),"OK","BREAK")')
        expected[f"Summary!D{row}"] = src[key]["target_rows"]
        expected[f"Summary!E{row}"] = src[key]["held_out_rows"]
        expected[f"Summary!G{row}"] = "OK"
    ws.cell(row=r + 5, column=1,
            value="Control counts are typed in from the source extracts (yellow). Every other "
                  "cell is a formula over the data sheets.").font = Font(italic=True, color="595959")

    # ---- control totals
    r = 11
    ws.cell(row=r, column=1, value="2. Year-end balance control totals by currency").font = Font(bold=True, size=12)
    header(ws, r + 1, ["Currency", "Accounts", "Source balance", "Target balance",
                       "Held out balance", "Difference", "Result"])
    totals = q(con, "SELECT * FROM recon_control_totals")
    first = r + 2
    for k, t in enumerate(totals):
        row = first + k
        cur = t["currency"]
        ws.cell(row=row, column=1, value=cur)
        ws.cell(row=row, column=2, value=f'=COUNTIF(Accounts!$F$2:$F${n_acc},A{row})')
        ws.cell(row=row, column=3, value=f'=SUMIFS(Accounts!$E$2:$E${n_acc},Accounts!$F$2:$F${n_acc},A{row})')
        ws.cell(row=row, column=4, value=f'=SUMIFS(Accounts!$E$2:$E${n_acc},Accounts!$F$2:$F${n_acc},A{row},Accounts!$H$2:$H${n_acc},"Y")')
        ws.cell(row=row, column=5, value=f'=SUMIFS(Accounts!$E$2:$E${n_acc},Accounts!$F$2:$F${n_acc},A{row},Accounts!$H$2:$H${n_acc},"N")')
        ws.cell(row=row, column=6, value=f"=ROUND(C{row}-D{row}-E{row},2)")
        ws.cell(row=row, column=7, value=f'=IF(F{row}=0,"OK","BREAK")')
        expected[f"Summary!C{row}"] = float(t["source_balance"])
        expected[f"Summary!D{row}"] = float(t["target_balance"])
        expected[f"Summary!G{row}"] = "OK"
    last = first + len(totals) - 1
    tot = last + 1
    ws.cell(row=tot, column=1, value="Total").font = Font(bold=True)
    for col in "BCDEF":
        ws[f"{col}{tot}"] = f"=SUM({col}{first}:{col}{last})"
        ws[f"{col}{tot}"].font = Font(bold=True)
    ws[f"G{tot}"] = f'=IF(ROUND(F{tot},2)=0,"OK","BREAK")'
    raw_total = float(sum(t["source_balance"] for t in totals))
    ws.cell(row=tot + 1, column=1, value="Raw file total")
    ws.cell(row=tot + 1, column=3, value=raw_total).fill = INPUT_FILL
    ws.cell(row=tot + 1, column=6, value=f"=ROUND(C{tot}-C{tot + 1},2)")
    ws.cell(row=tot + 1, column=7, value=f'=IF(F{tot + 1}=0,"OK","BREAK")')
    expected[f"Summary!G{tot}"] = "OK"
    expected[f"Summary!G{tot + 1}"] = "OK"
    for row in range(first, tot + 2):
        for col in (3, 4, 5, 6):
            ws.cell(row=row, column=col).number_format = MONEY

    # ---- exceptions by rule and severity
    r = tot + 4
    ws.cell(row=r, column=1, value="3. Exceptions by rule and severity").font = Font(bold=True, size=12)
    header(ws, r + 1, ["Rule", "Rule name", "High", "Medium", "Low", "Total"])
    rules = q(con, "SELECT rule_id, rule_name FROM rule_catalogue ORDER BY rule_id")
    counts = {(x["rule_id"], x["severity"]): x["n"] for x in q(
        con, "SELECT rule_id, severity, count(*) AS n FROM exception_register GROUP BY ALL")}
    first = r + 2
    for k, rule in enumerate(rules):
        row = first + k
        ws.cell(row=row, column=1, value=rule["rule_id"])
        ws.cell(row=row, column=2, value=rule["rule_name"])
        for j, sev in enumerate(SEVERITIES):
            col = get_column_letter(3 + j)
            ws[f"{col}{row}"] = (f"=COUNTIFS({reg}!$B$2:$B${n_reg},$A{row},"
                                 f"{reg}!$D$2:$D${n_reg},{col}${r + 1})")
            expected[f"Summary!{col}{row}"] = counts.get((rule["rule_id"], sev), 0)
        ws[f"F{row}"] = f"=SUM(C{row}:E{row})"
    last = first + len(rules) - 1
    ws.cell(row=last + 1, column=1, value="Total").font = Font(bold=True)
    for col in "CDEF":
        ws[f"{col}{last + 1}"] = f"=SUM({col}{first}:{col}{last})"
        ws[f"{col}{last + 1}"].font = Font(bold=True)
    expected[f"Summary!F{last + 1}"] = sum(counts.values())

    # ---- self-certification completeness
    r = last + 4
    ws.cell(row=r, column=1, value="4. Self-certification completeness (accounts in target)").font = Font(bold=True, size=12)
    ws.cell(row=r + 1, column=1, value="Accounts in target")
    ws.cell(row=r + 1, column=3, value=f'=COUNTIF(Accounts!$H$2:$H${n_acc},"Y")')
    ws.cell(row=r + 2, column=1, value="With a complete self-certification")
    ws.cell(row=r + 2, column=3,
            value=f'=COUNTIFS(Accounts!$H$2:$H${n_acc},"Y",Accounts!$N$2:$N${n_acc},"Y")')
    ws.cell(row=r + 3, column=1, value="% complete").font = Font(bold=True)
    ws.cell(row=r + 3, column=3, value=f"=IF(C{r + 1}=0,0,C{r + 2}/C{r + 1})")
    ws.cell(row=r + 3, column=3).number_format = "0.0%"
    ws.cell(row=r + 3, column=3).font = Font(bold=True)
    ws.cell(row=r + 4, column=1,
            value="Complete = a self-certification is on file and the customer has no R02, R03 "
                  "or R08 exception and no R09 exception on the self-certification.").font = Font(
        italic=True, color="595959")
    complete = q(con, """
        SELECT count(*) AS n_target,
               count(*) FILTER (WHERE has_selfcert AND customer_id NOT IN (
                   SELECT customer_id FROM exception_register
                   WHERE customer_id IS NOT NULL AND (rule_id IN ('R02', 'R03', 'R08')
                      OR (rule_id = 'R09' AND source_system = 'self_certification')))) AS n_complete
        FROM tgt_reportable_account""")[0]
    expected[f"Summary!C{r + 1}"] = complete["n_target"]
    expected[f"Summary!C{r + 2}"] = complete["n_complete"]
    expected[f"Summary!C{r + 3}"] = complete["n_complete"] / complete["n_target"]

    # ---- status tracker
    r = r + 7
    ws.cell(row=r, column=1, value="5. Exception status (updates as the register is worked)").font = Font(bold=True, size=12)
    header(ws, r + 1, ["Status", "", "Count", "% of exceptions"])
    for k, s in enumerate(STATUSES):
        row = r + 2 + k
        ws.cell(row=row, column=1, value=s)
        ws.cell(row=row, column=3, value=f'=COUNTIF({reg}!$N$2:$N${n_reg},A{row})')
        ws.cell(row=row, column=4, value=f"=IF(SUM($C${r + 2}:$C${r + 4})=0,0,C{row}/SUM($C${r + 2}:$C${r + 4}))")
        ws.cell(row=row, column=4).number_format = "0.0%"
    expected[f"Summary!C{r + 2}"] = n_reg - 1

    ws.conditional_formatting.add(f"G5:G{tot + 1}", CellIsRule(operator="equal", formula=['"OK"'], fill=GREEN))
    ws.conditional_formatting.add(f"G5:G{tot + 1}", CellIsRule(operator="equal", formula=['"BREAK"'], fill=RED))
    # Column B holds the full rule names; the subtitle wraps across A:G.
    widths(ws, [34, 62, 16, 16, 16, 14, 10])
    ws.merge_cells("A2:G2")
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[2].height = 30


# --------------------------------------------------------------------- lookup
def sheet_lookup(wb, con, n_acc, n_reg, expected):
    ws = wb.create_sheet("Lookup", 2)
    title(ws, "Account lookup",
          "Type an account_id in the yellow cell. Column C uses XLOOKUP; column D gives the "
          "same answer with INDEX-MATCH for Excel versions without XLOOKUP.")
    # Default: an account whose customer has the most exceptions (ties: lowest ID).
    default = q(con, """
        SELECT t.account_id, t.customer_id, count(*) AS n
        FROM tgt_reportable_account t JOIN exception_register e ON e.customer_id = t.customer_id
        GROUP BY ALL ORDER BY n DESC, t.account_id LIMIT 1""")[0]
    ws["B4"] = "account_id"
    ws["C4"] = default["account_id"]
    ws["C4"].fill = INPUT_FILL
    ws["C4"].font = Font(bold=True)
    header(ws, 3, ["", "Field", "XLOOKUP", "INDEX-MATCH"])
    fields = [("customer_id", "B"), ("customer_name", "J"), ("country_of_residence", "K"),
              ("balance_ye", "E"), ("currency", "F"), ("status", "G"), ("in_target", "H"),
              ("selfcert_complete", "N")]
    a = f"Accounts!$A$2:$A${n_acc}"
    for k, (label, col) in enumerate(fields):
        row = 5 + k
        rng = f"Accounts!${col}$2:${col}${n_acc}"
        ws.cell(row=row, column=2, value=label)
        ws.cell(row=row, column=3, value=f'=_xlfn.XLOOKUP($C$4,{a},{rng},"Not found")')
        ws.cell(row=row, column=4, value=f'=IFERROR(INDEX({rng},MATCH($C$4,{a},0)),"Not found")')
        if label == "balance_ye":
            for c in (3, 4):
                ws.cell(row=row, column=c).number_format = MONEY
    expected["Lookup!C5"] = default["customer_id"]
    expected["Lookup!D5"] = default["customer_id"]

    reg = "'Exception register'"
    row = 5 + len(fields) + 1
    ws.cell(row=row, column=2, value="Exceptions on this account or its customer").font = Font(bold=True)
    ws.cell(row=row, column=3, value=f"=COUNT({reg}!$Q$2:$Q${n_reg})")
    n_exc = q(con, """SELECT count(*) AS n FROM exception_register
                      WHERE record_key = ? OR customer_id = ?""",
              [default["account_id"], default["customer_id"]])[0]["n"]
    expected[f"Lookup!C{row}"] = n_exc
    start = row + 2
    header(ws, start, ["#", "exception_id", "rule_id", "severity", "description", "status"])
    for k in range(1, LOOKUP_ROWS + 1):
        rr = start + k
        ws.cell(row=rr, column=1, value=k)
        for j, col in enumerate(["A", "B", "D", "L", "N"]):
            ws.cell(row=rr, column=2 + j,
                    value=f'=IFERROR(INDEX({reg}!${col}$2:${col}${n_reg},'
                          f'MATCH($A{rr},{reg}!$Q$2:$Q${n_reg},0)),"")')
    ws.cell(row=start + LOOKUP_ROWS + 1, column=2,
            value=f"Shows the first {LOOKUP_ROWS} matches. The lookup_match column on the "
                  "register numbers the matching rows.").font = Font(italic=True, color="595959")
    widths(ws, [4, 40, 26, 26, 70, 13])


# ----------------------------------------------------------- rule x country
def sheet_pivot(wb, con, n_reg, expected):
    ws = wb.create_sheet("Rule x country", 3)
    title(ws, "Exceptions by rule and country of residence",
          "Pivot-style summary built with COUNTIFS formulas (openpyxl cannot reliably create a "
          "real pivot table). Recalculates as the register changes.")
    countries = [r["country"] for r in q(con, """
        SELECT country FROM exception_register GROUP BY country
        ORDER BY country = 'Unknown', count(*) DESC, country""")]
    rules = [r["rule_id"] for r in q(con, "SELECT rule_id FROM rule_catalogue ORDER BY rule_id")]
    header(ws, 4, ["Rule"] + countries + ["Total"])
    reg = "'Exception register'"
    counts = {(x["rule_id"], x["country"]): x["n"] for x in q(
        con, "SELECT rule_id, country, count(*) AS n FROM exception_register GROUP BY ALL")}
    for i, rule in enumerate(rules):
        row = 5 + i
        ws.cell(row=row, column=1, value=rule).font = Font(bold=True)
        for j, country in enumerate(countries):
            col = get_column_letter(2 + j)
            ws[f"{col}{row}"] = (f"=COUNTIFS({reg}!$B$2:$B${n_reg},$A{row},"
                                 f"{reg}!$K$2:$K${n_reg},{col}$4)")
        tcol = get_column_letter(2 + len(countries))
        ws[f"{tcol}{row}"] = f"=SUM(B{row}:{get_column_letter(1 + len(countries))}{row})"
        ws[f"{tcol}{row}"].font = Font(bold=True)
        expected[f"Rule x country!{tcol}{row}"] = sum(
            v for (r, _), v in counts.items() if r == rule)
    trow = 5 + len(rules)
    ws.cell(row=trow, column=1, value="Total").font = Font(bold=True)
    for j in range(len(countries) + 1):
        col = get_column_letter(2 + j)
        ws[f"{col}{trow}"] = f"=SUM({col}5:{col}{trow - 1})"
        ws[f"{col}{trow}"].font = Font(bold=True)
    expected[f"Rule x country!{get_column_letter(2 + len(countries))}{trow}"] = sum(counts.values())
    if countries:
        first_col = get_column_letter(2)
        expected[f"Rule x country!{first_col}{trow}"] = sum(
            v for (_, c), v in counts.items() if c == countries[0])
    widths(ws, [8] + [10] * (len(countries) + 1))


# --------------------------------------------------------------------- scores
def sheet_scores(wb, con):
    ws = wb.create_sheet("Check scores")
    title(ws, "How well the checks work",
          "Main answer key was designed alongside the checks; the unseen rounds are the "
          "fairer measure. Recall and precision are formulas.")
    sets = [
        ("Main dataset (designed with the checks)", "main", None),
        ("Unseen round 1, first rules (fair test)", None, UNSEEN_ROUND1_BEFORE_FIXES),
        ("Unseen round 1, current rules (not a fair test)", "unseen_round1", None),
        ("Unseen round 2, current rules (fair test)", "unseen_round2", None),
    ]
    row = 4
    for label, key, static in sets:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True, size=12)
        header(ws, row + 1, ["Rule", "Planted", "Caught", "Missed", "Flags raised",
                             "False positives", "Recall", "Precision"])
        if static:
            data = [(k, *v) for k, v in static.items()]
        else:
            data = con.execute("""SELECT rule_id, injected, caught, missed, flags, false_positives
                                  FROM rule_score WHERE dataset = ? ORDER BY rule_id""",
                               [key]).fetchall()
        first = row + 2
        for k, d in enumerate(data):
            r = first + k
            for c, v in enumerate(d, start=1):
                ws.cell(row=r, column=c, value=v)
            ws[f"G{r}"] = f'=IF(B{r}=0,"",C{r}/B{r})'
            ws[f"H{r}"] = f'=IF(E{r}=0,"",(E{r}-F{r})/E{r})'
        last = first + len(data) - 1
        t = last + 1
        ws.cell(row=t, column=1, value="Total").font = Font(bold=True)
        for col in "BCDEF":
            ws[f"{col}{t}"] = f"=SUM({col}{first}:{col}{last})"
        ws[f"G{t}"] = f'=IF(B{t}=0,"",C{t}/B{t})'
        ws[f"H{t}"] = f'=IF(E{t}=0,"",(E{t}-F{t})/E{t})'
        for r in range(first, t + 1):
            ws[f"G{r}"].number_format = ws[f"H{r}"].number_format = "0%"
        for c in range(1, 9):
            ws.cell(row=t, column=c).font = Font(bold=True)
        row = t + 3
    ws.cell(row=row, column=1,
            value="Recall = caught / planted. Precision = (flags raised - false positives) / "
                  "flags raised. See docs/RULES.md for why each miss happened.").font = Font(
        italic=True, color="595959")
    widths(ws, [46, 9, 9, 9, 12, 14, 9, 10])


# -------------------------------------------------------------------- mapping
def sheet_mapping(wb):
    ws = wb.create_sheet("Mapping")
    title(ws, "Source-to-target mapping", "From docs/MAPPING.md")
    text = (ROOT / "docs" / "MAPPING.md").read_text(encoding="utf-8")
    section = text.split("## Field mapping", 1)[1].split("\n## ", 1)[0]
    lines = [l for l in section.splitlines() if l.startswith("|")]
    rows = [[cell.strip().replace("`", "") for cell in l.strip("|").split("|")] for l in lines]
    rows = [r for r in rows if not all(re.fullmatch(r"-+", c) for c in r)]
    header(ws, 4, rows[0])
    for i, r in enumerate(rows[1:], start=5):
        for j, v in enumerate(r, start=1):
            c = ws.cell(row=i, column=j, value=v)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = BOX
    ws.freeze_panes = "A5"
    widths(ws, [26, 8, 22, 60, 55])


def sheet_lists(wb):
    ws = wb.create_sheet("Lists")
    header(ws, 1, ["Status", "Owner"])
    for i, s in enumerate(STATUSES, start=2):
        ws.cell(row=i, column=1, value=s)
    for i, o in enumerate(OWNERS, start=2):
        ws.cell(row=i, column=2, value=o)
    widths(ws, [16, 24])


def build() -> None:
    con = duckdb.connect(str(DB), read_only=True)
    wb = Workbook()
    expected: dict[str, object] = {}
    n_acc = sheet_accounts(wb, con)
    n_cust = sheet_customers(wb, con)
    n_sc = sheet_selfcerts(wb, con)
    n_reg = sheet_register(wb, con)
    sheet_summary(wb, con, n_acc, n_cust, n_sc, n_reg, expected)
    sheet_lookup(wb, con, n_acc, n_reg, expected)
    sheet_pivot(wb, con, n_reg, expected)
    sheet_scores(wb, con)
    sheet_mapping(wb)
    sheet_lists(wb)
    for name in PRINT_SHEETS:
        print_setup(wb[name], repeat_header=(name == "Exception register"))
    con.close()
    wb.calculation.fullCalcOnLoad = True
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    EXPECTED.write_text(json.dumps(expected, indent=1, default=str), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} with sheets: {', '.join(wb.sheetnames)}")
    print(f"Wrote {len(expected)} expected cell values to {EXPECTED.relative_to(ROOT)}")


if __name__ == "__main__":
    build()
