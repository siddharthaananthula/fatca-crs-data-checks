-- rules.sql: checks R01 to R09, reconciliation and the exception register (DuckDB)
--
-- Runs after transform.sql. Plain-English versions of every rule, with
-- sources, are in docs/RULES.md. This is a simplified educational model, not
-- a complete implementation of FATCA or CRS.

-- --------------------------------------------------------------- catalogue
CREATE OR REPLACE TABLE rule_catalogue (
    rule_id VARCHAR PRIMARY KEY,
    rule_name VARCHAR,
    severity VARCHAR,
    severity_reason VARCHAR,
    recommended_action VARCHAR
);
INSERT INTO rule_catalogue VALUES
('R01', 'Duplicate customer candidate', 'Medium',
 'Two customer IDs for one person can split or double report the same accounts',
 'Compare the two records; if they are the same person, merge them in the customer master and re-run the checks'),
('R02', 'Missing TIN for a declared tax residency', 'High',
 'The TIN is a required reporting element for each residency unless an exception applies',
 'Request the TIN from the customer, or record why no TIN is available (for example the jurisdiction does not issue one)'),
('R03', 'TIN format invalid for its country', 'Medium',
 'A TIN was given but does not fit the expected format, so it is likely a keying error',
 'Check the TIN against the signed form; correct the keying or ask the customer to confirm it'),
('R04', 'Residence country does not match self-certified tax residency', 'High',
 'The self-certification may be unreliable, so it cannot be relied on until resolved',
 'Ask the customer to confirm residence; obtain a new self-certification or record a reasonable explanation'),
('R05', 'US indicia with no US person declaration', 'High',
 'US indicia without curing documentation can make the account US reportable',
 'Obtain the curing documents for each indicium (self-certification plus documentary evidence), or treat the account as US reportable'),
('R06', 'Orphan account', 'High',
 'The account cannot be attributed to a customer, so it cannot be assessed or reported',
 'Find the owning customer in the source system and correct the customer_id link'),
('R07', 'Customer with no accounts', 'Low',
 'No account means nothing to report, but the record may point to a missing account feed',
 'Confirm the customer has no accounts; if accounts exist, investigate the account extract'),
('R08', 'Missing or out-of-date self-certification', 'High',
 'A self-certification is needed to determine tax residency',
 'Obtain a valid, signed and dated self-certification from the customer'),
('R09', 'Format or standardisation failure', 'Medium',
 'The value could not be standardised, so checks that use it are incomplete',
 'Correct the value in the source system; the transformation will pick it up on the next run');

-- Latest self-certification per customer (same choice as the target table).
CREATE OR REPLACE VIEW latest_selfcert AS
SELECT * FROM stg_selfcert
WHERE customer_id IS NOT NULL
QUALIFY row_number() OVER (PARTITION BY customer_id
                           ORDER BY date_signed DESC NULLS LAST, src_row DESC) = 1;

CREATE OR REPLACE VIEW latest_residency AS
SELECT r.* FROM stg_selfcert_residency r
WHERE r.selfcert_id IN (SELECT selfcert_id FROM latest_selfcert);

-- ------------------------------------------------------------------- flags
CREATE OR REPLACE TABLE rule_flags (
    rule_id VARCHAR,
    source_system VARCHAR,
    record_key VARCHAR,
    customer_id VARCHAR,
    related_key VARCHAR,
    field VARCHAR,
    description VARCHAR,
    severity VARCHAR,          -- set only when it differs from the catalogue
    severity_reason VARCHAR
);

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

-- R06 orphan accounts.
INSERT INTO rule_flags
SELECT 'R06', 'accounts', a.account_id, a.customer_id, a.customer_id, 'customer_id',
       'Account customer_id ' || coalesce(a.customer_id, '(unreadable)')
       || ' is not in the customer master',
       NULL, NULL
FROM stg_account a
WHERE a.customer_id IS NULL
   OR a.customer_id NOT IN (SELECT customer_id FROM stg_customer WHERE customer_id IS NOT NULL);

-- R07 customers with no accounts.
INSERT INTO rule_flags
SELECT 'R07', 'customer_master', c.customer_id, c.customer_id, NULL, 'customer_id',
       'Customer has no accounts in the account extract', NULL, NULL
FROM stg_customer c
WHERE c.customer_id IS NOT NULL
  AND c.customer_id NOT IN (SELECT customer_id FROM stg_account WHERE customer_id IS NOT NULL);

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

