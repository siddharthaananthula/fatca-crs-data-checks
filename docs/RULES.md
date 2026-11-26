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

## R01 Duplicate customer candidates

**What it checks.** Flags two customer records that share a date of birth and whose names, after removing accents, case, punctuation and extra spaces, are the same (exact), use the same words in a different order (surname written first), have a Jaro-Winkler similarity of 0.90 or more (fuzzy), or share a surname where one first name is a single initial matching the other (initial).

**Why it matters.** Data quality. One person under two customer IDs can have accounts split across records, so residency and indicia checks see only part of the picture.

**Simplifications.** Name and date of birth only. Address, TIN and other attributes are not used, and the 0.90 threshold was chosen for this project, not taken from a source. Known limit: twins (same surname and date of birth, similar first names) are flagged, because name and date of birth alone cannot tell them apart. This is left as a manual review step on purpose.

**SQL** (from `src/rules.sql`):

```sql
-- R01 duplicate customer candidates: same date of birth and any of
--   exact:   the same normalised name
--   order:   the same words in a different order (surname written first)
--   fuzzy:   Jaro-Winkler similarity of at least 0.90
--   initial: same surname, and one first name is a single initial matching the other
INSERT INTO rule_flags
WITH c AS (
    SELECT customer_id, name_normalised, date_of_birth,
           list_filter(string_split(regexp_replace(name_normalised, '[^A-Z ]', ' ', 'g'), ' '),
                       lambda t: t <> '') AS words
    FROM stg_customer
    WHERE customer_id IS NOT NULL AND name_normalised IS NOT NULL
      AND date_of_birth IS NOT NULL
),
k AS (
    SELECT *, array_to_string(list_sort(words), ' ') AS sorted_words FROM c
),
pairs AS (
    SELECT a.customer_id AS a_id, b.customer_id AS b_id,
           a.name_normalised AS a_name, b.name_normalised AS b_name,
           jaro_winkler_similarity(a.name_normalised, b.name_normalised) AS similarity,
           CASE
               WHEN a.name_normalised = b.name_normalised THEN 'exact'
               WHEN a.sorted_words = b.sorted_words THEN 'order'
               WHEN jaro_winkler_similarity(a.name_normalised, b.name_normalised) >= 0.90 THEN 'fuzzy'
               WHEN len(a.words) >= 2 AND len(b.words) >= 2
                AND a.words[-1] = b.words[-1]
                AND (length(a.words[1]) = 1 OR length(b.words[1]) = 1)
                AND left(a.words[1], 1) = left(b.words[1], 1) THEN 'initial'
           END AS match_type
    FROM k a JOIN k b
      ON a.date_of_birth = b.date_of_birth AND a.customer_id < b.customer_id
)
SELECT 'R01', 'customer_master', b_id, b_id, a_id, 'full_name',
       CASE match_type
           WHEN 'exact' THEN 'Exact match with ' || a_id || ' on normalised name and date of birth'
           WHEN 'order' THEN 'Same name words as ' || a_id || ' in a different order (''' || a_name
                             || ''' vs ''' || b_name || '''), same date of birth'
           WHEN 'fuzzy' THEN 'Fuzzy match with ' || a_id || ': ''' || a_name || ''' vs '''
                             || b_name || ''' (similarity ' || round(similarity, 2)
                             || '), same date of birth'
           ELSE 'Initial match with ' || a_id || ': ''' || a_name || ''' vs ''' || b_name
                || ''', same surname and date of birth'
       END,
       NULL, NULL
FROM pairs
WHERE match_type IS NOT NULL;
```

## R02 Missing TIN for a declared tax residency

**What it checks.** Flags each declared tax residency on the self-certification whose TIN is blank, a known placeholder (N/A, NA, NONE, NIL, UNKNOWN, -), or any value with no digits at all (for example TBA, NOT APPLICABLE, XXXXXXXXX).

**Why it matters.** TINs are a reported item for each jurisdiction of residence [CRS] Section I A(1)(a), and a self-certification for a new account must include the TIN [CRS] Section IV B.

**Simplifications.** The source data has no field for a reason why a TIN is not available, so every gap is raised. A reviewer must check whether the [CRS] Section I D exceptions (TIN not issued or not required) apply.

**SQL** (from `src/rules.sql`):

```sql
-- R02 missing TIN: a declared residency (country present, readable or not)
-- with a blank or placeholder TIN.
INSERT INTO rule_flags
SELECT 'R02', 'self_certification', r.selfcert_id, r.customer_id, NULL, 'tin_' || r.slot,
       'No TIN for declared residency ' || coalesce(r.country, trim(r.country_raw))
       || CASE WHEN r.tin_status = 'PLACEHOLDER'
               THEN ' (placeholder ''' || trim(r.tin_raw) || ''')' ELSE ' (blank)' END,
       NULL, NULL
FROM latest_residency r
WHERE nullif(trim(r.country_raw), '') IS NOT NULL
  AND r.tin_status <> 'PROVIDED';
```

