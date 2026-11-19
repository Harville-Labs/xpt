from datetime import date
from decimal import Decimal

import pytest

from ledger.entries import Entry, LedgerError, load_entries
from ledger.parse import parse_amount
from ledger.summary import monthly_summary, totals_by_category


@pytest.mark.parametrize(
    ("text", "value"),
    [("12", "12"), ("1,200.00", "1200.00"), ("$5", "5"), (" $1,200.00 ", "1200.00"),
     ("($3.50)", "-3.50"), ("(3.50)", "-3.50"), ("$0.99", "0.99"), ("( $1,000 )", "-1000")],
)
def test_parse(text, value):
    assert parse_amount(text) == Decimal(value)


def test_load():
    rows = [{"date": "2026-03-02", "amount": "$1,000.00", "category": "Salary"}]
    assert load_entries(rows) == [Entry(date(2026, 3, 2), Decimal("1000.00"), "salary")]
    with pytest.raises(LedgerError, match="row 0"):
        load_entries([{"date": "2026-01-01", "category": "x"}])


E = [Entry(date(2026, 3, 2), Decimal("1000"), "salary"),
     Entry(date(2026, 3, 5), Decimal("-42.10"), "g"),
     Entry(date(2026, 3, 19), Decimal("-17.90"), "g"),
     Entry(date(2026, 11, 3), Decimal("-12.50"), "g")]


def test_totals():
    assert totals_by_category(E) == {"salary": Decimal("1000"), "g": Decimal("-72.50")}


def test_monthly():
    assert monthly_summary(E) == {"2026-03": {"in": "1000", "out": "60.00"},
                                  "2026-11": {"in": "0", "out": "12.50"}}
