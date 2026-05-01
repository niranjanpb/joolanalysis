"""Analyse bank statement CSVs.

Usage:
    python analyze_statements.py path/to/statement.csv [more.csv ...]
    python analyze_statements.py --dir path/to/folder

Expected CSV columns (case-insensitive, common aliases accepted):
    date, description, amount
Optional: category, balance, debit, credit
If only debit/credit columns exist, amount is computed as credit - debit.
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable


CATEGORY_RULES: dict[str, list[str]] = {
    "Groceries": ["walmart", "kroger", "tesco", "sainsbury", "aldi", "lidl", "whole foods", "trader joe", "safeway", "costco"],
    "Dining": ["starbucks", "mcdonald", "uber eats", "doordash", "grubhub", "restaurant", "cafe", "coffee", "pizza"],
    "Transport": ["uber", "lyft", "shell", "exxon", "chevron", "bp ", "transit", "metro", "parking", "amtrak"],
    "Utilities": ["electric", "gas company", "water", "internet", "comcast", "xfinity", "at&t", "verizon", "t-mobile"],
    "Rent/Mortgage": ["rent", "mortgage", "landlord", "property mgmt"],
    "Subscriptions": ["netflix", "spotify", "hulu", "disney", "apple.com/bill", "google", "youtube", "prime video", "icloud"],
    "Shopping": ["amazon", "ebay", "etsy", "target", "best buy", "ikea"],
    "Health": ["pharmacy", "cvs", "walgreens", "doctor", "clinic", "hospital", "dental"],
    "Income": ["payroll", "salary", "deposit from", "direct deposit", "interest paid"],
    "Transfers": ["transfer", "zelle", "venmo", "paypal", "wire"],
    "Fees": ["fee", "charge", "overdraft", "atm withdrawal"],
}

DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y",
    "%Y/%m/%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y",
]

COLUMN_ALIASES = {
    "date": {"date", "transaction date", "posted date", "post date", "trans date"},
    "description": {"description", "details", "memo", "narrative", "payee", "particulars", "reference"},
    "amount": {"amount", "value", "transaction amount"},
    "debit": {"debit", "withdrawal", "withdrawals", "money out", "out"},
    "credit": {"credit", "deposit", "deposits", "money in", "in"},
    "category": {"category", "type"},
    "balance": {"balance", "running balance"},
}


@dataclass
class Transaction:
    date: datetime
    description: str
    amount: Decimal
    category: str
    source: str


def parse_date(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def parse_amount(value: str) -> Decimal | None:
    if value is None:
        return None
    cleaned = value.strip().replace(",", "").replace("$", "").replace("£", "").replace("€", "")
    if not cleaned:
        return None
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1]
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def resolve_columns(fieldnames: Iterable[str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    lower_map = {f.lower().strip(): f for f in fieldnames if f}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower_map:
                resolved[canonical] = lower_map[alias]
                break
    return resolved


def categorise(description: str, existing: str | None) -> str:
    if existing and existing.strip():
        return existing.strip()
    text = description.lower()
    for category, keywords in CATEGORY_RULES.items():
        if any(k in text for k in keywords):
            return category
    return "Uncategorised"


def load_csv(path: str) -> list[Transaction]:
    transactions: list[Transaction] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return transactions
        cols = resolve_columns(reader.fieldnames)
        if "date" not in cols or "description" not in cols:
            print(f"  ! skipping {path}: missing date/description columns", file=sys.stderr)
            return transactions
        if "amount" not in cols and not ("debit" in cols or "credit" in cols):
            print(f"  ! skipping {path}: missing amount columns", file=sys.stderr)
            return transactions

        for row in reader:
            date = parse_date(row.get(cols["date"], ""))
            if date is None:
                continue
            description = (row.get(cols["description"], "") or "").strip()

            if "amount" in cols:
                amount = parse_amount(row.get(cols["amount"], ""))
            else:
                debit = parse_amount(row.get(cols.get("debit", ""), "")) or Decimal(0)
                credit = parse_amount(row.get(cols.get("credit", ""), "")) or Decimal(0)
                amount = credit - debit
            if amount is None:
                continue

            existing_cat = row.get(cols["category"], "") if "category" in cols else None
            category = categorise(description, existing_cat)
            transactions.append(Transaction(date, description, amount, category, os.path.basename(path)))
    return transactions


def gather_paths(args: argparse.Namespace) -> list[str]:
    paths: list[str] = []
    if args.dir:
        paths.extend(sorted(glob.glob(os.path.join(args.dir, "*.csv"))))
    paths.extend(args.files)
    return [p for p in paths if os.path.isfile(p)]


def fmt(amount: Decimal) -> str:
    return f"{amount:>12,.2f}"


def report(transactions: list[Transaction]) -> None:
    if not transactions:
        print("No transactions parsed.")
        return

    transactions.sort(key=lambda t: t.date)
    income = sum((t.amount for t in transactions if t.amount > 0), Decimal(0))
    expenses = sum((t.amount for t in transactions if t.amount < 0), Decimal(0))
    net = income + expenses

    start = transactions[0].date.date()
    end = transactions[-1].date.date()

    print("=" * 64)
    print(f"Bank Statement Analysis  ({start} -> {end})")
    print(f"Transactions: {len(transactions)}  Sources: {len({t.source for t in transactions})}")
    print("=" * 64)
    print(f"Total income   : {fmt(income)}")
    print(f"Total expenses : {fmt(expenses)}")
    print(f"Net change     : {fmt(net)}")

    print("\nBy category (expenses):")
    cat_totals: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for t in transactions:
        if t.amount < 0:
            cat_totals[t.category] += t.amount
    for cat, total in sorted(cat_totals.items(), key=lambda kv: kv[1]):
        share = (total / expenses * 100) if expenses else Decimal(0)
        print(f"  {cat:<18} {fmt(total)}  ({share:5.1f}%)")

    print("\nBy month:")
    month_totals: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"in": Decimal(0), "out": Decimal(0)})
    for t in transactions:
        key = t.date.strftime("%Y-%m")
        if t.amount >= 0:
            month_totals[key]["in"] += t.amount
        else:
            month_totals[key]["out"] += t.amount
    print(f"  {'Month':<8} {'Income':>12} {'Expenses':>14} {'Net':>14}")
    for month in sorted(month_totals):
        v = month_totals[month]
        print(f"  {month:<8} {fmt(v['in'])} {fmt(v['out'])} {fmt(v['in'] + v['out'])}")

    print("\nTop 10 expenses:")
    top = sorted((t for t in transactions if t.amount < 0), key=lambda t: t.amount)[:10]
    for t in top:
        desc = re.sub(r"\s+", " ", t.description)[:40]
        print(f"  {t.date.date()}  {fmt(t.amount)}  {t.category:<14} {desc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyse bank statement CSVs.")
    parser.add_argument("files", nargs="*", help="CSV statement files")
    parser.add_argument("--dir", help="Directory containing CSV statements")
    args = parser.parse_args(argv)

    paths = gather_paths(args)
    if not paths:
        parser.error("provide at least one CSV file or --dir")

    all_tx: list[Transaction] = []
    for path in paths:
        print(f"Loading {path} ...")
        all_tx.extend(load_csv(path))

    print()
    report(all_tx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
