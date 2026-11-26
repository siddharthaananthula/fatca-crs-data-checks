"""Shared reference data for the synthetic FATCA and CRS project.

Everything here is invented for an educational model. The TIN formats are
deliberately and obviously fake: every valid TIN in this project looks like
``AU-FAKE-123456789``. The digit counts per country are illustrative only and
are not a statement about any real tax identifier.
"""

from datetime import date

SEED = 20260630

# Year end used for balances and for self-certification date checks.
REPORTING_DATE = date(2026, 6, 30)

# iso2: (english name, iso3, faker locale, currency, phone prefix,
#        fake TIN digit count, weight used when sampling residence)
COUNTRIES = {
    "AU": ("Australia", "AUS", "en_AU", "AUD", "+61", 9, 0.70),
    "NZ": ("New Zealand", "NZL", "en_NZ", "NZD", "+64", 8, 0.06),
    "GB": ("United Kingdom", "GBR", "en_GB", "GBP", "+44", 10, 0.06),
    "US": ("United States", "USA", "en_US", "USD", "+1", 9, 0.05),
    "IN": ("India", "IND", "en_IN", "INR", "+91", 10, 0.05),
    "DE": ("Germany", "DEU", "de_DE", "EUR", "+49", 11, 0.03),
    "FR": ("France", "FRA", "fr_FR", "EUR", "+33", 13, 0.03),
    "IE": ("Ireland", "IRL", "en_IE", "EUR", "+353", 8, 0.02),
}

CURRENCIES = sorted({c[3] for c in COUNTRIES.values()})

# Currency spellings that a staging step can safely map to an ISO 4217 code.
# A bare "$" is deliberately absent: it is ambiguous (AUD, NZD or USD).
CURRENCY_ALIASES = {
    "A$": "AUD",
    "AU$": "AUD",
    "NZ$": "NZD",
    "US$": "USD",
    "£": "GBP",
    "€": "EUR",
    "₹": "INR",
}

# Extra spellings of countries that can be safely mapped to ISO 3166 alpha-2.
# Names, alpha-3 codes and alpha-2 codes (any case, trimmed) are also accepted.
COUNTRY_ALIASES = {
    "USA": "US",
    "U.S.": "US",
    "U.S.A.": "US",
    "UNITED STATES OF AMERICA": "US",
    "UK": "GB",
    "GREAT BRITAIN": "GB",
}


def country_name(iso2: str) -> str:
    return COUNTRIES[iso2][0]


def tin_pattern(iso2: str) -> str:
    """Regular expression for a valid fake TIN for a country."""
    digits = COUNTRIES[iso2][5]
    return rf"^{iso2}-FAKE-[0-9]{{{digits}}}$"


# TIN values that mean "not provided". Treated as missing (rule R02), not as
# a malformed TIN.
TIN_PLACEHOLDERS = ["N/A", "NA", "NONE", "NIL", "UNKNOWN", "-"]

ACCOUNT_TYPES = ["Transaction", "Savings", "Term Deposit", "Investment"]
