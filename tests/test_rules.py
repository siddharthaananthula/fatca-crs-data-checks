"""One focused test per rule (R01 to R09), plus reconciliation, on hand-made data.

Each fixture is a few rows written as CSV text. Every test checks both that the
rule fires on the bad record and that it stays quiet on the good ones.
"""

import pytest

from checks import run_rules
from transform import build

CM_HEAD = ("customer_id,full_name,date_of_birth,residence_address,mailing_address,"
           "country_of_residence,phone_numbers,place_of_birth,nationality\n")
AC_HEAD = "account_id,customer_id,account_type,open_date,balance_ye,currency,status\n"
SC_HEAD = ("selfcert_id,customer_id,tax_residency_1,tin_1,tax_residency_2,tin_2,"
           "tax_residency_3,tin_3,us_person,date_signed\n")

AU_ADDR = '"1 A St, Sydney, Australia"'


def cust(cid, name="Ann Lee", dob="1980-01-01", res=AU_ADDR, mail=AU_ADDR, country="AU",
         phone="+61 400 000 000", pob='"Perth, Australia"', nat="AU"):
    return f"{cid},{name},{dob},{res},{mail},{country},{phone},{pob},{nat}\n"


def acct(aid, cid, balance="100.00", currency="AUD"):
    return f"{aid},{cid},Savings,2020-01-01,{balance},{currency},Open\n"


def cert(sid, cid, c1="AU", t1="AU-FAKE-123456789", c2="", t2="", us="N", signed="2020-01-01"):
    return f"{sid},{cid},{c1},{t1},{c2},{t2},,,{us},{signed}\n"


def run(tmp_path, customers, accounts, certs):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "customer_master.csv").write_text(CM_HEAD + "".join(customers), encoding="utf-8")
    (raw / "accounts.csv").write_text(AC_HEAD + "".join(accounts), encoding="utf-8")
    (raw / "self_certifications.csv").write_text(SC_HEAD + "".join(certs), encoding="utf-8")
    con = build(None, raw)
    run_rules(con)
    return con


def flagged(con, rule):
    return sorted(con.execute(
        "SELECT customer_id, record_key, related_key, field FROM rule_flags WHERE rule_id = ?",
        [rule]).fetchall())


def simple(tmp_path, customers, certs=None):
    """Each customer gets one account and, unless given, a clean self-certification."""
    ids = [c.split(",")[0] for c in customers]
    accounts = [acct(f"A{n:07d}", cid) for n, cid in enumerate(ids, start=1)]
    if certs is None:
        certs = [cert(f"SC{n:06d}", cid) for n, cid in enumerate(ids, start=1)]
    return run(tmp_path, customers, accounts, certs)


def test_r01_exact_and_fuzzy_duplicates(tmp_path):
    con = simple(tmp_path, [
        cust("C000001", "José Smith"),
        cust("C000002", "JOSE  SMITH"),                       # exact after normalising
        cust("C000003", "Mary Jonson", dob="1975-05-05"),
        cust("C000004", "Mary Johnson", dob="05/05/1975"),     # fuzzy, other date layout
        cust("C000005", "Mary Johnson", dob="1976-05-05"),     # same name, other DOB
        cust("C000006", "Peter Brown", dob="1975-05-05"),      # same DOB, other name
    ])
    pairs = {frozenset((r[1], r[2])) for r in flagged(con, "R01")}
    assert pairs == {frozenset({"C000001", "C000002"}), frozenset({"C000003", "C000004"})}


def test_r02_missing_tin_blank_and_placeholder(tmp_path):
    con = simple(tmp_path, [cust("C000001"), cust("C000002"), cust("C000003")], certs=[
        cert("SC000001", "C000001", t1=""),
        cert("SC000002", "C000002", c2="NZ", t2="N/A"),
        cert("SC000003", "C000003"),
    ])
    assert [(r[0], r[3]) for r in flagged(con, "R02")] == [("C000001", "tin_1"), ("C000002", "tin_2")]


