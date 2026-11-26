"""Recalculate outputs/exception_workpaper.xlsx in Microsoft Excel and check it.

Windows with Excel only (driven through PowerShell and COM). LibreOffice was
not available on the machine this was built on. The workbook is opened
read-only and never saved.

    .venv\\Scripts\\python src\\verify_workbook.py

Checks:
  1. Every formula calculates without an Excel error (#NAME?, #VALUE!, #REF! ...).
  2. Key cells match values computed independently from DuckDB
     (data/workbook_expected.json, written by build_workbook.py).
  3. The workbook responds: marking an exception Resolved updates the status
     counts, and a new account_id on the Lookup sheet changes the results.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOK = ROOT / "outputs" / "exception_workpaper.xlsx"
EXPECTED = ROOT / "data" / "workbook_expected.json"

PS_SCRIPT = r"""
param([string]$Book, [string]$CellsFile, [string]$OutFile)
$ErrorActionPreference = 'Stop'
$cells = Get-Content $CellsFile -Raw | ConvertFrom-Json
$x = New-Object -ComObject Excel.Application
$x.Visible = $false
$x.DisplayAlerts = $false
$result = [ordered]@{ excel_version = $x.Version; sheets = @(); values = [ordered]@{}; interactive = [ordered]@{} }
try {
    $wb = $x.Workbooks.Open($Book, 0, $true)
    $x.CalculateFull()
    foreach ($ws in $wb.Worksheets) {
        $formulas = 0; $errors = @()
        try { $formulas = $ws.UsedRange.SpecialCells(-4123).Count } catch {}
        try {
            foreach ($c in $ws.UsedRange.SpecialCells(-4123, 16).Cells) {
                if ($errors.Count -lt 10) { $errors += ($c.Address(0, 0) + ' ' + $c.Text) }
            }
        } catch {}
        $result.sheets += [ordered]@{ name = $ws.Name; formulas = $formulas; errors = $errors }
    }
    foreach ($key in $cells) {
        $sheet, $addr = $key -split '!', 2
        $result.values[$key] = $wb.Worksheets.Item($sheet).Range($addr).Value2
    }
    # Interactive checks (not saved)
    $sum = $wb.Worksheets.Item('Summary'); $reg = $wb.Worksheets.Item('Exception register')
    $lk = $wb.Worksheets.Item('Lookup')
    $statusRow = 0
    for ($r = 1; $r -le 200; $r++) { if ($sum.Cells.Item($r, 1).Text -eq 'Resolved') { $statusRow = $r; break } }
    $result.interactive.resolved_before = $sum.Cells.Item($statusRow, 3).Value2
    $reg.Range('N2').Value2 = 'Resolved'
    $x.Calculate()
    $result.interactive.resolved_after = $sum.Cells.Item($statusRow, 3).Value2
    $result.interactive.lookup_before = $lk.Range('C5').Text
    $lk.Range('C4').Value2 = 'A0000001'
    $x.Calculate()
    $result.interactive.lookup_after_xlookup = $lk.Range('C5').Text
    $result.interactive.lookup_after_index = $lk.Range('D5').Text
    $wb.Close($false)
} finally {
    $x.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($x)
}
$result | ConvertTo-Json -Depth 5 | Set-Content -Path $OutFile -Encoding UTF8
"""


def main() -> int:
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "verify.ps1").write_text(PS_SCRIPT, encoding="utf-8")
        (tmp / "cells.json").write_text(json.dumps(list(expected)), encoding="utf-8")
        out = tmp / "result.json"
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(tmp / "verify.ps1"), str(BOOK), str(tmp / "cells.json"), str(out)],
            capture_output=True, text=True)
        if proc.returncode != 0 or not out.exists():
            print("Excel check could not run:", proc.stderr.strip()[:500])
            return 2
        result = json.loads(out.read_text(encoding="utf-8-sig"))

    print(f"Recalculated in Excel {result['excel_version']}")
    total_formulas = total_errors = 0
    for s in result["sheets"]:
        total_formulas += s["formulas"]
        total_errors += len(s["errors"])
        flag = "" if not s["errors"] else f"  ERRORS: {s['errors']}"
        print(f"  {s['name']:<20} formulas {s['formulas']:>6}{flag}")
    print(f"  total formulas {total_formulas}, cells in error {total_errors}")

    mismatches = []
    for key, want in expected.items():
        got = result["values"].get(key)
        if isinstance(want, (int, float)) and isinstance(got, (int, float)):
            ok = abs(got - want) < 0.005
        else:
            ok = got == want
        if not ok:
            mismatches.append((key, want, got))
    print(f"Key cells matching DuckDB: {len(expected) - len(mismatches)} of {len(expected)}")
    for m in mismatches:
        print(f"  MISMATCH {m[0]}: expected {m[1]!r}, Excel {m[2]!r}")

    i = result["interactive"]
    resolved_ok = (i["resolved_after"] or 0) == (i["resolved_before"] or 0) + 1
    lookup_ok = (i["lookup_after_xlookup"] == i["lookup_after_index"]
                 and i["lookup_after_xlookup"] != i["lookup_before"])
    print(f"Marking one exception Resolved: {i['resolved_before']} -> {i['resolved_after']} "
          f"{'OK' if resolved_ok else 'FAIL'}")
    print(f"Changing the Lookup account: customer {i['lookup_before']} -> "
          f"{i['lookup_after_xlookup']} (XLOOKUP) / {i['lookup_after_index']} (INDEX-MATCH) "
          f"{'OK' if lookup_ok else 'FAIL'}")
    print("Workbook was not saved by this check.")
    return 0 if not (total_errors or mismatches or not resolved_ok or not lookup_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
