"""Staging and target behaviour on small hand-made extracts."""

from datetime import date
from decimal import Decimal

import pytest

from transform import build

CUSTOMERS = """customer_id,full_name,date_of_birth,residence_address,mailing_address,country_of_residence,phone_numbers,place_of_birth,nationality
C000001,  Inès  Martin ,05/03/1984,"1 A St, Sydney, Australia","1 A St, Sydney, Australia",australia ,+61 400 000 001,"Paris, France",FR
C000002,Bob Lee,31/02/1985,"2 B St, Austin, TX 73301, USA","PO Box 1, Perth, Australia",Ausralia,+61 400 000 002; +1 (512) 555-0101,"Perth, Australia",AU
C000003,Cara Ng,1990-01-01,"3 C St, Auckland, New Zealand","3 C St, Auckland, New Zealand",NZL,+64 21 000 0003,"Auckland, New Zealand",NZ
"""

ACCOUNTS = """account_id,customer_id,account_type,open_date,balance_ye,currency,status
A0000001,c000001 ,savings,5-Mar-2015,"12,345.60",A$,OPEN
A0000002,C000002,Transaction,2019-02-30,10.00,$,closed
A0000003,C000099,Savings,20200101,5.00,USD,Open
"""

SELFCERTS = """selfcert_id,customer_id,tax_residency_1,tin_1,tax_residency_2,tin_2,tax_residency_3,tin_3,us_person,date_signed
SC000001,C000001,Australia,au-fake-123 456 789,France,N/A,,,yes,01/07/2020
SC000002,C000002,AU,AU-FAKE-987654321,,,,,U,
SC000003,C000003,NZ,NZ-FAKE-12345678,,,,,N,2021-01-01
"""


@pytest.fixture()
def con(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "customer_master.csv").write_text(CUSTOMERS, encoding="utf-8")
    (raw / "accounts.csv").write_text(ACCOUNTS, encoding="utf-8")
    (raw / "self_certifications.csv").write_text(SELFCERTS, encoding="utf-8")
    connection = build(None, raw)
    yield connection
    connection.close()


def one(con, sql):
    return con.execute(sql).fetchone()


def test_customer_fields_are_standardised(con):
    row = one(con, """SELECT full_name, name_normalised, date_of_birth, country_of_residence,
                             birth_country, phone_country_codes
                      FROM stg_customer WHERE customer_id = 'C000001'""")
    assert row == ("Inès Martin", "INES MARTIN", date(1984, 3, 5), "AU", "FR", "61")
    assert one(con, "SELECT country_of_residence FROM stg_customer WHERE customer_id = 'C000003'")[0] == "NZ"


def test_address_country_and_us_phone_code_are_derived(con):
    row = one(con, """SELECT residence_address_country, mailing_address_country, phone_country_codes
                      FROM stg_customer WHERE customer_id = 'C000002'""")
    assert row == ("US", "AU", "61;1")


def test_account_fields_are_standardised(con):
    row = one(con, """SELECT customer_id, account_type, open_date, balance_ye, currency, status
                      FROM stg_account WHERE account_id = 'A0000001'""")
    assert row == ("C000001", "Savings", date(2015, 3, 5), Decimal("12345.60"), "AUD", "Open")


def test_tins_are_compacted_and_placeholders_become_missing(con):
    rows = con.execute("""SELECT slot, country, tin, tin_status FROM stg_selfcert_residency
                          WHERE customer_id = 'C000001' ORDER BY slot""").fetchall()
    assert rows == [(1, "AU", "AU-FAKE-123456789", "PROVIDED"), (2, "FR", None, "PLACEHOLDER")]
    assert one(con, "SELECT us_person, date_signed FROM stg_selfcert WHERE customer_id = 'C000001'") \
        == ("Y", date(2020, 7, 1))


def test_unfixable_values_are_raised_not_dropped(con):
    raised = set(con.execute("""SELECT record_key, field, exception_type FROM exc_transform
                                WHERE record_in_target_scope""").fetchall())
    assert raised == {
        ("C000002", "date_of_birth", "UNPARSEABLE"),
        ("C000002", "country_of_residence", "UNPARSEABLE"),
        ("A0000002", "open_date", "UNPARSEABLE"),
        ("A0000002", "currency", "UNPARSEABLE"),
        ("SC000002", "us_person", "UNPARSEABLE"),
    }
    # the record is still in the target, with the failed value NULL
    assert one(con, "SELECT open_date, currency FROM tgt_reportable_account WHERE account_id = 'A0000002'") \
        == (None, None)


def test_every_record_is_accounted_for(con):
    held = dict(con.execute("""SELECT exception_type, count(*) FROM exc_transform
                               WHERE NOT record_in_target_scope GROUP BY 1""").fetchall())
    assert held == {"ORPHAN_ACCOUNT": 1, "CUSTOMER_NO_ACCOUNT": 1, "SELFCERT_NO_ACCOUNT": 1}
    assert one(con, "SELECT count(*) FROM tgt_reportable_account")[0] == 2
    assert one(con, """SELECT declared_tax_residencies, has_foreign_tax_residency, has_selfcert
                       FROM tgt_reportable_account WHERE account_id = 'A0000001'""") \
        == ("AU;FR", True, True)