def test_r03_tin_format(tmp_path):
    con = simple(tmp_path, [cust(f"C00000{i}") for i in range(1, 6)], certs=[
        cert("SC000001", "C000001", t1="AU-FAKE-12345678"),      # too short
        cert("SC000002", "C000002", t1="NZ-FAKE-123456789"),     # wrong country prefix
        cert("SC000003", "C000003", t1="AU-FAKE-000000000"),     # repeated digit
        cert("SC000004", "C000004", t1="au-fake-123 456 789"),   # fixable, valid
        cert("SC000005", "C000005", t1=""),                      # missing is R02, not R03
    ])
    assert [r[0] for r in flagged(con, "R03")] == ["C000001", "C000002", "C000003"]


def test_r04_residence_mismatch(tmp_path):
    con = simple(tmp_path, [cust(f"C00000{i}") for i in range(1, 5)], certs=[
        cert("SC000001", "C000001", c1="NZ", t1="NZ-FAKE-12345678"),
        cert("SC000002", "C000002", c1="NZ", t1="NZ-FAKE-12345678",
             c2="AU", t2="AU-FAKE-123456789"),                     # AU among declared
        cert("SC000003", "C000003", c1="Ausralia"),                # unreadable: skip
        cert("SC000004", "C000004", c1="Australia"),               # fixable: matches
    ])
    assert [r[0] for r in flagged(con, "R04")] == ["C000001"]


def test_r05_us_indicia_without_declaration(tmp_path):
    us_addr = '"5 Main St, Austin, TX 73301, USA"'
    con = simple(tmp_path, [
        cust("C000001", mail=us_addr),
        cust("C000002", phone='"+61 400 000 000; +1 (512) 555-0100"'),
        cust("C000003", pob='"Denver, United States"'),
        cust("C000004", pob='"Denver, United States"'),          # declared Y
        cust("C000005"),                                          # no indicia
        cust("C000006", res=us_addr, mail=us_addr, country="US"),
    ], certs=[
        cert("SC000001", "C000001"), cert("SC000002", "C000002"),
        cert("SC000003", "C000003"), cert("SC000004", "C000004", us="Y"),
        cert("SC000005", "C000005"),
        # C000006 has no self-certification
    ])
    assert [r[0] for r in flagged(con, "R05")] == ["C000001", "C000002", "C000003", "C000006"]


def test_r06_r07_orphans_and_customers_without_accounts(tmp_path):
    con = run(tmp_path, [cust("C000001"), cust("C000002")],
              [acct("A0000001", "c000001 "), acct("A0000002", "C000999")],
              [cert("SC000001", "C000001"), cert("SC000002", "C000002")])
    assert [r[1] for r in flagged(con, "R06")] == ["A0000002"]
    assert [r[0] for r in flagged(con, "R07")] == ["C000002"]


def test_r08_missing_undated_and_future_selfcerts(tmp_path):
    con = simple(tmp_path, [cust(f"C00000{i}") for i in range(1, 5)], certs=[
        cert("SC000002", "C000002", signed=""),
        cert("SC000003", "C000003", signed="2027-01-01"),
        cert("SC000004", "C000004", signed="30/06/2026"),          # on the reporting date
    ])
    assert [r[0] for r in flagged(con, "R08")] == ["C000001", "C000002", "C000003"]
    severity = dict(con.execute("""SELECT customer_id, severity FROM exception_register
                                   WHERE rule_id = 'R08'""").fetchall())
    assert severity == {"C000001": "High", "C000002": "Medium", "C000003": "Medium"}


