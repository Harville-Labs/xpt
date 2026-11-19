from datetime import date
from decimal import Decimal

import pytest

from ledger.entries import Entry, LedgerError, load_entries

ROWS = [
    {"date": "2026-03-02", "amount": "$1,000.00", "category": "Salary"},
    {"date": "2026-03-05", "amount": "(42.10)", "category": " Groceries"},
]


def test_load_entries_reads_each_row():
    assert load_entries(ROWS) == [
        Entry(date(2026, 3, 2), Decimal("1000.00"), "salary"),
        Entry(date(2026, 3, 5), Decimal("-42.10"), "groceries"),
    ]


def test_a_bad_row_names_its_index():
    with pytest.raises(LedgerError, match="row 1"):
        load_entries([ROWS[0], {"date": "nope", "amount": "1", "category": "x"}])
