"""Generate the three synthetic source extracts and the injected issue answer key.

Run from the project folder:

    .venv\\Scripts\\python src\\generate.py

Outputs (all fictional, fixed seed):
    data/raw/customer_master.csv
    data/raw/accounts.csv
    data/raw/self_certifications.csv
    data/injected_issues.csv   (answer key, used only to score the checks)

Clean values use one standard form (ISO dates, ISO 3166 alpha-2 countries,
ISO 4217 currencies, "Open"/"Closed", "Y"/"N"). Every value that departs from
that form, and every planted data problem, is written to the answer key.
"""

from __future__ import annotations

import argparse
import csv
import random
import unicodedata
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

from reference import (
    COUNTRIES,
    CURRENCY_ALIASES,
    REPORTING_DATE,
    SEED,
    country_name,
)

N_BASE_CUSTOMERS = 1960
N_DUPLICATES = 40
N_ACCOUNTS = 3000
N_ORPHAN_ACCOUNTS = 30

CUSTOMER_FIELDS = [
    "customer_id", "full_name", "date_of_birth", "residence_address",
    "mailing_address", "country_of_residence", "phone_numbers",
    "place_of_birth", "nationality",
]
ACCOUNT_FIELDS = [
    "account_id", "customer_id", "account_type", "open_date", "balance_ye",
    "currency", "status",
]
SELFCERT_FIELDS = [
    "selfcert_id", "customer_id", "tax_residency_1", "tin_1",
    "tax_residency_2", "tin_2", "tax_residency_3", "tin_3", "us_person",
    "date_signed",
]
ISSUE_FIELDS = [
    "issue_id", "issue_type", "sub_type", "expected_rule", "source_system",
    "record_key", "related_key", "field", "injected_value", "clean_value",
    "description",
]

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
NON_US = [c for c in COUNTRIES if c != "US"]
US_COUNTRY_SPELLINGS = ["United States", "USA", "United States of America"]


def fmt_date(d: date, style: str = "iso") -> str:
    if style == "iso":
        return d.isoformat()
    if style == "dmy":
        return d.strftime("%d/%m/%Y")
    if style == "d-mon-y":
        return f"{d.day}-{MONTHS[d.month - 1]}-{d.year}"
    if style == "ymd8":
        return d.strftime("%Y%m%d")
    raise ValueError(style)


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