-- -------------------------------------------------------- exception register
CREATE OR REPLACE TABLE exception_register AS
SELECT
    printf('E%05d', row_number() OVER (ORDER BY f.rule_id, f.source_system, f.record_key, f.field))
                                                   AS exception_id,
    f.rule_id,
    k.rule_name,
    coalesce(f.severity, k.severity)               AS severity,
    coalesce(f.severity_reason, k.severity_reason) AS severity_reason,
    f.source_system,
    f.record_key,
    f.customer_id,
    f.related_key,
    f.field,
    coalesce(c.country_of_residence, 'Unknown')    AS country,
    f.description,
    k.recommended_action
FROM rule_flags f
JOIN rule_catalogue k USING (rule_id)
LEFT JOIN (SELECT customer_id, any_value(country_of_residence) AS country_of_residence
           FROM stg_customer GROUP BY customer_id) c
       ON c.customer_id = f.customer_id
ORDER BY exception_id;

-- ------------------------------------------------------------ reconciliation
CREATE OR REPLACE TABLE recon_counts AS
WITH held AS (
    SELECT source_system, count(DISTINCT src_row) AS n
    FROM exc_transform WHERE NOT record_in_target_scope GROUP BY 1
),
counts AS (
    SELECT 1 AS ord, 'accounts' AS source_system,
           (SELECT count(*) FROM src_accounts) AS source_rows,
           (SELECT count(*) FROM stg_account) AS staging_rows,
           (SELECT count(*) FROM tgt_reportable_account) AS target_rows
    UNION ALL
    SELECT 2, 'customer_master',
           (SELECT count(*) FROM src_customer_master),
           (SELECT count(*) FROM stg_customer),
           (SELECT count(DISTINCT customer_id) FROM tgt_reportable_account)
    UNION ALL
    SELECT 3, 'self_certification',
           (SELECT count(*) FROM src_self_certifications),
           (SELECT count(*) FROM stg_selfcert),
           (SELECT count(DISTINCT selfcert_id) FROM tgt_reportable_account)
)
SELECT c.source_system, c.source_rows, c.staging_rows, c.target_rows,
       coalesce(h.n, 0) AS held_out_rows,
       c.source_rows - c.target_rows - coalesce(h.n, 0) AS difference,
       CASE WHEN c.source_rows = c.staging_rows
             AND c.source_rows - c.target_rows - coalesce(h.n, 0) = 0
            THEN 'OK' ELSE 'BREAK' END AS result
FROM counts c LEFT JOIN held h USING (source_system)
ORDER BY c.ord;

-- Control totals: year-end balances by standardised currency. Accounts whose
-- currency could not be standardised are shown as UNMAPPED.
CREATE OR REPLACE TABLE recon_control_totals AS
WITH staged AS (
    SELECT coalesce(currency, 'UNMAPPED') AS currency, count(*) AS n, sum(balance_ye) AS total
    FROM stg_account GROUP BY 1
),
target AS (
    SELECT coalesce(currency, 'UNMAPPED') AS currency, count(*) AS n, sum(balance_ye) AS total
    FROM tgt_reportable_account GROUP BY 1
),
held AS (
    SELECT coalesce(a.currency, 'UNMAPPED') AS currency, count(*) AS n, sum(a.balance_ye) AS total
    FROM stg_account a
    WHERE a.src_row IN (SELECT src_row FROM exc_transform
                        WHERE source_system = 'accounts' AND NOT record_in_target_scope)
    GROUP BY 1
)
SELECT s.currency,
       s.n AS source_accounts, coalesce(t.n, 0) AS target_accounts, coalesce(h.n, 0) AS held_out_accounts,
       s.total AS source_balance, coalesce(t.total, 0) AS target_balance,
       coalesce(h.total, 0) AS held_out_balance,
       s.total - coalesce(t.total, 0) - coalesce(h.total, 0) AS difference,
       CASE WHEN s.total - coalesce(t.total, 0) - coalesce(h.total, 0) = 0
             AND s.n = coalesce(t.n, 0) + coalesce(h.n, 0) THEN 'OK' ELSE 'BREAK' END AS result
FROM staged s
LEFT JOIN target t USING (currency)
LEFT JOIN held h USING (currency)
ORDER BY s.currency = 'UNMAPPED', s.currency;
