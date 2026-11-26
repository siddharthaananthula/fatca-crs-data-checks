"""Round 2: a second unseen-variant dataset, written after the round 1 fixes.

Written before any round 2 result was seen, and not tuned afterwards. It uses
a different seed (7777) and issue variants that appear neither in the main
dataset nor in round 1: nicknames, middle names, "SURNAME, First" with a
comma, swapped day and month, new TIN placeholders and malformations, a
country outside the reference list, US indicia written in new ways, and
format layouts staging has not seen. It also has clean records and hard
negatives. The answer key records the ideal outcome.

Run from the project folder:

    .venv\\Scripts\\python src\\generate_unseen_round2.py
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from generate import fmt_date
from generate_unseen import AC, CM, SC, Unseen

SEED = 7777
OUT = Path(__file__).resolve().parent.parent / "data" / "unseen" / "round2"


class Round2(Unseen):
    def __init__(self) -> None:
        super().__init__(seed=SEED, out=OUT, first_customer=700001,
                         first_account=7000001, selfcert_prefix="SC7")

    def first_account(self, cid: str) -> dict:
        return [a for a in self.accounts if a["customer_id"] == cid][0]

    def build(self) -> None:  # noqa: C901 - a flat list of planted cases
        g = self.g

        # ---- R01 duplicates
        o = self.customer()
        last = o["full_name"].split(" ", 1)[1]
        o["full_name"] = f"William {last}"
        d = self.dup(o, full_name=f"Bill {last}")
        self.log("duplicate_customer", "nickname", "R01", CM, d["customer_id"], "customer_id",
                 d["full_name"], "Nickname Bill for William", related=o["customer_id"])

        o = self.customer()
        first, last = o["full_name"].split(" ", 1)
        d = self.dup(o, full_name=f"{first} {g.fakers['AU'].first_name()} {last}")
        self.log("duplicate_customer", "middle_name_added", "R01", CM, d["customer_id"],
                 "customer_id", d["full_name"], "Middle name added", related=o["customer_id"])

        o = self.customer()
        first, last = o["full_name"].split(" ", 1)
        d = self.dup(o, full_name=f"{last.upper()}, {first}")
        self.log("duplicate_customer", "surname_comma_first", "R01", CM, d["customer_id"],
                 "customer_id", d["full_name"], "Written 'SURNAME, First'",
                 related=o["customer_id"])

        o = self.customer()
        dob = date(o["_dob"].year, 3, 11)                 # day and month both <= 12
        o.update(_dob=dob, date_of_birth=fmt_date(dob))
        swapped = date(dob.year, 11, 3)
        d = self.dup(o, date_of_birth=fmt_date(swapped), _dob=swapped)
        self.log("duplicate_customer", "day_month_swapped", "R01", CM, d["customer_id"],
                 "customer_id", d["date_of_birth"], "Day and month of birth swapped",
                 related=o["customer_id"])

        o = self.customer()
        first = o["full_name"].split(" ", 1)[0]
        o["full_name"] = f"{first} McAllister"
        d = self.dup(o, full_name=f"{first} Mc Allister")
        self.log("duplicate_customer", "space_in_surname", "R01", CM, d["customer_id"],
                 "customer_id", d["full_name"], "Space inside the surname",
                 related=o["customer_id"])

        # ---- R02 placeholders of new kinds
        for value in ["Not provided", "?", "999999999"]:
            c = self.customer()
            self.certs[c["customer_id"]]["tin_1"] = value
            self.log("missing_tin", "new_placeholder_r2", "R02", SC, c["customer_id"],
                     "tin_1", value, f"Placeholder '{value}' instead of a TIN")

        # ---- R03 new malformations
        for make, sub in [
            (lambda t: "FAKE-AU-" + t.split("-")[-1], "prefix_order_swapped"),
            (lambda t: t.replace("-", "–"), "en_dash_separators"),
            (lambda t: "AU-" + t, "doubled_country_prefix"),
        ]:
            c = self.customer()
            value = make(self.certs[c["customer_id"]]["tin_1"])
            self.certs[c["customer_id"]]["tin_1"] = value
            self.log("malformed_tin", sub, "R03", SC, c["customer_id"], "tin_1", value,
                     "TIN in an unexpected shape")

        # ---- R04
        c = self.customer("DE")
        self.certs[c["customer_id"]].update(tax_residency_1="AT", tin_1="AT-FAKE-123456789")
        self.log("residence_selfcert_mismatch", "country_outside_reference", "R04", SC,
                 c["customer_id"], "tax_residency_1", "AT",
                 "Residence DE, declared Austria (not in the reference list)")
        c = self.customer("GB")
        c["country_of_residence"] = "Great Britain"
        self.certs[c["customer_id"]].update(tax_residency_1="IE", tin_1=g.fake_tin("IE"))
        self.log("residence_selfcert_mismatch", "alias_residence", "R04", CM,
                 c["customer_id"], "country_of_residence", "Great Britain",
                 "Residence written 'Great Britain', declared IE")
        self.log("format_fixable", "country_alias_great_britain", "STAGING_FIX", CM,
                 c["customer_id"], "country_of_residence", "Great Britain",
                 "Country written Great Britain", clean="GB")

        # ---- R05 new ways of writing US indicia
        c = self.customer("AU")
        c["mailing_address"] = g.address("US", "United States.")
        self.log("us_indicia_no_declaration", "mailing_trailing_full_stop", "R05", CM,
                 c["customer_id"], "mailing_address", c["mailing_address"],
                 "US mailing address ending 'United States.'")
        c = self.customer("AU")
        c["phone_numbers"] += f"; (+1) {g.rng.randint(201, 989)} 555 01{g.digits(2)}"
        self.log("us_indicia_no_declaration", "phone_bracketed_plus", "R05", CM,
                 c["customer_id"], "phone_numbers", c["phone_numbers"],
                 "US phone written (+1) ...")
        c = self.customer("AU")
        c["nationality"] = "American"
        self.log("us_indicia_no_declaration", "nationality_word", "R05", CM,
                 c["customer_id"], "nationality", "American", "Nationality written 'American'")
        c = self.customer("AU")
        c["place_of_birth"] = "Chicago, IL"
        self.log("us_indicia_no_declaration", "birthplace_state_code", "R05", CM,
                 c["customer_id"], "place_of_birth", "Chicago, IL",
                 "US place of birth as city and state code")
        c = self.customer("AU")
        c["residence_address"] = g.address("US", "USA")
        self.log("us_indicia_no_declaration", "residence_address_only", "R05", CM,
                 c["customer_id"], "residence_address", c["residence_address"],
                 "US residence address while the residence field still says AU")

        # ---- R06 and R07
        a = self.account("CUST700099", "AU", date(1980, 1, 1))
        self.log("orphan_account", "foreign_id_format", "R06", AC, a["account_id"],
                 "customer_id", "CUST700099", "Customer ID in another system's format",
                 related="CUST700099")
        self.log("format_unfixable", "id_format", "R09", AC, a["account_id"], "customer_id",
                 "CUST700099", "Customer ID is not in the C + 6 digits form")
        c = self.customer()
        acc = self.first_account(c["customer_id"])
        wrong = "C79" + c["customer_id"][3:]
        acc["customer_id"] = wrong
        self.log("orphan_account", "id_digit_typo", "R06", AC, acc["account_id"],
                 "customer_id", wrong, "Customer ID keyed with a wrong digit", related=wrong)
        self.log("customer_no_account", "account_keyed_to_wrong_id", "R07", CM,
                 c["customer_id"], "customer_id", c["customer_id"],
                 "Only account was keyed to a wrong customer ID")

        # ---- R08
        for value, sub in [("9999-12-31", "far_future_sentinel"),
                           ("0001-01-01", "year_one_sentinel"),
                           ("2026-02-30", "impossible_date")]:
            c = self.customer()
            self.certs[c["customer_id"]]["date_signed"] = value
            self.log("selfcert_out_of_date", sub, "R08", SC, c["customer_id"],
                     "date_signed", value, f"Signature date {value}")
            if sub == "impossible_date":
                self.log("format_unfixable", "invalid_date", "R09", SC, c["customer_id"],
                         "date_signed", value, "Signature date is not a valid date")

        # ---- formats a staging step should ideally fix
        c = self.customer("IE")
        acc = self.first_account(c["customer_id"])
        clean = acc["currency"]
        acc["currency"] = {"EUR": "Euro", "AUD": "Australian dollar"}[clean]
        self.log("format_fixable", "currency_word", "STAGING_FIX", AC, acc["account_id"],
                 "currency", acc["currency"], "Currency written as a word", clean=clean)
        c = self.customer("IE")
        c["country_of_residence"] = "Republic of Ireland"
        self.log("format_fixable", "country_long_name", "STAGING_FIX", CM, c["customer_id"],
                 "country_of_residence", "Republic of Ireland", "Long country name", clean="IE")
        c = self.customer()
        dob = date(c["_dob"].year, 3, 5)                  # single-digit month and day
        c["_dob"] = dob
        c["date_of_birth"] = f"{dob.year}-{dob.month}-{dob.day}"
        self.log("format_fixable", "date_unpadded", "STAGING_FIX", CM, c["customer_id"],
                 "date_of_birth", c["date_of_birth"], "ISO date without zero padding",
                 clean=fmt_date(dob))
        c = self.customer()
        acc = self.first_account(c["customer_id"])
        if acc["status"] == "Open":
            acc["status"] = "Active"
            self.log("format_fixable", "status_synonym", "STAGING_FIX", AC, acc["account_id"],
                     "status", "Active", "Status written 'Active'", clean="Open")

        # ---- genuinely unfixable or ambiguous values
        c = self.customer()
        acc = self.first_account(c["customer_id"])
        acc.update(balance_ye="12.345,67", status="Open")
        self.log("format_unfixable", "balance_european_decimal", "R09", AC, acc["account_id"],
                 "balance_ye", "12.345,67", "Balance with European separators is ambiguous")
        c = self.customer()
        acc = self.first_account(c["customer_id"])
        acc["currency"] = "AUD/USD"
        self.log("format_unfixable", "currency_pair", "R09", AC, acc["account_id"], "currency",
                 "AUD/USD", "Two currencies in one field")

        # ---- clean records and hard negatives
        for iso2 in ["AU", "NZ", "IN"]:
            self.customer(iso2)
        # Same date of birth and surname, different first initial: different people.
        a1 = self.customer()
        a2 = self.customer()
        surname = a1["full_name"].split(" ", 1)[1]
        a1["full_name"] = f"P. {surname}"
        a2.update(full_name=f"Thomas {surname}", date_of_birth=a1["date_of_birth"],
                  _dob=a1["_dob"])


def main() -> None:
    r = Round2()
    r.build()
    r.write()
    print(f"Seed: {SEED}")
    print(f"  customers {len(r.customers)}, accounts {len(r.accounts)}, "
          f"self-certifications {len(r.certs)}, answer-key rows {len(r.g.issues)}")


if __name__ == "__main__":
    main()
