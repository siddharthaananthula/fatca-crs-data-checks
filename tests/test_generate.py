import csv

from generate import generate


def read(path):
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_same_seed_gives_identical_files(tmp_path):
    generate(tmp_path / "a")
    generate(tmp_path / "b")
    for rel in ["raw/customer_master.csv", "raw/accounts.csv",
                "raw/self_certifications.csv", "injected_issues.csv"]:
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes()


def test_row_counts_and_answer_key_keys_exist(tmp_path):
    generate(tmp_path)
    customers = read(tmp_path / "raw/customer_master.csv")
    accounts = read(tmp_path / "raw/accounts.csv")
    issues = read(tmp_path / "injected_issues.csv")
    assert len(customers) == 2000
    assert len(accounts) == 3000

    customer_ids = {r["customer_id"] for r in customers}
    account_ids = {r["account_id"] for r in accounts}
    assert len(customer_ids) == 2000 and len(account_ids) == 3000
    for issue in issues:
        if issue["source_system"] == "accounts":
            assert issue["record_key"] in account_ids
        else:
            assert issue["record_key"] in customer_ids
        if issue["issue_type"] == "orphan_account":
            assert issue["related_key"] not in customer_ids
        if issue["issue_type"] == "duplicate_customer":
            assert issue["related_key"] in customer_ids
