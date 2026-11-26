# Rules R01 to R09

Each rule is described in plain English first, then shown as the SQL that
runs in `src/rules.sql`. This is a simplified educational model built on
synthetic data. It is not tax advice and not a complete implementation of
FATCA or CRS. Where a rule simplifies the source material, it says so.

## Sources

- **[CRS]** OECD (2025), *Consolidated text of the Common Reporting Standard:
  Standard for Automatic Exchange of Financial Account Information in Tax
  Matters*, OECD Publishing, Paris. https://doi.org/10.1787/055664b1-en
- **[IGA]** *Agreement between the Government of Australia and the Government
  of the United States of America to Improve International Tax Compliance and
  to Implement FATCA*, signed 28 April 2014, Annex I.
  https://treasury.gov.au/sites/default/files/2019-03/Australia-US-FATCA-IGA.pdf
- **[ATO]** ATO guidance on CRS and FATCA due diligence, for example
  https://www.ato.gov.au/about-ato/tax-avoidance/international-exchange-of-information/automatic-exchange-of-information/automatic-exchange-of-information-crs-and-fatca/4-due-diligence
  The ATO site refused automated access while this project was built, so it
  is listed as further reading only. No statement below relies on it.

Section references below were checked against the text of [CRS] and [IGA].

## What the sources say, in brief

- [CRS] Section I A(1)(a): for each reportable person, report the "name,
  address, jurisdiction(s) of residence, TIN(s) and date and place of birth"
  and "whether the Account Holder has provided a valid self-certification".
- [CRS] Section I B: "The information reported must identify the currency in
  which each amount is denominated."
- [CRS] Section I D: a TIN is not required if it is not issued by the
  jurisdiction, or domestic law there does not require it to be collected.
- [CRS] Section II B: a balance is "determined as of the last day of the
  calendar year or other appropriate reporting period".
- [CRS] Section III B(2): the electronic record search for lower value
  preexisting accounts looks for indicia including residence in a reportable
  jurisdiction, a current mailing or residence address there, and telephone
  numbers there.
- [CRS] Section IV A and C: for new individual accounts, obtain a
  self-certification at account opening and "confirm the reasonableness" of
  it; after a change of circumstances that gives "reason to know" it is
  incorrect or unreliable, it cannot be relied on.
- [IGA] Annex I, Section II B(1): US indicia include identification as a US
  citizen or resident, an "unambiguous indication of a U.S. place of birth", a
  "current U.S. mailing or residence address", and a "current U.S. telephone
  number".
- [IGA] Annex I, Section II B(3) and B(4): if US indicia are found, the
  account is treated as US reportable unless curing documents are held. For a
  US address or phone, that is a self-certification plus documentary
  evidence; for a US place of birth, a self-certification, a non-US passport
  or ID, and a loss of nationality certificate or reasonable explanation.
- [IGA] Annex I, Section III B: for new individual accounts, obtain a
  self-certification and confirm its reasonableness.

## Project parameters

- Reporting date: 30 June 2026. This is the year end chosen for this
  fictional institution, in line with [CRS] Section II B ("calendar year or
  other appropriate reporting period"). It is not a statement about any
  jurisdiction's actual reporting period.
- All TIN formats are invented (`AU-FAKE-123456789`). No real TIN format is
  checked.
- No balance thresholds are applied. Every account is checked.

## Severity

| Severity | Meaning in this project |
|---|---|
| High | Could cause an account to be reported wrongly or not at all |
| Medium | Weakens a check or a reported value, but a value is present |
| Low | Housekeeping, or a field not used to decide residency or US status |

{{RULES}}

## Results

### Main dataset (2,000 customers, 3,000 accounts)

{{MAIN_SCORE}}

The main answer key was designed alongside the checks, so this score shows
that the rules do what they were built to do. It is not evidence of how they
would perform on new data. The unseen-variant rounds below are the fairer
measure.

### How the unseen-variant rounds work

Each round is a small dataset with its own seed, written after the rules it
tests were fixed, using issue variants that appear in no earlier dataset. It
also has clean records and hard negatives. Its answer key records the ideal
outcome. A round is scored once and the rules are not tuned on the result.

| Round | Seed | Written | Tests |
|---|---|---|---|
| 1 | 4242 | After the first version of the rules | First version |
| 2 | 7777 | After the round 1 fixes, before any round 2 result was seen | Current rules |

### Round 1: first version of the rules (fair test)

Measured on 41 customers and 42 accounts, before any fixes. Kept exactly as
first measured (`src/score_history.py`).

{{ROUND1_BEFORE}}

What round 1 showed, and what was changed in response:

| Rule | Round 1 finding | Change made |
|---|---|---|
| R01 | Missed a surname written first | Added word-order matching |
| R01 | Missed a first name reduced to an initial | Added initial matching (same surname and date of birth) |
| R01 | False positive on twins | None. Known limit of name and date of birth matching, left for manual review |
| R01 | Missed a date of birth written yyyy/mm/dd (staging cannot read it) | None. Still raised under R09 |
| R02 | Missed TBA, NOT APPLICABLE, XXXXXXXXX, n.a. | A TIN with no digits now counts as missing |
| R05 | Missed a mailing address ending "U.S." | Added U.S. as a country alias |
| R05 | Missed a phone written 001 ... | Phone country codes are read after + or 00 |
| R05 | Missed birthplace "Boston, Massachusetts" | None. Still raised under R09 |
| R08 | Missed a 1900-01-01 signature date | Dates before the customer's birth, or 1900-01-01 or earlier, are now flagged |

### Round 1 re-scored on the current rules (not a fair test)

The fixes above were designed from round 1, so this only confirms they work
as intended. It is not evidence of performance on new data.

{{ROUND1_AFTER}}

### Round 2: current rules (fair test)

Measured on 38 customers and 39 accounts with seed 7777. Written before any
round 2 result was seen; the rules were not changed afterwards.

{{ROUND2}}

Why the round 2 misses and false positives happen:

| Rule | What happened | Why | Possible fix (not applied) |
|---|---|---|---|
| R09 | **Missed a balance written 12.345,67. Staging read it as 12.35, so a wrong value reached the target** | Commas are removed as thousands separators; a European decimal comma is not recognised. Control totals cannot catch it, because the source total is parsed the same way | Reject a balance where a comma follows a full stop, and flag it for manual review |
| R01 | Missed nickname Bill for William | No nickname list; similarity below 0.90 | Nickname table, with manual review |
| R01 | Missed day and month of birth swapped | The rule needs an identical date of birth | Also compare with day and month swapped |
| R02 | Missed placeholder 999999999 | It has digits, so it is treated as a TIN; R03 raised it instead (1 false positive there) | Treat a TIN of one repeated digit with no prefix as missing |
| R04 | Missed declared residency Austria | AT is not in the reference country list, so the comparison is skipped by design; R09 raised it | Extend the reference list |
| R05 | Missed a phone written (+1) ... | Only numbers starting + or 00 are read | Strip brackets before reading the code |
| R05 | Missed "United States." with a full stop, nationality "American", birthplace "Chicago, IL" | Not in the alias lists; each was raised under R09 instead | Strip trailing punctuation; add demonyms and US state codes |
| R09 | 7 false positives | Valid values staging does not know: Australian dollar, Active, Republic of Ireland, AT, and the three R05 cases above. Flagged for manual fixing, not lost | Extend staging and the reference lists |

The staging step standardised 2 of 5 round 2 format variants (Great Britain
and an unpadded ISO date). The twins limit from round 1 still applies; round 2
had a different hard negative (same surname and date of birth, different
first initial), which was correctly not flagged.
