-- transform.sql: staging, target and transformation exceptions (DuckDB)
--
-- Inputs, created by src/transform.py before this script runs:
--   src_customer_master, src_accounts, src_self_certifications
--       raw extracts, every column VARCHAR, plus src_row (file row number)
--   ref_country_alias(alias, iso2)      country spellings to ISO 3166 alpha-2
--   ref_currency_alias(alias, code)     currency spellings to ISO 4217
--   ref_tin_placeholder(value)          TIN values meaning "not provided"
--   ref_account_type(alias, value)      account type spellings
--
-- Outputs:
--   stg_customer, stg_account, stg_selfcert   one row per source row, cleansed and typed
--   stg_selfcert_residency                    one row per declared tax residency
--   tgt_reportable_account                    one row per account linked to a known customer
--   exc_transform                             every value or record that could not be
--                                             standardised or placed in the target
--
-- Rule of the house: no source row is dropped. A value that cannot be
-- standardised becomes NULL in staging and is written to exc_transform. A
-- record that cannot go into the target is written to exc_transform too.

-- ------------------------------------------------------------------ macros
CREATE OR REPLACE MACRO clean_text(x) AS
    nullif(trim(regexp_replace(x, '\s+', ' ', 'g')), '');

CREATE OR REPLACE MACRO lookup_key(x) AS upper(clean_text(x));

-- Accepted date layouts. dd/mm/yyyy is read the Australian way, never mm/dd.
CREATE OR REPLACE MACRO parse_date(x) AS
    try_strptime(clean_text(x), ['%Y-%m-%d', '%d/%m/%Y', '%d-%b-%Y', '%Y%m%d'])::DATE;

CREATE OR REPLACE MACRO to_iso2(x) AS
    (SELECT r.iso2 FROM ref_country_alias r WHERE r.alias = lookup_key(x));

-- Text after the last comma, used for the country at the end of an address.
CREATE OR REPLACE MACRO last_part(x) AS clean_text(regexp_extract(x, '[^,]*$'));

CREATE OR REPLACE MACRO tin_compact(x) AS
    nullif(upper(regexp_replace(coalesce(x, ''), '\s+', '', 'g')), '');

CREATE OR REPLACE MACRO tin_status(x) AS
    CASE
        WHEN tin_compact(x) IS NULL THEN 'MISSING'
        -- A known placeholder, or any value with no digits at all (TBA, n.a., XXXX)
        WHEN tin_compact(x) IN (SELECT value FROM ref_tin_placeholder)
          OR NOT regexp_matches(tin_compact(x), '[0-9]') THEN 'PLACEHOLDER'
        ELSE 'PROVIDED'
    END;

CREATE OR REPLACE MACRO clean_tin(x) AS
    CASE WHEN tin_status(x) = 'PROVIDED' THEN tin_compact(x) END;

CREATE OR REPLACE MACRO yes_no(x) AS
    CASE lookup_key(x)
        WHEN 'Y' THEN 'Y' WHEN 'YES' THEN 'Y'
        WHEN 'N' THEN 'N' WHEN 'NO' THEN 'N'
    END;

-- ----------------------------------------------------------------- staging
CREATE OR REPLACE TABLE stg_customer AS
SELECT
    src_row,
    upper(clean_text(customer_id))                         AS customer_id,
    clean_text(full_name)                                  AS full_name,
    upper(strip_accents(clean_text(full_name)))            AS name_normalised,
    parse_date(date_of_birth)                              AS date_of_birth,
    clean_text(residence_address)                          AS residence_address,
    to_iso2(last_part(residence_address))                  AS residence_address_country,
    clean_text(mailing_address)                            AS mailing_address,
    to_iso2(last_part(mailing_address))                    AS mailing_address_country,
    to_iso2(country_of_residence)                          AS country_of_residence,
    clean_text(phone_numbers)                              AS phone_numbers,
    array_to_string(list_filter(
        list_transform(string_split(coalesce(phone_numbers, ''), ';'),
                       lambda p: regexp_extract(trim(p), '^(?:\+|00)([0-9]{1,3})(?:[^0-9]|$)', 1)),
        lambda c: c <> ''), ';')                                AS phone_country_codes,
    clean_text(place_of_birth)                             AS place_of_birth,
    to_iso2(last_part(place_of_birth))                     AS birth_country,
    to_iso2(nationality)                                   AS nationality
