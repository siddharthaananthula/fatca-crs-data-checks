# FATCA and CRS data review: exception summary

**Fictional financial institution. Year ended 30 June 2026.**
Synthetic data in a simplified educational model. This is not tax advice.

## Scope

We reviewed the three data extracts used to prepare FATCA and CRS customer
reporting, to find records that are missing, duplicated or inconsistent
before reporting. We did not decide which accounts are legally reportable.

| Source system | Records received |
|---|---|
| Core banking customer master | 2,000 customers |
| Account system | 3,000 accounts, balances in 6 currencies |
| Onboarding self-certifications | 1,960 self-certifications |

## What we checked

Nine checks, each based on public OECD and FATCA agreement guidance or on
basic data quality, cover duplicates, tax identification numbers, residence,
US indicators, account links, self-certifications and data formats. Full
definitions are in docs/RULES.md.

## Results by check

| Check | What it finds | Exceptions | By severity |
|---|---|---|---|
| R01 | Possible duplicate customers (same person under two IDs) | 40 | Medium 40 |
| R02 | Missing tax identification number (TIN) | 40 | High 40 |
| R03 | TIN in the wrong format | 40 | Medium 40 |
| R04 | Country of residence differs from the declared tax residency | 50 | High 50 |
| R05 | US indicators with no US person declaration | 40 | High 40 |
| R06 | Accounts not linked to any customer | 30 | High 30 |
| R07 | Customers with no accounts | 40 | Low 40 |
| R08 | Missing or unusable self-certification | 60 | High 40, Medium 20 |
| R09 | Values that could not be standardised | 32 | Medium 21, Low 11 |
| | **Total** | **372** | High 200, Medium 121, Low 51 |

329 of 2,000 customers (16.4%) have at least one exception.

## Reconciliation

Every record is accounted for.
Of 3,000 accounts, 2,970 are in the reporting dataset and 30 are held
out; of 2,000 customers, 1,960 have accounts and 40 do not; of
1,960 self-certifications, 1,920 relate to reported accounts and 40 do not.
Year-end balances agree between the source and the
reporting dataset in every currency, after allowing for the held-out accounts.
6 accounts have a currency that could not be read, so their balances
(totalling 64,301.47) cannot be stated in a currency until corrected.

## Top 3 issues

1. **40 customers show US indicators but have not declared US
   status** (59 accounts). Unless curing documents are held,
   these accounts may need to be treated as US reportable.
2. **Self-certifications are incomplete.** 40 customers have no
   self-certification, 20 have one with an unusable date,
   40 declared residencies have no TIN and 40 TINs are
   in the wrong format. Only 92.6% of reported accounts (2,751 of
   2,970) have a complete self-certification.
3. **30 accounts (AUD 1,560,291.72) are not linked to
   any customer**, so they cannot be assessed or reported until the link is
   corrected.

## Recommended next steps

1. Request curing documents or US status declarations for the 40 US
   indicator cases, starting with the highest balances.
2. Contact customers for missing self-certifications and TINs; correct keying
   errors against the signed forms.
3. Correct the 30 account links in the account system and confirm
   the 40 customers with no accounts are expected.
4. Review the 50 residence mismatches and the 40 possible
   duplicates, then re-run the checks.
5. Assign an owner and status to each exception in the workbook register.

## Limitations

- Simplified model on synthetic data, not a complete implementation of FATCA
  or CRS. No balance thresholds and no documentary evidence are considered.
- On data it had not seen before, the checks caught 19 of 28 planted
  issues and raised 8 false alarms. Some issues will be missed.
- A balance written in European style (12.345,67) is misread without warning.
  Balances should be checked for this before reporting.
- Duplicate matching uses name and date of birth only, so twins are flagged
  and need a manual check.
