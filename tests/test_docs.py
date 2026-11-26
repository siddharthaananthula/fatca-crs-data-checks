"""Keep documentation in step with code, and in the project's house style."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_rules_doc_sql_matches_rules_sql():
    doc = (ROOT / "docs" / "RULES.md").read_text(encoding="utf-8")
    sql = (ROOT / "src" / "rules.sql").read_text(encoding="utf-8")
    blocks = re.findall(r"```sql\n(.*?)\n```", doc, flags=re.S)
    assert len(blocks) == 9
    for block in blocks:
        assert block in sql, block.splitlines()[0]


def test_house_style_no_em_dashes_or_banned_words():
    files = list((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "—" not in text, f"em dash in {path.name}"
        assert not re.search(r"\b(production|deployed)\b", text, re.I), path.name


# ------------------------------------------------ numbers match the outputs
import duckdb  # noqa: E402
import pytest  # noqa: E402

DB = ROOT / "data" / "fatca_crs.duckdb"
needs_db = pytest.mark.skipif(not DB.exists(), reason="run src/checks.py first")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(DB), read_only=True)
    yield c
    c.close()


def score_row(con, dataset):
    return con.execute("""SELECT sum(injected), sum(caught), sum(missed), sum(false_positives)
                          FROM rule_score WHERE dataset = ?""", [dataset]).fetchone()


@needs_db
def test_readme_scores_match_database(con):
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from score_history import UNSEEN_ROUND1_BEFORE_FIXES as r1

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    r1_tot = [sum(v[i] for v in r1.values()) for i in (0, 1, 2, 4)]
    for label, (planted, caught, missed, fp) in [
        ("Main (2,000 customers) | Current", score_row(con, "main")),
        ("Unseen round 1 (41 customers, seed 4242) | First version", r1_tot),
        ("Unseen round 2 (38 customers, seed 7777) | Current", score_row(con, "unseen_round2")),
    ]:
        assert f"| {label} | {planted} | {caught} | {missed} | {fp} |" in readme, label


@needs_db
def test_readme_reconciliation_and_register_match_database(con):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    counts = {r[0]: r for r in con.execute("SELECT * FROM recon_counts").fetchall()}
    a, c, s = counts["accounts"], counts["customer_master"], counts["self_certification"]
    assert all(r[-1] == "OK" for r in counts.values())
    assert f"accounts {a[1]:,} = {a[3]:,} in target + {a[4]:,} held out" in readme
    assert f"customers {c[1]:,} = {c[3]:,} + {c[4]:,}" in readme
    assert f"self-certifications {s[1]:,} = {s[3]:,} + {s[4]:,}" in readme
    sev = dict(con.execute("SELECT severity, count(*) FROM exception_register GROUP BY 1").fetchall())
    total = sum(sev.values())
    assert (f"{total} exceptions: High {sev['High']}, Medium {sev['Medium']}, "
            f"Low {sev['Low']}") in readme
    lines = con.execute("SELECT count(*), bool_and(result = 'OK'), sum(source_balance) "
                        "FROM recon_control_totals").fetchone()
    assert lines[1] and f"OK in all {lines[0]} currency lines" in readme
    assert f"({lines[2]:,.2f})" in readme


@needs_db
def test_exception_summary_is_current(con):
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from build_summary_doc import build

    on_disk = (ROOT / "docs" / "EXCEPTION_SUMMARY.md").read_text(encoding="utf-8")
    assert on_disk == build(con)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for fragment in re.findall(r"\d[\d,]* of [\d,]* customers", on_disk):
        assert fragment in readme
    pct = re.search(r"Only (\d+\.\d)% of reported accounts\s+\(([\d,]+) of\s+([\d,]+)\)", on_disk)
    assert f"{pct.group(1)}% of target accounts ({pct.group(2)} of {pct.group(3)})" in readme