## R03 TIN format invalid for its country

**What it checks.** Flags a provided TIN that does not match the project's fake pattern for its country (`<country>-FAKE-` plus a set number of digits), or whose digits are all the same.

**Why it matters.** Data quality on a reported item ([CRS] Section I A(1)(a)). A TIN in the wrong shape is likely to be a keying error.

**Simplifications.** The patterns are invented for this project. Real TIN formats and check digits are not tested.

**SQL** (from `src/rules.sql`):

```sql
-- R03 TIN format: a provided TIN for a readable country that does not match
-- the country's fake pattern, or whose digits are all the same.
INSERT INTO rule_flags
SELECT 'R03', 'self_certification', r.selfcert_id, r.customer_id, NULL, 'tin_' || r.slot,
       'TIN ''' || r.tin || ''' does not fit the ' || r.country || ' format'
       || CASE WHEN regexp_full_match(r.tin, f.pattern) THEN ' (all digits the same)' ELSE '' END,
       NULL, NULL
FROM latest_residency r
JOIN ref_tin_format f ON f.iso2 = r.country
WHERE r.tin_status = 'PROVIDED'
  AND (NOT regexp_full_match(r.tin, f.pattern)
       OR length(list_distinct(string_split(regexp_extract(r.tin, '([0-9]+)$', 1), ''))) = 1);
```

## R04 Residence country does not match self-certified tax residency

**What it checks.** Flags a customer whose country of residence in the customer master is not any of the tax residencies declared on the latest self-certification. Skipped when a declared country cannot be read, because the comparison would not be reliable (R09 raises those).

**Why it matters.** A self-certification must be confirmed as reasonable against the information the institution holds, and cannot be relied on when there is reason to know it is incorrect or unreliable ([CRS] Section IV A and C; [IGA] Annex I, Section III B).

**Simplifications.** A mismatch is a prompt to review, not proof the self-certification is wrong: a person can live in one country and be tax resident in another.

**SQL** (from `src/rules.sql`):

```sql
-- R04 residence mismatch: customer master residence is not among the declared
-- tax residencies. Skipped when any declared country is unreadable (R09 raises
-- those), because the comparison would not be reliable.
INSERT INTO rule_flags
WITH declared AS (
    SELECT customer_id,
           list(country ORDER BY slot) FILTER (WHERE country IS NOT NULL) AS countries,
           bool_or(country IS NULL AND nullif(trim(country_raw), '') IS NOT NULL) AS has_unreadable
    FROM latest_residency GROUP BY customer_id
)
SELECT 'R04', 'customer_master', c.customer_id, c.customer_id, NULL, 'country_of_residence',
       'Customer master residence ' || c.country_of_residence
       || ' but self-certified tax residency is ' || array_to_string(d.countries, ', '),
       NULL, NULL
FROM stg_customer c JOIN declared d USING (customer_id)
WHERE c.country_of_residence IS NOT NULL
  AND NOT d.has_unreadable
  AND len(d.countries) > 0
  AND NOT list_contains(d.countries, c.country_of_residence);
```

## R05 US indicia with no US person declaration

**What it checks.** Flags a customer with any of: US nationality, a US residence address, a US mailing address (including addresses ending US, USA, U.S. or U.S.A.), a phone number starting +1 or 001, or a US place of birth, where the self-certification does not declare a US person (declaration N, unreadable, or no self-certification at all).

**Why it matters.** These are US indicia under [IGA] Annex I, Section II B(1). When indicia are found, the account is treated as US reportable unless curing documents are held ([IGA] Annex I, Section II B(3) and B(4)).

**Simplifications.** A declaration of N is not treated as a cure, because [IGA] B(4) also asks for documentary evidence, which this data does not hold. +1 is treated as a US phone, although it is shared with Canada and other countries. Standing instructions, power of attorney and hold mail indicia are not in the data.

**SQL** (from `src/rules.sql`):