FROM src_customer_master;

CREATE OR REPLACE TABLE stg_account AS
SELECT
    src_row,
    upper(clean_text(account_id))                                       AS account_id,
    upper(clean_text(customer_id))                                      AS customer_id,
    (SELECT t.value FROM ref_account_type t
      WHERE t.alias = lookup_key(account_type))                         AS account_type,
    parse_date(open_date)                                               AS open_date,
    try_cast(replace(clean_text(balance_ye), ',', '') AS DECIMAL(18, 2)) AS balance_ye,
    (SELECT c.code FROM ref_currency_alias c
      WHERE c.alias = lookup_key(currency))                             AS currency,
    CASE lookup_key(status) WHEN 'OPEN' THEN 'Open'
                            WHEN 'CLOSED' THEN 'Closed' END             AS status
FROM src_accounts;

CREATE OR REPLACE TABLE stg_selfcert AS
SELECT
    src_row,
    upper(clean_text(selfcert_id))   AS selfcert_id,
    upper(clean_text(customer_id))   AS customer_id,
    to_iso2(tax_residency_1)         AS tax_residency_1,
    clean_tin(tin_1)                 AS tin_1,
    tin_status(tin_1)                AS tin_1_status,
    to_iso2(tax_residency_2)         AS tax_residency_2,
    clean_tin(tin_2)                 AS tin_2,
    tin_status(tin_2)                AS tin_2_status,
    to_iso2(tax_residency_3)         AS tax_residency_3,
    clean_tin(tin_3)                 AS tin_3,
    tin_status(tin_3)                AS tin_3_status,
    yes_no(us_person)                AS us_person,
    parse_date(date_signed)          AS date_signed
FROM src_self_certifications;

-- Long form: one row per declared residency slot that has any content.
CREATE OR REPLACE TABLE stg_selfcert_residency AS
WITH slots AS (
    SELECT t.src_row, t.selfcert_id, t.customer_id, 1 AS slot,
           s.tax_residency_1 AS country_raw, t.tax_residency_1 AS country,
           s.tin_1 AS tin_raw, t.tin_1 AS tin, t.tin_1_status AS tin_status
    FROM stg_selfcert t JOIN src_self_certifications s USING (src_row)
    UNION ALL
    SELECT t.src_row, t.selfcert_id, t.customer_id, 2,
           s.tax_residency_2, t.tax_residency_2, s.tin_2, t.tin_2, t.tin_2_status
    FROM stg_selfcert t JOIN src_self_certifications s USING (src_row)
    UNION ALL
    SELECT t.src_row, t.selfcert_id, t.customer_id, 3,
           s.tax_residency_3, t.tax_residency_3, s.tin_3, t.tin_3, t.tin_3_status
    FROM stg_selfcert t JOIN src_self_certifications s USING (src_row)
)
SELECT * FROM slots
WHERE clean_text(country_raw) IS NOT NULL OR clean_text(tin_raw) IS NOT NULL;

-- ------------------------------------------------------------------ target
CREATE OR REPLACE TABLE tgt_reportable_account AS
WITH cust AS (
    SELECT * FROM stg_customer
    WHERE customer_id IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY customer_id ORDER BY src_row) = 1
),
acct AS (
    -- An account with an unreadable account_id is kept (raised in exc_transform).
    SELECT * FROM stg_account
    QUALIFY account_id IS NULL
         OR row_number() OVER (PARTITION BY account_id ORDER BY src_row) = 1
),
cert AS (
    SELECT * FROM stg_selfcert
    WHERE customer_id IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY customer_id
                               ORDER BY date_signed DESC NULLS LAST, src_row DESC) = 1
),
residencies AS (
    SELECT customer_id,
           string_agg(country, ';' ORDER BY slot) AS declared_tax_residencies,
           bool_or(country IS NOT NULL AND country <> 'AU') AS has_foreign_tax_residency
    FROM stg_selfcert_residency r
    WHERE selfcert_id IN (SELECT selfcert_id FROM cert)
    GROUP BY customer_id
)
SELECT
    a.account_id, a.customer_id, a.account_type, a.open_date, a.balance_ye,
    a.currency, a.status,
    c.full_name, c.date_of_birth, c.country_of_residence,
    c.residence_address, c.residence_address_country,
    c.mailing_address, c.mailing_address_country,
    c.phone_numbers, c.phone_country_codes,
    c.place_of_birth, c.birth_country, c.nationality,
    s.selfcert_id,
    s.tax_residency_1, s.tin_1, s.tax_residency_2, s.tin_2,
    s.tax_residency_3, s.tin_3,
    s.us_person, s.date_signed,
    s.selfcert_id IS NOT NULL                     AS has_selfcert,
    r.declared_tax_residencies,
    coalesce(r.has_foreign_tax_residency, FALSE)  AS has_foreign_tax_residency
