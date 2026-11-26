"""Round 1: generate a small unseen-variant dataset to test the checks fairly.

The main answer key was designed alongside the checks, so a perfect score on
it says little about how the checks would cope with new data. This script was
written after rules R01 to R09 were fixed. It uses a different seed and issue
variants that do not appear in the main dataset (new typo patterns, other date
layouts, new TIN placeholders, other ways of writing US indicia), plus a few
clean records and one hard negative (twins).

The answer key records the IDEAL outcome: what a perfect pipeline would raise.
Where the current checks fall short, the score shows it.

Run from the project folder:

    .venv\\Scripts\\python src\\generate_unseen.py
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from generate import (
    ACCOUNT_FIELDS, CUSTOMER_FIELDS, ISSUE_FIELDS, SELFCERT_FIELDS,
    Generator, fmt_date, write_csv,
)
from reference import REPORTING_DATE

UNSEEN_SEED = 4242
OUT = Path(__file__).resolve().parent.parent / "data" / "unseen" / "round1"

CM, AC, SC = "customer_master", "accounts", "self_certification"


class Unseen:
    def __init__(self, seed: int = UNSEEN_SEED, out: Path = OUT,
                 first_customer: int = 800001, first_account: int = 8000001,
                 selfcert_prefix: str = "SC8") -> None:
        self.g = Generator(seed)
        self.out = out
        self.selfcert_prefix = selfcert_prefix
        self.customers: dict[str, dict] = {}
        self.certs: dict[str, dict] = {}
        self.accounts: list[dict] = []
        self.next_c = first_customer
        self.next_a = first_account

    def customer(self, iso2: str = "AU", account: bool = True, cert: bool = True) -> dict:
        cid = f"C{self.next_c:06d}"
        self.next_c += 1
        c = self.g.make_customer(cid, iso2)
        c["phone_numbers"] = self.g.phone(iso2)          # one number, no surprises
        self.customers[cid] = c
        first_open = None
        if account:
            first_open = self.account(cid, iso2, c["_dob"])["_opened"]
        if cert:
            sc = self.g.make_selfcert(c)
            for i in (2, 3):                             # single residency
                sc[f"tax_residency_{i}"] = sc[f"tin_{i}"] = ""
            start = max(first_open or date(2016, 1, 1), date(2016, 1, 1))
            sc["date_signed"] = fmt_date(self.g.rand_date(start, REPORTING_DATE))
            self.certs[cid] = sc
        return c

    def account(self, cid: str, iso2: str, dob: date) -> dict:
        a = self.g.new_account(cid, iso2, dob)
        a["account_id"] = f"A{self.next_a:07d}"
        self.next_a += 1
        self.accounts.append(a)
        return a

    def dup(self, orig: dict, **changes) -> dict:
        d = self.customer(orig["_residence"])
        keep = {k: d[k] for k in ("customer_id",)}
        d.update({k: v for k, v in orig.items() if k != "customer_id"})
        d.update(keep)
        d.update(changes)
        self.customers[d["customer_id"]] = d
        sc = dict(self.certs[orig["customer_id"]], customer_id=d["customer_id"])
        self.certs[d["customer_id"]] = sc
        return d

    def log(self, *args, **kwargs) -> None:
        self.g.log(*args, **kwargs)

    def build(self) -> None:
        g = self.g

        # ---- R01 duplicates with new variant types
        o = self.customer()
        first, last = o["full_name"].split(" ", 1)
        d = self.dup(o, full_name=f"{last} {first}")
        self.log("duplicate_customer", "name_order_swapped", "R01", CM, d["customer_id"],
                 "customer_id", d["full_name"], "Surname written first",
                 related=o["customer_id"])

        o = self.customer()
        d = self.dup(o, date_of_birth=o["_dob"].strftime("%Y/%m/%d"))
        self.log("duplicate_customer", "dob_yyyy_slash", "R01", CM, d["customer_id"],
                 "customer_id", d["date_of_birth"], "Date of birth written yyyy/mm/dd",
                 related=o["customer_id"])
        self.log("format_fixable", "date_yyyy_slash", "STAGING_FIX", CM, d["customer_id"],
                 "date_of_birth", d["date_of_birth"], "Date of birth in yyyy/mm/dd",
                 clean=fmt_date(o["_dob"]))

        o = self.customer()
        first, last = o["full_name"].split(" ", 1)
        other = g.fakers["AU"].last_name()
        o["full_name"] = f"{first} {last}-{other}"
        d = self.dup(o, full_name=f"{first} {last} {other}")
        self.log("duplicate_customer", "hyphen_vs_space", "R01", CM, d["customer_id"],
                 "customer_id", d["full_name"], "Double surname without the hyphen",
                 related=o["customer_id"])

        o = self.customer()
        first, last = o["full_name"].split(" ", 1)
        d = self.dup(o, full_name=f"{first[0]}. {last}")
        self.log("duplicate_customer", "first_initial", "R01", CM, d["customer_id"],
                 "customer_id", d["full_name"], "First name reduced to an initial",
                 related=o["customer_id"])

        o = self.customer()
        first, last = o["full_name"].split(" ", 1)
        typo = last[0] + last[2] + last[1] + last[3:] + last[-1]   # swap and double
        d = self.dup(o, full_name=f"{first} {typo}")
        self.log("duplicate_customer", "two_edits", "R01", CM, d["customer_id"],
                 "customer_id", d["full_name"], "Surname with two keying errors",
                 related=o["customer_id"])

        # ---- R02 new placeholder spellings
        for value in ["NOT APPLICABLE", "TBA", "XXXXXXXXX", "n.a."]:
            c = self.customer()
            self.certs[c["customer_id"]]["tin_1"] = value
            self.log("missing_tin", "new_placeholder", "R02", SC, c["customer_id"],
                     "tin_1", value, f"Placeholder '{value}' instead of a TIN")

        # ---- R03 new malformations
        c = self.customer()
        tin = self.certs[c["customer_id"]]["tin_1"]
        for value, sub in [(tin.replace("-", ""), "missing_hyphens")]:
            self.certs[c["customer_id"]]["tin_1"] = value
            self.log("malformed_tin", sub, "R03", SC, c["customer_id"], "tin_1", value,
                     "TIN without hyphens")
        c = self.customer()
        value = self.certs[c["customer_id"]]["tin_1"] + "X"
        self.certs[c["customer_id"]]["tin_1"] = value
        self.log("malformed_tin", "trailing_check_letter", "R03", SC, c["customer_id"],
                 "tin_1", value, "TIN with an extra trailing letter")
        c = self.customer()
        value = self.certs[c["customer_id"]]["tin_1"] + "-01"
        self.certs[c["customer_id"]]["tin_1"] = value
        self.log("malformed_tin", "numeric_suffix", "R03", SC, c["customer_id"],
                 "tin_1", value, "TIN with a numeric suffix")

        # ---- R04 new mismatch shapes
        c = self.customer("AU")
        sc = self.certs[c["customer_id"]]
        sc.update(tax_residency_1="NZ", tin_1=g.fake_tin("NZ"),
                  tax_residency_2="GB", tin_2=g.fake_tin("GB"))
        self.log("residence_selfcert_mismatch", "two_residencies_neither_match", "R04", SC,
                 c["customer_id"], "tax_residency_1", "NZ;GB",
                 "Residence AU, declared NZ and GB")
        c = self.customer("AU")
        sc = self.certs[c["customer_id"]]
        sc.update(tax_residency_1="NZL", tin_1=g.fake_tin("NZ"))
        self.log("residence_selfcert_mismatch", "alpha3_declared", "R04", SC,
                 c["customer_id"], "tax_residency_1", "NZL",
                 "Residence AU, declared NZ written as NZL")
        self.log("format_fixable", "country_iso3", "STAGING_FIX", SC, c["customer_id"],
                 "tax_residency_1", "NZL", "Tax residency as alpha-3", clean="NZ")

        # ---- R05 new ways of writing US indicia
        c = self.customer("AU")
        c["mailing_address"] = g.address("US", "US")
        self.log("us_indicia_no_declaration", "mailing_country_code_US", "R05", CM,
                 c["customer_id"], "mailing_address", c["mailing_address"],
                 "US mailing address ending in 'US'")
        c = self.customer("AU")
        c["mailing_address"] = g.address("US", "U.S.")
        self.log("us_indicia_no_declaration", "mailing_country_U.S.", "R05", CM,
                 c["customer_id"], "mailing_address", c["mailing_address"],
                 "US mailing address ending in 'U.S.'")
        c = self.customer("AU")
        c["phone_numbers"] += f"; 001 {g.rng.randint(201, 989)} 555 01{g.digits(2)}"
        self.log("us_indicia_no_declaration", "phone_00_prefix", "R05", CM,
                 c["customer_id"], "phone_numbers", c["phone_numbers"],
                 "US phone written with the 001 international prefix")
        c = self.customer("AU")
        c["phone_numbers"] += f"; +1-{g.rng.randint(201, 989)}-555-01{g.digits(2)}"
        self.log("us_indicia_no_declaration", "phone_dashes", "R05", CM,
                 c["customer_id"], "phone_numbers", c["phone_numbers"],
                 "US phone written +1-xxx-xxx-xxxx")
        c = self.customer("AU")
        c["place_of_birth"] = "Boston, Massachusetts"
        self.log("us_indicia_no_declaration", "birthplace_state_only", "R05", CM,
                 c["customer_id"], "place_of_birth", c["place_of_birth"],
                 "US place of birth given as city and state, no country")

        # ---- R06 orphans
        c_dob = date(1980, 1, 1)
        a = self.account("C899999", "AU", c_dob)
        self.log("orphan_account", "unknown_id", "R06", AC, a["account_id"], "customer_id",
                 "C899999", "Account for a customer not in the master", related="C899999")
        a = self.account("C8O0099", "AU", c_dob)
        self.log("orphan_account", "letter_o_in_id", "R06", AC, a["account_id"],
                 "customer_id", "C8O0099", "Customer ID keyed with letter O",
                 related="C8O0099")
        self.log("format_unfixable", "id_letter_o", "R09", AC, a["account_id"],
                 "customer_id", "C8O0099", "Customer ID is not in the C + 6 digits form")

        # ---- R07 and R08
        c = self.customer(account=False)
        self.log("customer_no_account", "", "R07", CM, c["customer_id"], "customer_id",
                 c["customer_id"], "Customer has no accounts")
        c = self.customer(cert=False)
        self.log("missing_selfcert", "", "R08", SC, c["customer_id"], "customer_id",
                 c["customer_id"], "No self-certification on file")
        c = self.customer()
        self.certs[c["customer_id"]]["date_signed"] = "1900-01-01"
        self.log("selfcert_out_of_date", "placeholder_date", "R08", SC, c["customer_id"],
                 "date_signed", "1900-01-01", "Placeholder signature date 1900-01-01")
        c = self.customer()
        self.certs[c["customer_id"]]["date_signed"] = "15/03/2027"
        self.log("selfcert_out_of_date", "future_dmy", "R08", SC, c["customer_id"],
                 "date_signed", "15/03/2027", "Future date written dd/mm/yyyy")

        # ---- formats a staging step should ideally fix (new layouts and names)
        c = self.customer()
        c["date_of_birth"] = c["_dob"].strftime("%d.%m.%Y")
        self.log("format_fixable", "date_dots", "STAGING_FIX", CM, c["customer_id"],
                 "date_of_birth", c["date_of_birth"], "Date of birth dd.mm.yyyy",
                 clean=fmt_date(c["_dob"]))
        c = self.customer()
        c["date_of_birth"] = f"{c['_dob'].strftime('%B')} {c['_dob'].day}, {c['_dob'].year}"
        self.log("format_fixable", "date_long_us", "STAGING_FIX", CM, c["customer_id"],
                 "date_of_birth", c["date_of_birth"], "Date of birth as 'March 5, 1984'",
                 clean=fmt_date(c["_dob"]))
        c = self.customer("GB")
        c["country_of_residence"] = "U.K."
        self.log("format_fixable", "country_dotted_alias", "STAGING_FIX", CM, c["customer_id"],
                 "country_of_residence", "U.K.", "Country written U.K.", clean="GB")
        c = self.customer("NZ")
        c["country_of_residence"] = "Aotearoa"
        self.log("format_fixable", "country_local_name", "STAGING_FIX", CM, c["customer_id"],
                 "country_of_residence", "Aotearoa", "New Zealand by its Maori name",
                 clean="NZ")
        c = self.customer()
        a = [x for x in self.accounts if x["customer_id"] == c["customer_id"]][0]
        clean = a["currency"]
        a["currency"] = clean + "."
        self.log("format_fixable", "currency_trailing_dot", "STAGING_FIX", AC, a["account_id"],
                 "currency", a["currency"], "Currency code with a trailing full stop",
                 clean=clean)

        # ---- genuinely unfixable values
        c = self.customer()
        a = [x for x in self.accounts if x["customer_id"] == c["customer_id"]][0]
        a["currency"] = "dollars"
        self.log("format_unfixable", "currency_word", "R09", AC, a["account_id"], "currency",
                 "dollars", "Currency written as 'dollars'")
        c = self.customer()
        a = [x for x in self.accounts if x["customer_id"] == c["customer_id"]][0]
        a["open_date"] = "sometime 2019"
        self.log("format_unfixable", "date_text", "R09", AC, a["account_id"], "open_date",
                 "sometime 2019", "Open date is free text")

        # ---- clean records and one hard negative (twins: different people)
        for iso2 in ["AU", "AU", "NZ", "GB"]:
            self.customer(iso2)
        t1 = self.customer()
        first, last = t1["full_name"].split(" ", 1)
        t2 = self.customer()
        t2.update(full_name=f"{first[:-1]}{'a' if first[-1] != 'a' else 'e'} {last}",
                  date_of_birth=t1["date_of_birth"], _dob=t1["_dob"],
                  residence_address=t1["residence_address"],
                  mailing_address=t1["mailing_address"])

    def write(self) -> None:
        write_csv(self.out / "raw" / "customer_master.csv", CUSTOMER_FIELDS,
                  sorted(self.customers.values(), key=lambda r: r["customer_id"]))
        write_csv(self.out / "raw" / "accounts.csv", ACCOUNT_FIELDS, self.accounts)
        certs = [self.certs[k] for k in sorted(self.certs)]
        for n, sc in enumerate(certs, start=1):
            sc["selfcert_id"] = f"{self.selfcert_prefix}{n:05d}"
        write_csv(self.out / "raw" / "self_certifications.csv", SELFCERT_FIELDS, certs)
        write_csv(self.out / "injected_issues.csv", ISSUE_FIELDS, self.g.issues)


def main() -> None:
    u = Unseen()
    u.build()
    u.write()
    print(f"Seed: {UNSEEN_SEED}")
    print(f"  customers {len(u.customers)}, accounts {len(u.accounts)}, "
          f"self-certifications {len(u.certs)}, answer-key rows {len(u.g.issues)}")


if __name__ == "__main__":
    main()
