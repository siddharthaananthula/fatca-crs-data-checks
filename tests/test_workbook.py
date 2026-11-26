"""Structural checks on the built workbook (offline; Excel itself is not needed).

src/verify_workbook.py does the full recalculation check in Excel.
"""

from pathlib import Path

import pytest
from openpyxl import load_workbook

BOOK = Path(__file__).resolve().parent.parent / "outputs" / "exception_workpaper.xlsx"

pytestmark = pytest.mark.skipif(not BOOK.exists(), reason="run src/build_workbook.py first")


@pytest.fixture(scope="module")
def wb():
    return load_workbook(BOOK)


def formulas(ws):
    return [c.value for row in ws.iter_rows() for c in row
            if isinstance(c.value, str) and c.value.startswith("=")]


def test_sheets_present(wb):
    assert wb.sheetnames[:4] == ["Summary", "Exception register", "Lookup", "Rule x country"]
    assert {"Mapping", "Accounts", "Customers", "Self-certs", "Check scores"} <= set(wb.sheetnames)


def test_summary_uses_real_formulas(wb):
    text = " ".join(formulas(wb["Summary"]))
    for fn in ("COUNTIFS(", "SUMIFS(", "COUNTA(", '"BREAK"'):
        assert fn in text


def test_register_is_a_filterable_table_with_dropdowns_and_colours(wb):
    ws = wb["Exception register"]
    assert "tblExceptionRegister" in ws.tables
    lists = {dv.formula1 for dv in ws.data_validations.dataValidation}
    assert "=Lists!$A$2:$A$4" in lists                       # Status
    assert any(f.startswith("=Lists!$B$2") for f in lists)    # Owner
    rules = [r.formula[0] for cf in ws.conditional_formatting for r in cf.rules]
    assert {'$D2="High"', '$D2="Medium"', '$D2="Low"'} <= set(rules)
    assert ws["N1"].value == "status" and ws["N2"].value == "Open"


def test_lookup_has_xlookup_and_index_match(wb):
    text = " ".join(formulas(wb["Lookup"]))
    assert "XLOOKUP(" in text and "INDEX(" in text and "MATCH(" in text


def test_pivot_and_mapping(wb):
    assert "COUNTIFS(" in " ".join(formulas(wb["Rule x country"]))
    assert wb["Mapping"]["A4"].value == "Target field"
    assert wb["Mapping"].max_row > 30


def test_print_layout_for_client_sheets(wb):
    for name in ["Summary", "Exception register", "Lookup", "Rule x country", "Check scores"]:
        ws = wb[name]
        assert ws.page_setup.orientation == "landscape", name
        assert ws.sheet_properties.pageSetUpPr.fitToPage, name
        assert ws.page_setup.fitToWidth == 1 and ws.page_setup.fitToHeight == 0, name
        assert "&A" in ws.oddFooter.left.text and "&P" in ws.oddFooter.right.text, name
    assert wb["Exception register"].print_title_rows == "$1:$1"
    summary = wb["Summary"]
    assert "A2:G2" in {str(r) for r in summary.merged_cells.ranges}
    assert summary["A2"].alignment.wrap_text
    longest = max(len(summary.cell(row=r, column=2).value or "")
                  for r in range(1, summary.max_row + 1)
                  if isinstance(summary.cell(row=r, column=2).value, str)
                  and not str(summary.cell(row=r, column=2).value).startswith("="))
    assert summary.column_dimensions["B"].width >= longest