FROM acct a
JOIN cust c ON c.customer_id = a.customer_id
LEFT JOIN cert s ON s.customer_id = a.customer_id
LEFT JOIN residencies r ON r.customer_id = a.customer_id
ORDER BY a.account_id;

-- -------------------------------------------------- transformation exceptions
CREATE OR REPLACE TABLE exc_transform AS
WITH field_checks AS (
    -- customer master: (raw value, cleaned value is unusable, required?)
    SELECT 'customer_master' AS source_system, s.src_row,
           coalesce(t.customer_id, clean_text(s.customer_id)) AS record_key,
           t.customer_id, f.field, f.raw_value, f.failed, f.required
    FROM src_customer_master s JOIN stg_customer t USING (src_row),
    LATERAL (VALUES
        ('customer_id', s.customer_id,
            NOT coalesce(regexp_full_match(t.customer_id, 'C[0-9]{6}'), FALSE), TRUE),
        ('full_name', s.full_name, t.full_name IS NULL, TRUE),
        ('date_of_birth', s.date_of_birth, t.date_of_birth IS NULL, TRUE),
        ('country_of_residence', s.country_of_residence, t.country_of_residence IS NULL, TRUE),
        ('residence_address', s.residence_address, t.residence_address_country IS NULL, FALSE),
        ('mailing_address', s.mailing_address, t.mailing_address_country IS NULL, FALSE),
        ('place_of_birth', s.place_of_birth, t.birth_country IS NULL, FALSE),
        ('nationality', s.nationality, t.nationality IS NULL, FALSE)
    ) AS f(field, raw_value, failed, required)

    UNION ALL
    SELECT 'accounts', s.src_row,
           coalesce(t.account_id, clean_text(s.account_id)),
           t.customer_id, f.field, f.raw_value, f.failed, f.required
    FROM src_accounts s JOIN stg_account t USING (src_row),
    LATERAL (VALUES
        ('account_id', s.account_id,
            NOT coalesce(regexp_full_match(t.account_id, 'A[0-9]{7}'), FALSE), TRUE),
        ('customer_id', s.customer_id,
            NOT coalesce(regexp_full_match(t.customer_id, 'C[0-9]{6}'), FALSE), TRUE),
        ('account_type', s.account_type, t.account_type IS NULL, TRUE),
        ('open_date', s.open_date, t.open_date IS NULL, TRUE),
        ('balance_ye', s.balance_ye, t.balance_ye IS NULL, TRUE),
        ('currency', s.currency, t.currency IS NULL, TRUE),
        ('status', s.status, t.status IS NULL, TRUE)
    ) AS f(field, raw_value, failed, required)

    UNION ALL
    SELECT 'self_certification', s.src_row,
           coalesce(t.selfcert_id, clean_text(s.selfcert_id)),
           t.customer_id, f.field, f.raw_value, f.failed, f.required
    FROM src_self_certifications s JOIN stg_selfcert t USING (src_row),
    LATERAL (VALUES
        ('customer_id', s.customer_id,
            NOT coalesce(regexp_full_match(t.customer_id, 'C[0-9]{6}'), FALSE), TRUE),
        ('tax_residency_1', s.tax_residency_1, t.tax_residency_1 IS NULL, TRUE),
        ('tax_residency_2', s.tax_residency_2, t.tax_residency_2 IS NULL, FALSE),
        ('tax_residency_3', s.tax_residency_3, t.tax_residency_3 IS NULL, FALSE),
        ('us_person', s.us_person, t.us_person IS NULL, TRUE),
        -- A missing signature date is a self-certification check (R08), so
        -- only a date that is present but unreadable is raised here.
        ('date_signed', s.date_signed, t.date_signed IS NULL, FALSE)
    ) AS f(field, raw_value, failed, required)
),
field_exceptions AS (
    SELECT source_system, src_row, record_key, customer_id, field, raw_value,
           CASE WHEN clean_text(raw_value) IS NULL THEN 'MISSING_REQUIRED'
                ELSE 'UNPARSEABLE' END AS exception_type,
           TRUE AS record_in_target_scope,
           CASE WHEN clean_text(raw_value) IS NULL
                THEN 'Required value is blank'
                ELSE 'Value could not be standardised; set to NULL in staging' END AS detail
    FROM field_checks
    WHERE failed AND (required OR clean_text(raw_value) IS NOT NULL)
),
record_exceptions AS (
    SELECT 'accounts' AS source_system, a.src_row, a.account_id AS record_key,
           a.customer_id, 'customer_id' AS field, s.customer_id AS raw_value,
           'ORPHAN_ACCOUNT' AS exception_type, FALSE AS record_in_target_scope,
           'Account customer_id not found in the customer master; account held out of the target' AS detail
    FROM stg_account a JOIN src_accounts s USING (src_row)
    WHERE a.customer_id IS NULL
       OR a.customer_id NOT IN (SELECT customer_id FROM stg_customer WHERE customer_id IS NOT NULL)

    UNION ALL
    SELECT 'customer_master', c.src_row, c.customer_id, c.customer_id, 'customer_id',
           s.customer_id, 'CUSTOMER_NO_ACCOUNT', FALSE,
           'Customer has no account in the account system, so has no target row'
    FROM stg_customer c JOIN src_customer_master s USING (src_row)
    WHERE c.customer_id IS NOT NULL
      AND c.customer_id NOT IN (SELECT customer_id FROM stg_account WHERE customer_id IS NOT NULL)

    UNION ALL
    SELECT 'self_certification', t.src_row, t.selfcert_id, t.customer_id, 'customer_id',
           s.customer_id, 'SELFCERT_NO_ACCOUNT', FALSE,
           'Customer exists but has no account, so the self-certification has no target row'
    FROM stg_selfcert t JOIN src_self_certifications s USING (src_row)
    WHERE t.customer_id IN (SELECT customer_id FROM stg_customer)
      AND t.customer_id NOT IN (SELECT customer_id FROM stg_account WHERE customer_id IS NOT NULL)

    UNION ALL
    SELECT 'self_certification', t.src_row, t.selfcert_id, t.customer_id, 'customer_id',
           s.customer_id, 'SELFCERT_NO_CUSTOMER', FALSE,
           'Self-certification customer_id not found in the customer master'
    FROM stg_selfcert t JOIN src_self_certifications s USING (src_row)
    WHERE t.customer_id IS NULL
       OR t.customer_id NOT IN (SELECT customer_id FROM stg_customer WHERE customer_id IS NOT NULL)

    UNION ALL
    SELECT 'customer_master', src_row, customer_id, customer_id, 'customer_id', customer_id,
           'DUPLICATE_KEY', FALSE, 'customer_id repeated in the extract; first row kept'
    FROM stg_customer WHERE customer_id IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY customer_id ORDER BY src_row) > 1

    UNION ALL
    SELECT 'accounts', src_row, account_id, customer_id, 'account_id', account_id,
           'DUPLICATE_KEY', FALSE, 'account_id repeated in the extract; first row kept'
    FROM stg_account WHERE account_id IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY account_id ORDER BY src_row) > 1

    UNION ALL
    SELECT 'self_certification', src_row, selfcert_id, customer_id, 'customer_id', customer_id,
           'SUPERSEDED_SELFCERT', FALSE,
           'Customer has more than one self-certification; latest signed one used'
    FROM stg_selfcert WHERE customer_id IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY customer_id
                               ORDER BY date_signed DESC NULLS LAST, src_row DESC) > 1
)
SELECT * FROM field_exceptions
UNION ALL
SELECT * FROM record_exceptions
ORDER BY source_system, record_key, field;