def test_r09_unfixable_formats_only(tmp_path):
    con = run(tmp_path,
              [cust("C000001", dob="31/02/1985"), cust("C000002", country="Australia")],
              [acct("A0000001", "C000001", currency="$"), acct("A0000002", "C000002", currency="a$")],
              [cert("SC000001", "C000001", us="maybe"), cert("SC000002", "C000002", us="yes")])
    got = sorted((r[1], r[3]) for r in flagged(con, "R09"))
    assert got == [("A0000001", "currency"), ("C000001", "date_of_birth"), ("SC000001", "us_person")]


def test_reconciliation_accounts_for_every_record(tmp_path):
    con = run(tmp_path, [cust("C000001"), cust("C000002")],
              [acct("A0000001", "C000001", '"1,000.50"'), acct("A0000002", "C000999", "20.00", "usd")],
              [cert("SC000001", "C000001"), cert("SC000002", "C000002")])
    assert {r[0] for r in con.execute("SELECT result FROM recon_counts").fetchall()} == {"OK"}
    totals = con.execute("""SELECT currency, source_balance, target_balance, held_out_balance, result
                            FROM recon_control_totals ORDER BY currency""").fetchall()
    assert [(c, float(s), float(t), float(h), r) for c, s, t, h, r in totals] == [
        ("AUD", 1000.50, 1000.50, 0.0, "OK"), ("USD", 20.0, 0.0, 20.0, "OK")]


# ---------------------------------------------------- improvement round fixes
def test_r01_word_order_and_initial_matches(tmp_path):
    con = simple(tmp_path, [
        cust("C000001", "Kathryn Alvarez"),
        cust("C000002", '"ALVAREZ, Kathryn"'),                # surname first, with comma
        cust("C000003", "Leon Leach", dob="1960-02-02"),
        cust("C000004", "L. Leach", dob="1960-02-02"),         # initial only
        cust("C000005", "P. Nguyen", dob="1970-07-07"),
        cust("C000006", "Thomas Nguyen", dob="1970-07-07"),    # initial does not match
    ])
    pairs = {frozenset((r[1], r[2])) for r in flagged(con, "R01")}
    assert pairs == {frozenset({"C000001", "C000002"}), frozenset({"C000003", "C000004"})}


def test_r01_known_limit_twins_are_flagged(tmp_path):
    """Documented limit: name + date of birth cannot tell twins apart."""
    con = simple(tmp_path, [cust("C000001", "Jennifer Ellis"), cust("C000002", "Jennifea Ellis")])
    assert len(flagged(con, "R01")) == 1


def test_r02_tin_with_no_digits_counts_as_missing(tmp_path):
    values = ["TBA", "NOT APPLICABLE", "XXXXXXXXX", "n.a."]
    customers = [cust(f"C00000{i}") for i in range(1, 6)]
    certs = [cert(f"SC00000{i}", f"C00000{i}", t1=v) for i, v in enumerate(values, start=1)]
    certs.append(cert("SC000005", "C000005"))
    con = simple(tmp_path, customers, certs=certs)
    assert [r[0] for r in flagged(con, "R02")] == ["C000001", "C000002", "C000003", "C000004"]
    assert flagged(con, "R03") == []


def test_r05_us_dot_alias_and_00_phone_prefix(tmp_path):
    con = simple(tmp_path, [
        cust("C000001", mail='"9 Elm St, Salem, OR 97301, U.S."'),
        cust("C000002", phone='"+61 400 000 000; 001 512 555 0100"'),
        cust("C000003", phone='"0061 400 000 000"'),             # 00 prefix, not US
    ])
    assert [r[0] for r in flagged(con, "R05")] == ["C000001", "C000002"]


def test_r08_placeholder_and_impossible_signature_dates(tmp_path):
    con = simple(tmp_path, [cust("C000001"), cust("C000002"), cust("C000003")], certs=[
        cert("SC000001", "C000001", signed="1900-01-01"),
        cert("SC000002", "C000002", signed="1975-01-01"),         # before birth (1980)
        cert("SC000003", "C000003", signed="2019-05-05"),
    ])
    assert [r[0] for r in flagged(con, "R08")] == ["C000001", "C000002"]
