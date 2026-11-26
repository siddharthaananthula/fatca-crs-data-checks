"""Write docs/RULES.md from the template, the SQL in rules.sql and measured scores.

Run after checks.py (main and --unseen) so the scores are in the database:

    .venv\\Scripts\\python src\\build_rules_doc.py

The SQL shown for each rule is copied from src/rules.sql, so the document
cannot drift from the code.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb

from score_history import UNSEEN_ROUND1_BEFORE_FIXES

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent

# Plain-English description per rule: what, why (with source), simplification.
RULES = {
    "R01": ("Duplicate customer candidates",
            "Flags two customer records that share a date of birth and whose names, after "
            "removing accents, case, punctuation and extra spaces, are the same (exact), "
            "use the same words in a different order (surname written first), have a "
            "Jaro-Winkler similarity of 0.90 or more (fuzzy), or share a surname where one "
            "first name is a single initial matching the other (initial).",
            "Data quality. One person under two customer IDs can have accounts split "
            "across records, so residency and indicia checks see only part of the picture.",
            "Name and date of birth only. Address, TIN and other attributes are not used, "
            "and the 0.90 threshold was chosen for this project, not taken from a source. "
            "Known limit: twins (same surname and date of birth, similar first names) are "
            "flagged, because name and date of birth alone cannot tell them apart. This is "
            "left as a manual review step on purpose."),
    "R02": ("Missing TIN for a declared tax residency",
            "Flags each declared tax residency on the self-certification whose TIN is "
            "blank, a known placeholder (N/A, NA, NONE, NIL, UNKNOWN, -), or any value "
            "with no digits at all (for example TBA, NOT APPLICABLE, XXXXXXXXX).",
            "TINs are a reported item for each jurisdiction of residence [CRS] Section "
            "I A(1)(a), and a self-certification for a new account must include the TIN "
            "[CRS] Section IV B.",
            "The source data has no field for a reason why a TIN is not available, so "
            "every gap is raised. A reviewer must check whether the [CRS] Section I D "
            "exceptions (TIN not issued or not required) apply."),
    "R03": ("TIN format invalid for its country",
            "Flags a provided TIN that does not match the project's fake pattern for "
            "its country (`<country>-FAKE-` plus a set number of digits), or whose "
            "digits are all the same.",
            "Data quality on a reported item ([CRS] Section I A(1)(a)). A TIN in the "
            "wrong shape is likely to be a keying error.",
            "The patterns are invented for this project. Real TIN formats and check "
            "digits are not tested."),
    "R04": ("Residence country does not match self-certified tax residency",
            "Flags a customer whose country of residence in the customer master is not "
            "any of the tax residencies declared on the latest self-certification. "
            "Skipped when a declared country cannot be read, because the comparison "
            "would not be reliable (R09 raises those).",
            "A self-certification must be confirmed as reasonable against the "
            "information the institution holds, and cannot be relied on when there is "
            "reason to know it is incorrect or unreliable ([CRS] Section IV A and C; "
            "[IGA] Annex I, Section III B).",
            "A mismatch is a prompt to review, not proof the self-certification is "
            "wrong: a person can live in one country and be tax resident in another."),
    "R05": ("US indicia with no US person declaration",
            "Flags a customer with any of: US nationality, a US residence address, a US "
            "mailing address (including addresses ending US, USA, U.S. or U.S.A.), a phone "
            "number starting +1 or 001, or a US place of birth, where the "
            "self-certification does not declare a US person (declaration N, unreadable, "
            "or no self-certification at all).",
            "These are US indicia under [IGA] Annex I, Section II B(1). When indicia are "
            "found, the account is treated as US reportable unless curing documents are "
            "held ([IGA] Annex I, Section II B(3) and B(4)).",
            "A declaration of N is not treated as a cure, because [IGA] B(4) also asks "
            "for documentary evidence, which this data does not hold. +1 is treated as "
            "a US phone, although it is shared with Canada and other countries. Standing "
            "instructions, power of attorney and hold mail indicia are not in the data."),
    "R06": ("Orphan accounts",
            "Flags an account whose customer_id (after trimming and uppercasing) is not "
            "in the customer master.",
            "Data quality and completeness. An account without an owner cannot be "
            "assessed or reported.",
            "No fuzzy matching of customer IDs is attempted."),
    "R07": ("Customers with no accounts",
            "Flags a customer who has no account in the account extract.",
            "Data quality and completeness. Usually harmless, but can point to a missing "
            "or incomplete account feed.",
            "None."),
    "R08": ("Missing or out-of-date self-certification",
            "Flags a customer with no self-certification (High), or whose latest "
            "self-certification has no readable signature date, is dated after the "
            "reporting date, or carries an impossible or placeholder date: before the "
            "customer's date of birth, or 1900-01-01 or earlier (Medium).",
            "A self-certification must be obtained for new accounts ([CRS] Section IV A; "
            "[IGA] Annex I, Section III B), and whether a valid one was provided is a "
            "reported item ([CRS] Section I A(1)(a)).",
            "\"Out of date\" here means undated, future dated or a placeholder date only. "
            "No age limit is "
            "applied, because none was found in the sources checked. Preexisting accounts "
            "can be documented in other ways, which this model does not cover."),
    "R09": ("Format or standardisation failures",
            "Flags each value that the transformation could not standardise (an "
            "unreadable date, country, currency, flag or ID), or a required value that "
            "is blank. These come from `exc_transform`. Severity is Low for descriptive "
            "fields and Medium for fields that drive the checks.",
            "Unreadable values leave other checks incomplete. Currency is required to "
            "report amounts ([CRS] Section I B).",
            "Staging only knows the formats listed in docs/MAPPING.md. A valid value in "
            "an unknown layout is raised here rather than guessed."),
}


def sql_blocks() -> dict[str, str]:
    """Split rules.sql into one block per rule, from '-- R0x' to the next rule."""
    text = (SRC / "rules.sql").read_text(encoding="utf-8")
    body = text.split("-- R01", 1)[1].split("-- ----", 1)[0]
    parts = re.split(r"^-- (R0\d)", "-- R01" + body, flags=re.M)
    return {parts[i]: f"-- {parts[i]}{parts[i + 1]}".strip() for i in range(1, len(parts), 2)}


def score_rows(con, dataset: str) -> list[tuple]:
    return con.execute("""SELECT rule_id, injected, caught, missed, flags, false_positives
                          FROM rule_score WHERE dataset = ? ORDER BY rule_id""",
                       [dataset]).fetchall()


def score_table(rows: list[tuple], dataset: str = "") -> str:
    if not rows:
        return f"_No {dataset} score in the database. Run src/checks.py first._"
    lines = ["| Rule | Planted | Caught | Missed | Flags raised | False positives |",
             "|---|---|---|---|---|---|"]
    tot = [0] * 5
    for r in rows:
        lines.append("| " + " | ".join(str(v) for v in r) + " |")
        tot = [a + b for a, b in zip(tot, r[1:])]
    lines.append("| **Total** | " + " | ".join(f"**{v}**" for v in tot) + " |")
    return "\n".join(lines)


def build() -> str:
    blocks = sql_blocks()
    sections = []
    for rule, (name, what, why, simple) in RULES.items():
        sections.append(
            f"## {rule} {name}\n\n"
            f"**What it checks.** {what}\n\n"
            f"**Why it matters.** {why}\n\n"
            f"**Simplifications.** {simple}\n\n"
            f"**SQL** (from `src/rules.sql`):\n\n```sql\n{blocks[rule]}\n```\n")
    con = duckdb.connect(str(ROOT / "data" / "fatca_crs.duckdb"), read_only=True)
    template = (SRC / "templates" / "RULES_TEMPLATE.md").read_text(encoding="utf-8")
    doc = (template.replace("{{RULES}}", "\n".join(sections).rstrip())
                   .replace("{{MAIN_SCORE}}", score_table(score_rows(con, "main"), "main"))
                   .replace("{{ROUND1_BEFORE}}", score_table(
                       [(k, *v) for k, v in UNSEEN_ROUND1_BEFORE_FIXES.items()]))
                   .replace("{{ROUND1_AFTER}}", score_table(
                       score_rows(con, "unseen_round1"), "round 1"))
                   .replace("{{ROUND2}}", score_table(
                       score_rows(con, "unseen_round2"), "round 2")))
    con.close()
    return doc


def main() -> None:
    out = ROOT / "docs" / "RULES.md"
    out.write_text(build(), encoding="utf-8")
    print(f"Wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