class Generator:
    def __init__(self, seed: int = SEED):
        self.rng = random.Random(seed)
        self.fakers = {}
        for i, (iso2, info) in enumerate(COUNTRIES.items()):
            fake = Faker(info[2])
            fake.seed_instance(seed + i)
            self.fakers[iso2] = fake
        self.customers: list[dict] = []
        self.selfcerts: dict[str, dict] = {}   # customer_id -> row
        self.accounts: list[dict] = []
        self.issues: list[dict] = []
        self.touched: set[tuple[str, str, str]] = set()

    # ------------------------------------------------------------ helpers
    def digits(self, n: int) -> str:
        return "".join(str(self.rng.randint(0, 9)) for _ in range(n))

    def rand_date(self, start: date, end: date) -> date:
        return start + timedelta(days=self.rng.randint(0, (end - start).days))

    def fake_tin(self, iso2: str) -> str:
        digits = self.digits(COUNTRIES[iso2][5])
        while len(set(digits)) == 1:          # never a repeated-digit placeholder
            digits = self.digits(COUNTRIES[iso2][5])
        return f"{iso2}-FAKE-{digits}"

    def phone(self, iso2: str) -> str:
        d = self.digits
        if iso2 == "AU":
            return f"+61 4{d(2)} {d(3)} {d(3)}"
        if iso2 == "NZ":
            return f"+64 2{d(1)} {d(3)} {d(4)}"
        if iso2 == "GB":
            return f"+44 7{d(3)} {d(6)}"
        if iso2 == "US":   # 555-01xx is a well known fictional range
            return f"+1 ({self.rng.randint(201, 989)}) 555-01{d(2)}"
        if iso2 == "IN":
            return f"+91 9{d(4)} {d(5)}"
        if iso2 == "DE":
            return f"+49 15{d(2)} {d(7)}"
        if iso2 == "FR":
            return f"+33 6 {d(2)} {d(2)} {d(2)} {d(2)}"
        if iso2 == "IE":
            return f"+353 8{d(1)} {d(3)} {d(4)}"
        raise ValueError(iso2)

    def address(self, iso2: str, country_label: str | None = None) -> str:
        fake = self.fakers[iso2]
        if iso2 == "US":
            state = fake.state_abbr(include_territories=False,
                                    include_freely_associated_states=False)
            body = f"{fake.street_address()}, {fake.city()}, {state} {fake.zipcode()}"
        else:
            body = fake.address().replace("\n", ", ")
        return f"{body}, {country_label or country_name(iso2)}"

    def birthplace(self, iso2: str, country_label: str | None = None) -> str:
        return f"{self.fakers[iso2].city()}, {country_label or country_name(iso2)}"

    def log(self, issue_type, sub_type, rule, source, key, field, value,
            description, related="", clean=""):
        self.issues.append({
            "issue_id": f"I{len(self.issues) + 1:04d}",
            "issue_type": issue_type,
            "sub_type": sub_type,
            "expected_rule": rule,
            "source_system": source,
            "record_key": key,
            "related_key": related,
            "field": field,
            "injected_value": value,
            "clean_value": clean,
            "description": description,
        })
        self.touched.add((source, key, field))

    def free(self, source: str, key: str, field: str) -> bool:
        return (source, key, field) not in self.touched

    # ------------------------------------------------------- base records
    def make_customer(self, cid: str, iso2: str) -> dict:
        fake = self.fakers[iso2]
        dob = self.rand_date(date(1940, 1, 1), date(2006, 6, 30))
        residence = self.address(iso2)
        mailing = residence if self.rng.random() < 0.88 else self.address(iso2)
        phones = [self.phone(iso2)]
        if self.rng.random() < 0.3:
            phones.append(self.phone(iso2))
        if iso2 == "US":
            birth = "US" if self.rng.random() < 0.7 else self.rng.choice(NON_US)
        else:
            others = [c for c in NON_US if c != iso2]
            birth = iso2 if self.rng.random() < 0.85 else self.rng.choice(others)
        return {
            "customer_id": cid,
            "full_name": f"{fake.first_name()} {fake.last_name()}",
            "date_of_birth": fmt_date(dob),
            "residence_address": residence,
            "mailing_address": mailing,
            "country_of_residence": iso2,
            "phone_numbers": "; ".join(phones),
            "place_of_birth": self.birthplace(birth),
            "nationality": birth,
            "_dob": dob,
            "_residence": iso2,
        }

    def make_selfcert(self, cust: dict) -> dict:
        iso2 = cust["_residence"]
        countries = [iso2]
        if iso2 != "US":
            r = self.rng.random()
            extra = 2 if r < 0.01 else 1 if r < 0.08 else 0
            others = [c for c in NON_US if c != iso2]
            countries += self.rng.sample(others, extra)
        row = {"selfcert_id": "", "customer_id": cust["customer_id"]}
        for i in range(3):
            c = countries[i] if i < len(countries) else ""
            row[f"tax_residency_{i + 1}"] = c
            row[f"tin_{i + 1}"] = self.fake_tin(c) if c else ""
        row["us_person"] = "Y" if iso2 == "US" else "N"
        row["date_signed"] = ""
        return row

    def build_base(self) -> None:
        ids = [f"C{n:06d}" for n in range(1, N_BASE_CUSTOMERS + N_DUPLICATES + 1)]
        self.rng.shuffle(ids)
        self.base_ids = ids[:N_BASE_CUSTOMERS]
        self.dup_ids = ids[N_BASE_CUSTOMERS:]
        codes = list(COUNTRIES)
        weights = [COUNTRIES[c][6] for c in codes]
        for cid in self.base_ids:
            iso2 = self.rng.choices(codes, weights)[0]
            cust = self.make_customer(cid, iso2)
            self.customers.append(cust)
            self.selfcerts[cid] = self.make_selfcert(cust)
        self.by_id = {c["customer_id"]: c for c in self.customers}

    # -------------------------------------------------------------- pools
    def draw_pools(self) -> None:
        """Pick disjoint customer sets so each planted issue is isolated."""
        available = list(self.base_ids)
        self.rng.shuffle(available)

        def take(n, predicate=lambda c: True):
            chosen = [cid for cid in available if predicate(self.by_id[cid])][:n]
            for cid in chosen:
                available.remove(cid)
            return chosen

        def single_non_us(c):
            sc = self.selfcerts[c["customer_id"]]
            return c["_residence"] != "US" and sc["tax_residency_2"] == ""

        self.pool = {
            # Legitimate US persons living in Australia: indicia AND a Y
            # declaration. These are true negatives for R05, not issues.
            "us_person_abroad": take(15, lambda c: c["_residence"] == "AU"
                                     and single_non_us(c)),
            "dup_original": take(N_DUPLICATES),
            "missing_tin": take(40),
            "malformed_tin": take(40),
            "mismatch": take(40, single_non_us),
            "us_indicia": take(40, single_non_us),
            "no_account": take(40),
            # Non-US only: a US resident with no self-certification would also
            # be a genuine R05 hit (indicia, no declaration) and blur scoring.
            "missing_selfcert": take(40, lambda c: c["_residence"] != "US"),
            "selfcert_undated": take(10),
            "selfcert_future": take(10),
            "bad_country": take(5),
            "bad_dob": take(6),
            "bad_tax_country": take(5, lambda c: c["_residence"] != "US"),
            "bad_us_person": take(5, lambda c: c["_residence"] != "US"),
        }

    def add_us_persons_abroad(self) -> None:
        for cid in self.pool["us_person_abroad"]:
            cust = self.by_id[cid]
            cust["place_of_birth"] = self.birthplace("US")
            cust["nationality"] = "US"
            sc = self.selfcerts[cid]
            sc["tax_residency_2"] = "US"
            sc["tin_2"] = self.fake_tin("US")
            sc["us_person"] = "Y"

    # ---------------------------------------------------------- duplicates
    def typo(self, name: str) -> str:
        first, last = name.rsplit(" ", 1)
        while True:
            letters = list(last)
            op = self.rng.choice(["swap", "drop", "double"])
            i = self.rng.randint(1, len(letters) - 2) if len(letters) > 3 else 1
            if op == "swap":
                letters[i], letters[i + 1] = letters[i + 1], letters[i]
            elif op == "drop":
                del letters[i]
            else:
                letters.insert(i, letters[i])
            new = "".join(letters)
            if new != last:
                return f"{first} {new}"

    def add_duplicates(self) -> None:
        variants = (["exact"] * 10 + ["name_typo"] * 10
                    + ["name_format"] * 10 + ["dob_format"] * 10)
        for orig_id, dup_id, variant in zip(self.pool["dup_original"],
                                            self.dup_ids, variants):
            orig = self.by_id[orig_id]
            dup = dict(orig, customer_id=dup_id)
            detail = "same name and date of birth"
            if variant == "name_typo":
                dup["full_name"] = self.typo(orig["full_name"])
                detail = f"name spelt '{dup['full_name']}'"
            elif variant == "name_format":
                plain = strip_accents(orig["full_name"])
                if plain != orig["full_name"]:
                    dup["full_name"] = plain
                elif self.rng.random() < 0.5:
                    dup["full_name"] = orig["full_name"].upper()
                else:
                    dup["full_name"] = orig["full_name"].replace(" ", "  ")
                detail = f"name written '{dup['full_name']}'"
            elif variant == "dob_format":
                dup["date_of_birth"] = fmt_date(orig["_dob"], "dmy")
                detail = f"date of birth written {dup['date_of_birth']}"
            self.customers.append(dup)
            self.by_id[dup_id] = dup
            sc = dict(self.selfcerts[orig_id], customer_id=dup_id)
            self.selfcerts[dup_id] = sc
            self.log("duplicate_customer", variant, "R01", "customer_master",
                     dup_id, "customer_id", dup_id,
                     f"Duplicate of {orig_id}: {detail}", related=orig_id)
            if variant == "dob_format":
                self.log("format_fixable", "date_dmy", "STAGING_FIX",
                         "customer_master", dup_id, "date_of_birth",
                         dup["date_of_birth"], "Date of birth in dd/mm/yyyy",
                         clean=fmt_date(orig["_dob"]))
            if variant in ("name_typo", "name_format"):
                self.touched.add(("customer_master", dup_id, "full_name"))

    # ------------------------------------------------------------ accounts
    def new_account(self, cid: str, residence: str, dob: date) -> dict:
        r = self.rng.random()
        if residence == "AU":
            currency = "AUD" if r < 0.92 else "USD" if r < 0.96 else "GBP"
        else:
            currency = COUNTRIES[residence][3] if r < 0.55 else "AUD"
        start = max(date(2008, 1, 1), dob.replace(year=dob.year + 18)
                    if not (dob.month == 2 and dob.day == 29)
                    else date(dob.year + 18, 3, 1))
        opened = self.rand_date(start, REPORTING_DATE)
        closed = self.rng.random() < 0.08
        balance = 0.0 if closed else min(
            round(self.rng.lognormvariate(9.6, 1.3), 2), 1_500_000.0)
        return {
            "account_id": "",
            "customer_id": cid,
            "account_type": self.rng.choices(
                ["Transaction", "Savings", "Term Deposit", "Investment"],
                [0.4, 0.3, 0.2, 0.1])[0],
            "open_date": fmt_date(opened),
            "balance_ye": f"{balance:.2f}",
            "currency": currency,
            "status": "Closed" if closed else "Open",
            "_opened": opened,
        }

    def build_accounts(self) -> None:
        no_account = set(self.pool["no_account"])
        eligible = sorted(c["customer_id"] for c in self.customers
                          if c["customer_id"] not in no_account)
        counts = Counter({cid: 1 for cid in eligible})
        for _ in range(N_ACCOUNTS - N_ORPHAN_ACCOUNTS - len(eligible)):
            counts[self.rng.choice(eligible)] += 1
        rows = []
        for cid in eligible:
            cust = self.by_id[cid]
            for _ in range(counts[cid]):
                rows.append(self.new_account(cid, cust["_residence"], cust["_dob"]))
        orphan_ids = self.rng.sample(
            [f"C{n:06d}" for n in range(2001, 3000)], N_ORPHAN_ACCOUNTS)
        for cid in orphan_ids:
            dob = self.rand_date(date(1950, 1, 1), date(2000, 12, 31))
            row = self.new_account(cid, "AU", dob)
            row["_orphan"] = True
            rows.append(row)
        self.rng.shuffle(rows)
        for n, row in enumerate(rows, start=1):
            row["account_id"] = f"A{n:07d}"
        self.accounts = rows
        for row in rows:
            if row.get("_orphan"):
                self.log("orphan_account", "", "R06", "accounts",
                         row["account_id"], "customer_id", row["customer_id"],
                         f"Account points to {row['customer_id']}, which is "
                         "not in the customer master",
                         related=row["customer_id"])
        for cid in sorted(no_account):
            self.log("customer_no_account", "", "R07", "customer_master", cid,
                     "customer_id", cid, "Customer has no accounts")

    def date_selfcerts(self) -> None:
        first_open: dict[str, date] = {}
        for acc in self.accounts:
            cid = acc["customer_id"]
            if cid not in first_open or acc["_opened"] < first_open[cid]:
                first_open[cid] = acc["_opened"]
        for cid in sorted(self.selfcerts):
            start = max(first_open.get(cid, date(2016, 1, 1)), date(2016, 1, 1))
            self.selfcerts[cid]["date_signed"] = fmt_date(
                self.rand_date(start, REPORTING_DATE))

    # -------------------------------------------------- planted problems
    def add_tin_issues(self) -> None:
        missing = ["blank"] * 15 + ["whitespace"] * 5 + ["placeholder"] * 20
        for cid, variant in zip(self.pool["missing_tin"], missing):
            sc = self.selfcerts[cid]
            slots = [i for i in (1, 2, 3) if sc[f"tax_residency_{i}"]]
            slot = slots[-1] if len(slots) > 1 and self.rng.random() < 0.5 else 1
            value = {"blank": "", "whitespace": "   ",
                     "placeholder": self.rng.choice(["N/A", "NONE", "UNKNOWN", "-"])
                     }[variant]
            sc[f"tin_{slot}"] = value
            self.log("missing_tin", variant, "R02", "self_certification", cid,
                     f"tin_{slot}", value,
                     f"No usable TIN for declared residency "
                     f"{sc[f'tax_residency_{slot}']}")

        malformed = ["wrong_length", "letter_in_digits", "wrong_country_prefix",
                     "missing_prefix", "repeated_digit"] * 8
        for cid, variant in zip(self.pool["malformed_tin"], malformed):
            sc = self.selfcerts[cid]
            iso2 = sc["tax_residency_1"]
            good = sc["tin_1"]
            digits = good.split("-")[-1]
            if variant == "wrong_length":
                bad = good + self.digits(1) if self.rng.random() < 0.5 else good[:-1]
            elif variant == "letter_in_digits":
                i = self.rng.randrange(len(digits))
                bad = f"{iso2}-FAKE-{digits[:i]}{self.rng.choice('OIS')}{digits[i + 1:]}"
            elif variant == "wrong_country_prefix":
                other = self.rng.choice([c for c in COUNTRIES if c != iso2])
                bad = f"{other}-FAKE-{digits}"
            elif variant == "missing_prefix":
                bad = digits
            else:
                bad = f"{iso2}-FAKE-{str(self.rng.randint(0, 9)) * len(digits)}"
            sc["tin_1"] = bad
            self.log("malformed_tin", variant, "R03", "self_certification", cid,
                     "tin_1", bad, f"TIN does not fit the fake {iso2} format")

    def add_mismatches(self) -> None:
        for cid in self.pool["mismatch"]:
            cust = self.by_id[cid]
            sc = self.selfcerts[cid]
            other = self.rng.choice([c for c in NON_US if c != cust["_residence"]])
            sc["tax_residency_1"] = other
            sc["tin_1"] = self.fake_tin(other)
            self.log("residence_selfcert_mismatch", "", "R04",
                     "self_certification", cid, "tax_residency_1", other,
                     f"Customer master residence {cust['_residence']} but "
                     f"self-certified tax residency is {other} only")

    def add_us_indicia(self) -> None:
        variants = (["us_residence_address"] * 10 + ["us_mailing_address"] * 10
                    + ["us_phone"] * 10 + ["us_place_of_birth"] * 10)
        for cid, variant in zip(self.pool["us_indicia"], variants):
            cust = self.by_id[cid]
            if variant == "us_residence_address":
                old = cust["_residence"]
                cust["residence_address"] = self.address("US")
                cust["mailing_address"] = cust["residence_address"]
                cust["country_of_residence"] = "US"
                cust["_residence"] = "US"
                field, value = "residence_address", cust["residence_address"]
                self.log("residence_selfcert_mismatch", "side_effect_of_us_residence",
                         "R04", "customer_master", cid, "country_of_residence",
                         "US", f"Residence moved to US but self-certified tax "
                         f"residency is still {old}")
            elif variant == "us_mailing_address":
                label = self.rng.choice(US_COUNTRY_SPELLINGS)
                cust["mailing_address"] = self.address("US", label)
                field, value = "mailing_address", cust["mailing_address"]
            elif variant == "us_phone":
                us = self.phone("US")
                if self.rng.random() < 0.5:
                    cust["phone_numbers"] = us
                else:
                    cust["phone_numbers"] = f"{cust['phone_numbers']}; {us}"
                field, value = "phone_numbers", cust["phone_numbers"]
            else:
                label = self.rng.choice(["United States", "USA"])
                cust["place_of_birth"] = self.birthplace("US", label)
                field, value = "place_of_birth", cust["place_of_birth"]
            self.log("us_indicia_no_declaration", variant, "R05",
                     "customer_master", cid, field, value,
                     "US indicia present but US person declaration is N")

    def add_selfcert_issues(self) -> None:
        for cid in self.pool["missing_selfcert"]:
            del self.selfcerts[cid]
            self.log("missing_selfcert", "", "R08", "self_certification", cid,
                     "customer_id", cid, "No self-certification on file")
        for cid in self.pool["selfcert_undated"]:
            self.selfcerts[cid]["date_signed"] = ""
            self.log("selfcert_out_of_date", "undated", "R08",
                     "self_certification", cid, "date_signed", "",
                     "Self-certification has no signature date")
        for cid in self.pool["selfcert_future"]:
            d = self.rand_date(date(2027, 1, 1), date(2027, 12, 31))
            self.selfcerts[cid]["date_signed"] = fmt_date(d)
            self.log("selfcert_out_of_date", "future_dated", "R08",
                     "self_certification", cid, "date_signed", fmt_date(d),
                     "Self-certification dated after the reporting date")

    def add_unfixable_formats(self) -> None:
        typos = {"AU": "Ausralia", "NZ": "New Zeland", "GB": "Untied Kingdom",
                 "IN": "Inda", "DE": "Germnay", "FR": "Frnace", "IE": "Irland",
                 "US": "Untied States"}
        for cid in self.pool["bad_country"]:
            cust = self.by_id[cid]
            value = typos[cust["_residence"]]
            cust["country_of_residence"] = value
            self.log("format_unfixable", "country_typo", "R09", "customer_master",
                     cid, "country_of_residence", value,
                     "Country of residence cannot be mapped to an ISO code")
        bad_dobs = ["31/02/1985", "1979-13-07", "unknown",
                    "30/02/1991", "1968-00-12", "TBC"]
        for cid, value in zip(self.pool["bad_dob"], bad_dobs):
            self.by_id[cid]["date_of_birth"] = value
            self.log("format_unfixable", "invalid_date", "R09", "customer_master",
                     cid, "date_of_birth", value, "Date of birth is not a valid date")
        for cid in self.pool["bad_tax_country"]:
            sc = self.selfcerts[cid]
            value = typos[sc["tax_residency_1"]]
            sc["tax_residency_1"] = value
            self.log("format_unfixable", "country_typo", "R09",
                     "self_certification", cid, "tax_residency_1", value,
                     "Tax residency country cannot be mapped to an ISO code")
        for cid, value in zip(self.pool["bad_us_person"], ["U", "?", "", "U", "maybe"]):
            self.selfcerts[cid]["us_person"] = value
            self.log("format_unfixable", "invalid_flag", "R09",
                     "self_certification", cid, "us_person", value,
                     "US person declaration is not Y or N")

        normal = [a for a in self.accounts if not a.get("_orphan")]
        picks = self.rng.sample(normal, 11)
        for acc, value in zip(picks[:5], ["2019-02-30", "13/13/2018", "TBC",
                                         "2021-31-12", "00/00/0000"]):
            acc["open_date"] = value
            self.log("format_unfixable", "invalid_date", "R09", "accounts",
                     acc["account_id"], "open_date", value,
                     "Open date is not a valid date")
        for acc, value in zip(picks[5:], ["$", "$", "$", "$", "", ""]):
            acc["currency"] = value
            self.log("format_unfixable",
                     "ambiguous_currency" if value else "blank_currency", "R09",
                     "accounts", acc["account_id"], "currency", value,
                     "Currency is ambiguous or missing")

    # --------------------------------------------------- fixable formats
    def add_fixable_formats(self) -> None:
        rng = self.rng

        def apply(records, source, key_field, field, n, make, sub_type, desc,
                  eligible=lambda r: True):
            candidates = [r for r in records
                          if self.free(source, r[key_field], field)
                          and r[field].strip() != "" and eligible(r)]
            for r in rng.sample(candidates, n):
                new, clean = make(r), r[field]
                if new == clean:
                    continue
                r[field] = new
                self.log("format_fixable", sub_type, "STAGING_FIX", source,
                         r[key_field], field, new, desc, clean=clean)

        cm, ac, sc = "customer_master", "accounts", "self_certification"
        customers = sorted(self.customers, key=lambda c: c["customer_id"])
        accounts = self.accounts
        certs = [self.selfcerts[k] for k in sorted(self.selfcerts)]
        clean_country = lambda r: r["country_of_residence"] in COUNTRIES
        clean_dob = lambda r: r["date_of_birth"] == fmt_date(r["_dob"])

        apply(customers, cm, "customer_id", "date_of_birth", 30,
              lambda r: fmt_date(r["_dob"], "dmy"), "date_dmy",
              "Date of birth in dd/mm/yyyy", clean_dob)
        apply(customers, cm, "customer_id", "date_of_birth", 15,
              lambda r: fmt_date(r["_dob"], "d-mon-y"), "date_d_mon_y",
              "Date of birth in d-Mon-yyyy", clean_dob)
        apply(customers, cm, "customer_id", "date_of_birth", 15,
              lambda r: fmt_date(r["_dob"], "ymd8"), "date_yyyymmdd",
              "Date of birth in yyyymmdd", clean_dob)
        apply(customers, cm, "customer_id", "country_of_residence", 20,
              lambda r: country_name(r["country_of_residence"]), "country_name",
              "Country written as an English name", clean_country)
        apply(customers, cm, "customer_id", "country_of_residence", 10,
              lambda r: country_name(r["country_of_residence"]).upper() + " ",
              "country_name_upper_space", "Country name in capitals with a "
              "trailing space", clean_country)
        apply(customers, cm, "customer_id", "country_of_residence", 15,
              lambda r: COUNTRIES[r["country_of_residence"]][1], "country_iso3",
              "Country as an ISO alpha-3 code", clean_country)
        apply(customers, cm, "customer_id", "country_of_residence", 10,
              lambda r: f" {r['country_of_residence'].lower()}", "country_lower",
              "Country code in lower case with a leading space", clean_country)
        apply(customers, cm, "customer_id", "full_name", 20,
              lambda r: r["full_name"] + "  ", "trailing_space",
              "Name has trailing spaces")
        apply(customers, cm, "customer_id", "full_name", 10,
              lambda r: f" {r['full_name']} ", "leading_trailing_space",
              "Name has leading and trailing spaces")

        clean_currency = lambda r: r["currency"] in CURRENCY_SYMBOLS
        apply(accounts, ac, "account_id", "currency", 25,
              lambda r: r["currency"].lower(), "currency_lower",
              "Currency code in lower case", clean_currency)
        apply(accounts, ac, "account_id", "currency", 15,
              lambda r: f"{r['currency']} ", "currency_space",
              "Currency code with a trailing space", clean_currency)
        apply(accounts, ac, "account_id", "currency", 20,
              lambda r: CURRENCY_SYMBOLS[r["currency"]], "currency_symbol",
              "Currency written as a symbol", clean_currency)
        apply(accounts, ac, "account_id", "status", 20,
              lambda r: r["status"].upper(), "status_upper", "Status in capitals")
        apply(accounts, ac, "account_id", "status", 20,
              lambda r: r["status"].lower() + " ", "status_lower_space",
              "Status in lower case with a trailing space")
        clean_open = lambda r: r["open_date"] == fmt_date(r["_opened"])
        apply(accounts, ac, "account_id", "open_date", 40,
              lambda r: fmt_date(r["_opened"], "dmy"), "date_dmy",
              "Open date in dd/mm/yyyy", clean_open)
        apply(accounts, ac, "account_id", "open_date", 20,
              lambda r: fmt_date(r["_opened"], "d-mon-y"), "date_d_mon_y",
              "Open date in d-Mon-yyyy", clean_open)
        apply(accounts, ac, "account_id", "customer_id", 8,
              lambda r: r["customer_id"].lower(), "id_lower",
              "Customer ID in lower case", lambda r: not r.get("_orphan"))
        apply(accounts, ac, "account_id", "customer_id", 7,
              lambda r: r["customer_id"] + " ", "id_trailing_space",
              "Customer ID with a trailing space", lambda r: not r.get("_orphan"))
        apply(accounts, ac, "account_id", "balance_ye", 15,
              lambda r: f"{float(r['balance_ye']):,.2f}", "thousands_separator",
              "Balance written with thousands separators",
              lambda r: float(r["balance_ye"]) >= 1000)

        clean_tax = lambda r: r["tax_residency_1"] in COUNTRIES
        apply(certs, sc, "customer_id", "tax_residency_1", 20,
              lambda r: country_name(r["tax_residency_1"]), "country_name",
              "Tax residency written as an English name", clean_tax)
        apply(certs, sc, "customer_id", "tax_residency_1", 10,
              lambda r: COUNTRIES[r["tax_residency_1"]][1], "country_iso3",
              "Tax residency as an ISO alpha-3 code", clean_tax)
        apply(certs, sc, "customer_id", "tax_residency_1", 10,
              lambda r: r["tax_residency_1"].lower(), "country_lower",
              "Tax residency code in lower case", clean_tax)
        apply(certs, sc, "customer_id", "date_signed", 30,
              lambda r: fmt_date(date.fromisoformat(r["date_signed"]), "dmy"),
              "date_dmy", "Date signed in dd/mm/yyyy")
        apply(certs, sc, "customer_id", "tin_1", 5,
              lambda r: r["tin_1"].lower(), "tin_lower", "TIN in lower case")
        apply(certs, sc, "customer_id", "tin_1", 5,
              lambda r: f" {r['tin_1']} ", "tin_outer_space",
              "TIN with leading and trailing spaces")
        apply(certs, sc, "customer_id", "tin_1", 5,
              lambda r: r["tin_1"][:-6] + " " + r["tin_1"][-6:-3] + " "
              + r["tin_1"][-3:], "tin_inner_space",
              "TIN digits split by spaces")
        apply(certs, sc, "customer_id", "us_person", 20,
              lambda r: rng.choice({"Y": ["y", "Yes", "YES"],
                                    "N": ["n", "No", "NO", "N "]}[r["us_person"]]),
              "flag_variant", "US person flag written in another form",
              lambda r: r["us_person"] in ("Y", "N"))

    # --------------------------------------------------------------- run
    def run(self) -> None:
        self.build_base()
        self.draw_pools()
        self.add_us_persons_abroad()
        self.add_duplicates()
        self.build_accounts()
        self.date_selfcerts()
        self.add_tin_issues()
        self.add_mismatches()
        self.add_us_indicia()
        self.add_selfcert_issues()
        self.add_unfixable_formats()
        self.add_fixable_formats()
        cids = sorted(self.selfcerts)
        for n, cid in enumerate(cids, start=1):
            self.selfcerts[cid]["selfcert_id"] = f"SC{n:06d}"