```sql
-- R05 US indicia with no US person declaration (us_person is not Y, or there
-- is no self-certification).
INSERT INTO rule_flags
WITH indicia AS (
    SELECT c.customer_id, s.us_person, s.selfcert_id,
           list_filter([
               CASE WHEN c.nationality = 'US' THEN 'US nationality' END,
               CASE WHEN c.country_of_residence = 'US' OR c.residence_address_country = 'US'
                    THEN 'US residence address' END,
               CASE WHEN c.mailing_address_country = 'US' THEN 'US mailing address' END,
               CASE WHEN list_contains(string_split(c.phone_country_codes, ';'), '1')
                    THEN 'US phone number (+1)' END,
               CASE WHEN c.birth_country = 'US' THEN 'US place of birth' END
           ], lambda x: x IS NOT NULL) AS found
    FROM stg_customer c LEFT JOIN latest_selfcert s USING (customer_id)
    WHERE c.customer_id IS NOT NULL
)
SELECT 'R05', 'customer_master', customer_id, customer_id, NULL, 'us_person',
       'US indicia found (' || array_to_string(found, ', ') || ') but '
       || CASE WHEN selfcert_id IS NULL THEN 'no self-certification is on file'
               WHEN us_person IS NULL THEN 'the US person declaration is unreadable'
               ELSE 'the US person declaration is ' || us_person END,
       NULL, NULL
FROM indicia
WHERE len(found) > 0 AND coalesce(us_person, '') <> 'Y';
```

## R06 Orphan accounts

**What it checks.** Flags an account whose customer_id (after trimming and uppercasing) is not in the customer master.

**Why it matters.** Data quality and completeness. An account without an owner cannot be assessed or reported.

**Simplifications.** No fuzzy matching of customer IDs is attempted.

**SQL** (from `src/rules.sql`):

```sql
-- R06 orphan accounts.
INSERT INTO rule_flags
SELECT 'R06', 'accounts', a.account_id, a.customer_id, a.customer_id, 'customer_id',
       'Account customer_id ' || coalesce(a.customer_id, '(unreadable)')
       || ' is not in the customer master',
       NULL, NULL
FROM stg_account a
WHERE a.customer_id IS NULL
   OR a.customer_id NOT IN (SELECT customer_id FROM stg_customer WHERE customer_id IS NOT NULL);
```

## R07 Customers with no accounts

**What it checks.** Flags a customer who has no account in the account extract.

**Why it matters.** Data quality and completeness. Usually harmless, but can point to a missing or incomplete account feed.

**Simplifications.** None.

**SQL** (from `src/rules.sql`):

```sql
-- R07 customers with no accounts.
INSERT INTO rule_flags
SELECT 'R07', 'customer_master', c.customer_id, c.customer_id, NULL, 'customer_id',
       'Customer has no accounts in the account extract', NULL, NULL
FROM stg_customer c
WHERE c.customer_id IS NOT NULL
  AND c.customer_id NOT IN (SELECT customer_id FROM stg_account WHERE customer_id IS NOT NULL);
```

## R08 Missing or out-of-date self-certification

**What it checks.** Flags a customer with no self-certification (High), or whose latest self-certification has no readable signature date, is dated after the reporting date, or carries an impossible or placeholder date: before the customer's date of birth, or 1900-01-01 or earlier (Medium).

**Why it matters.** A self-certification must be obtained for new accounts ([CRS] Section IV A; [IGA] Annex I, Section III B), and whether a valid one was provided is a reported item ([CRS] Section I A(1)(a)).

**Simplifications.** "Out of date" here means undated, future dated or a placeholder date only. No age limit is applied, because none was found in the sources checked. Preexisting accounts can be documented in other ways, which this model does not cover.

**SQL** (from `src/rules.sql`):

```sql
-- R08 missing or out-of-date self-certification. "Out of date" here means
-- undated, dated after the reporting date, or dated on an impossible or
-- placeholder date (before the customer's date of birth, or 1900-01-01 or
-- earlier). No age limit is applied.
INSERT INTO rule_flags
SELECT 'R08',
       CASE WHEN s.selfcert_id IS NULL THEN 'customer_master' ELSE 'self_certification' END,
       coalesce(s.selfcert_id, c.customer_id), c.customer_id, NULL,
       CASE WHEN s.selfcert_id IS NULL THEN 'customer_id' ELSE 'date_signed' END,
       CASE WHEN s.selfcert_id IS NULL THEN 'No self-certification on file'
            WHEN s.date_signed IS NULL THEN 'Self-certification has no readable signature date'
            WHEN s.date_signed > p.value
                THEN 'Self-certification dated ' || s.date_signed || ', after the reporting date '
                     || p.value
            ELSE 'Self-certification dated ' || s.date_signed
                 || ', a placeholder or a date before the customer was born' END,
       CASE WHEN s.selfcert_id IS NULL THEN NULL ELSE 'Medium' END,
       CASE WHEN s.selfcert_id IS NULL THEN NULL
            ELSE 'A self-certification is on file but its date cannot be relied on' END
FROM stg_customer c
LEFT JOIN latest_selfcert s USING (customer_id)
CROSS JOIN (SELECT value FROM ref_parameter WHERE name = 'reporting_date') p
WHERE c.customer_id IS NOT NULL
  AND (s.selfcert_id IS NULL OR s.date_signed IS NULL OR s.date_signed > p.value
       OR s.date_signed <= DATE '1900-01-01' OR s.date_signed < c.date_of_birth);
```

## R09 Format or standardisation failures

