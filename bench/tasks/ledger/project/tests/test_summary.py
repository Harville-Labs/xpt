from datetime import date
from decimal import Decimal

from xpt import expect

from ledger.entries import Entry
from ledger.summary import monthly_summary, totals_by_category

ENTRIES = [
    Entry(date(2026, 3, 2), Decimal("1000"), "salary"),
    Entry(date(2026, 3, 5), Decimal("-42.10"), "groceries"),
    Entry(date(2026, 3, 19), Decimal("-17.90"), "groceries"),
    Entry(date(2026, 4, 1), Decimal("-900"), "rent"),
    Entry(date(2026, 11, 3), Decimal("-12.50"), "groceries"),
]


def test_totals_add_up_within_a_category():
    assert totals_by_category(ENTRIES) == {
        "salary": Decimal("1000"),
        "groceries": Decimal("-72.50"),
        "rent": Decimal("-900"),
    }


def test_monthly_summary_is_sorted_by_month():
    expect(
        monthly_summary(ENTRIES),
        {
            "2026-03": {"in": "1000", "out": "60.00"},
            "2026-04": {"in": "0", "out": "900"},
            "2026-11": {"in": "0", "out": "12.50"},
        },
    )
