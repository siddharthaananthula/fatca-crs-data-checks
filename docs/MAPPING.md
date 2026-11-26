# Source-to-target mapping

This document maps the three fictional source extracts to the target table
`tgt_reportable_account` built by `src/transform.sql`. The same table is used
for the Mapping sheet in the analyst workbook.

## Sources

| Code | Source system | File | Grain |
|---|---|---|---|
| CM | Core banking customer master | `data/raw/customer_master.csv` | One row per customer |
| ACC | Account system | `data/raw/accounts.csv` | One row per account |
| SC | Onboarding self-certification | `data/raw/self_certifications.csv` | One row per self-certification, up to three tax residencies |

## Target grain and scope

`tgt_reportable_account` has one row per account that links to a known
customer, with that customer's details and latest self-certification joined
on.

Simplification: in this model every linked account is in scope for review,
open or closed. Deciding whether an account is legally reportable under
FATCA or CRS is out of scope. The target carries `has_foreign_tax_residency`
and `us_person` so that reviewers can filter, but these are not a
reportability decision.

## Standard transformations

| Name | What it does |
|---|---|
| Trim | Removes leading and trailing spaces and collapses repeated internal spaces. A value that is empty after trimming is treated as missing. |
| Uppercase | Converts to capitals before a lookup or a key comparison. |
| Date parse | Accepts `yyyy-mm-dd`, `dd/mm/yyyy`, `d-Mon-yyyy` and `yyyymmdd`. Slashed dates are always read day first (Australian convention), never month first. Impossible dates such as 31/02/1985 fail. |
| Country to ISO | Trim and uppercase, then look up ISO 3166 alpha-2. Accepts the alpha-2 code, the alpha-3 code, the English name, and the aliases USA, U.S., U.S.A., United States of America, UK and Great Britain. Misspellings are not guessed. |
| Currency to ISO | Trim and uppercase, then look up ISO 4217. Accepts the code and the symbols A$, AU$, NZ$, US$, £, € and ₹. A bare `$` is ambiguous and fails. |
| TIN compact | Removes all spaces and uppercases. N/A, NA, NONE, NIL, UNKNOWN, `-`, and any value with no digits at all (for example TBA), mean "not provided" and become missing. The TIN format itself is not checked here; that is rule R03 in Phase 3. |
| Address country | Takes the text after the last comma in the address and applies Country to ISO. |

## Field mapping

When a required value fails, it is set to NULL in staging and written to
`exc_transform` as `UNPARSEABLE` (value present but unusable) or
`MISSING_REQUIRED` (blank). The record itself is kept. When an optional value
fails, it is written as `UNPARSEABLE`; when it is blank, nothing is raised.

| Target field | Source | Source field | Transformation | If missing or invalid |
|---|---|---|---|---|
| account_id | ACC | account_id | Trim, uppercase; must match `A` + 7 digits | Exception (required); row still kept in the target |
| customer_id | ACC | customer_id | Trim, uppercase; must match `C` + 6 digits; join key to CM | If no CM customer matches, the account is held out of the target as `ORPHAN_ACCOUNT` |
| account_type | ACC | account_type | Trim, case-insensitive match to Transaction, Savings, Term Deposit, Investment | Exception (required) |
| open_date | ACC | open_date | Date parse | Exception (required) |
| balance_ye | ACC | balance_ye | Trim, remove thousands separators, cast to decimal(18,2) | Exception (required) |
| currency | ACC | currency | Currency to ISO | Exception (required) |
| status | ACC | status | Trim, uppercase, map OPEN/CLOSED to Open/Closed | Exception (required) |
| full_name | CM | full_name | Trim | Exception (required) |
| date_of_birth | CM | date_of_birth | Date parse | Exception (required) |
| country_of_residence | CM | country_of_residence | Country to ISO | Exception (required) |
| residence_address | CM | residence_address | Trim | Not checked |
| residence_address_country | CM | residence_address | Address country (derived) | Exception if the country cannot be read |
| mailing_address | CM | mailing_address | Trim | Not checked |
| mailing_address_country | CM | mailing_address | Address country (derived) | Exception if the country cannot be read |
| phone_numbers | CM | phone_numbers | Trim | Not checked |
| phone_country_codes | CM | phone_numbers | Split on `;`, take the 1 to 3 digit country code after a leading `+` or `00` of each number, join with `;` (derived) | Empty when no number starts with `+` or `00` |
| place_of_birth | CM | place_of_birth | Trim | Not checked |
| birth_country | CM | place_of_birth | Address country applied to "city, country" (derived) | Exception if the country cannot be read |
| nationality | CM | nationality | Country to ISO | Exception if present but unreadable |
| selfcert_id | SC | selfcert_id | Trim, uppercase | NULL when the customer has no self-certification |
| tax_residency_1 | SC | tax_residency_1 | Country to ISO | Exception (required) |
| tin_1 | SC | tin_1 | TIN compact | NULL when blank or a placeholder; tested by R02 and R03 |
| tax_residency_2 | SC | tax_residency_2 | Country to ISO | Exception if present but unreadable |
| tin_2 | SC | tin_2 | TIN compact | As tin_1 |
| tax_residency_3 | SC | tax_residency_3 | Country to ISO | Exception if present but unreadable |
| tin_3 | SC | tin_3 | TIN compact | As tin_1 |
| us_person | SC | us_person | Trim, uppercase, map Y/YES to Y and N/NO to N | Exception (required) |
| date_signed | SC | date_signed | Date parse | Blank is left to R08; present but unreadable is an exception |
| has_selfcert | SC | customer_id | TRUE when a self-certification joins on customer_id (derived) | FALSE; tested by R08 |
| declared_tax_residencies | SC | tax_residency_1 to 3 | Standardised countries joined with `;` (derived) | NULL when there is no self-certification |
| has_foreign_tax_residency | SC | tax_residency_1 to 3 | TRUE when any declared residency is not AU (derived) | FALSE when none are declared |

## Staging-only fields

These live in staging tables and are used by the Phase 3 checks.

| Table | Field | Transformation |
|---|---|---|
| stg_customer | name_normalised | Trim, remove accents, uppercase. Used for duplicate matching (R01). |
| stg_selfcert | tin_1_status to tin_3_status | MISSING, PLACEHOLDER or PROVIDED |
| stg_selfcert_residency | one row per residency slot | Long form of tax_residency and tin, with the raw values kept |

## Records that do not reach the target

No source row is dropped. Rows that cannot be placed in the target are
written to `exc_transform` with `record_in_target_scope = FALSE`.

| Exception type | Source | Meaning |
|---|---|---|
| ORPHAN_ACCOUNT | ACC | Account customer_id is not in the customer master |
| CUSTOMER_NO_ACCOUNT | CM | Customer has no account |
| SELFCERT_NO_ACCOUNT | SC | Self-certification belongs to a customer with no account |
| SELFCERT_NO_CUSTOMER | SC | Self-certification customer_id is not in the customer master |
| DUPLICATE_KEY | CM, ACC | Same key appears twice in one extract; the first row is used |
| SUPERSEDED_SELFCERT | SC | Customer has more than one self-certification; the latest signed is used |

## Assumptions

- The fictional institution is Australian, so "foreign" means not AU.
- When a customer has several self-certifications, the latest signed one is
  used. None occur in the generated data, but the rule is in place.
- A `+1` phone prefix is taken as a US phone in Phase 3. This is a
  simplification: +1 is shared by the US, Canada and other countries.
- A balance is read by removing commas as thousands separators. A European
  decimal comma (12.345,67) is therefore misread. This was found in the
  round 2 unseen-variant test and is listed in docs/RULES.md as a known risk.