# One symbol per currency, used to plant fixable symbol spellings.
CURRENCY_SYMBOLS = {}
for _symbol, _code in CURRENCY_ALIASES.items():
    CURRENCY_SYMBOLS.setdefault(_code, _symbol)


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def generate(out_dir: Path, seed: int = SEED) -> Generator:
    gen = Generator(seed)
    gen.run()
    raw = out_dir / "raw"
    write_csv(raw / "customer_master.csv", CUSTOMER_FIELDS,
              sorted(gen.customers, key=lambda r: r["customer_id"]))
    write_csv(raw / "accounts.csv", ACCOUNT_FIELDS,
              sorted(gen.accounts, key=lambda r: r["account_id"]))
    write_csv(raw / "self_certifications.csv", SELFCERT_FIELDS,
              [gen.selfcerts[k] for k in sorted(gen.selfcerts)])
    write_csv(out_dir / "injected_issues.csv", ISSUE_FIELDS, gen.issues)
    return gen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parent.parent / "data")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    gen = generate(args.out, args.seed)

    print(f"Seed: {args.seed}")
    print("Row counts")
    print(f"  customer_master.csv      {len(gen.customers):>5}")
    print(f"  accounts.csv             {len(gen.accounts):>5}")
    print(f"  self_certifications.csv  {len(gen.selfcerts):>5}")
    print(f"  injected_issues.csv      {len(gen.issues):>5}")
    print("\nInjected issues by type (expected rule)")
    by_type = Counter((i["issue_type"], i["expected_rule"]) for i in gen.issues)
    for (issue_type, rule), n in sorted(by_type.items(), key=lambda kv: kv[0][1]):
        print(f"  {rule:<12} {issue_type:<30} {n:>4}")
    print("\nBreakdown by sub type")
    by_sub = Counter((i["issue_type"], i["sub_type"]) for i in gen.issues)
    for (issue_type, sub), n in sorted(by_sub.items()):
        print(f"  {issue_type:<30} {sub or '-':<30} {n:>4}")


if __name__ == "__main__":
    main()