**What it checks.** Flags each value that the transformation could not standardise (an unreadable date, country, currency, flag or ID), or a required value that is blank. These come from `exc_transform`. Severity is Low for descriptive fields and Medium for fields that drive the checks.

**Why it matters.** Unreadable values leave other checks incomplete. Currency is required to report amounts ([CRS] Section I B).

**Simplifications.** Staging only knows the formats listed in docs/MAPPING.md. A valid value in an unknown layout is raised here rather than guessed.

**SQL** (from `src/rules.sql`):

```sql
-- R09 format failures that could not be fixed automatically (field level
-- exceptions raised by transform.sql).
INSERT INTO rule_flags
SELECT 'R09', e.source_system, e.record_key, e.customer_id, NULL, e.field,
       e.field || ' value ''' || coalesce(e.raw_value, '') || ''': '
       || CASE e.exception_type WHEN 'MISSING_REQUIRED' THEN 'required value is blank'
                                ELSE 'could not be standardised' END,
       CASE WHEN e.field IN ('date_of_birth', 'open_date', 'account_type', 'nationality',
                             'residence_address', 'mailing_address', 'place_of_birth')
            THEN 'Low' END,
       CASE WHEN e.field IN ('date_of_birth', 'open_date', 'account_type', 'nationality',
                             'residence_address', 'mailing_address', 'place_of_birth')
            THEN 'The field is descriptive and is not used to decide residency or US status' END
FROM exc_transform e
WHERE e.record_in_target_scope;
```

## Results

### Main dataset (2,000 customers, 3,000 accounts)

| Rule | Planted | Caught | Missed | Flags raised | False positives |
|---|---|---|---|---|---|
| R01 | 40 | 40 | 0 | 40 | 0 |
| R02 | 40 | 40 | 0 | 40 | 0 |
| R03 | 40 | 40 | 0 | 40 | 0 |
| R04 | 50 | 50 | 0 | 50 | 0 |
| R05 | 40 | 40 | 0 | 40 | 0 |
| R06 | 30 | 30 | 0 | 30 | 0 |
| R07 | 40 | 40 | 0 | 40 | 0 |
| R08 | 60 | 60 | 0 | 60 | 0 |
| R09 | 32 | 32 | 0 | 32 | 0 |
| **Total** | **372** | **372** | **0** | **372** | **0** |

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

| Rule | Planted | Caught | Missed | Flags raised | False positives |
|---|---|---|---|---|---|
| R01 | 5 | 2 | 3 | 3 | 1 |
| R02 | 4 | 0 | 4 | 0 | 0 |
| R03 | 3 | 3 | 0 | 7 | 4 |
| R04 | 2 | 2 | 0 | 2 | 0 |
| R05 | 5 | 2 | 3 | 2 | 0 |
| R06 | 2 | 2 | 0 | 2 | 0 |
| R07 | 1 | 1 | 0 | 1 | 0 |
| R08 | 3 | 2 | 1 | 2 | 0 |
| R09 | 3 | 3 | 0 | 11 | 8 |
| **Total** | **28** | **17** | **11** | **30** | **13** |

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

| Rule | Planted | Caught | Missed | Flags raised | False positives |
|---|---|---|---|---|---|
| R01 | 5 | 4 | 1 | 5 | 1 |
| R02 | 4 | 4 | 0 | 4 | 0 |
| R03 | 3 | 3 | 0 | 3 | 0 |
| R04 | 2 | 2 | 0 | 2 | 0 |
| R05 | 5 | 4 | 1 | 4 | 0 |
| R06 | 2 | 2 | 0 | 2 | 0 |
| R07 | 1 | 1 | 0 | 1 | 0 |
| R08 | 3 | 3 | 0 | 3 | 0 |
| R09 | 3 | 3 | 0 | 10 | 7 |
| **Total** | **28** | **26** | **2** | **34** | **8** |

### Round 2: current rules (fair test)

Measured on 38 customers and 39 accounts with seed 7777. Written before any
round 2 result was seen; the rules were not changed afterwards.

| Rule | Planted | Caught | Missed | Flags raised | False positives |
|---|---|---|---|---|---|
| R01 | 5 | 3 | 2 | 3 | 0 |
| R02 | 3 | 2 | 1 | 2 | 0 |
| R03 | 3 | 3 | 0 | 4 | 1 |
| R04 | 2 | 1 | 1 | 1 | 0 |
| R05 | 5 | 1 | 4 | 1 | 0 |
| R06 | 2 | 2 | 0 | 2 | 0 |
| R07 | 1 | 1 | 0 | 1 | 0 |
| R08 | 3 | 3 | 0 | 3 | 0 |
| R09 | 4 | 3 | 1 | 10 | 7 |
| **Total** | **28** | **19** | **9** | **27** | **8** |

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
