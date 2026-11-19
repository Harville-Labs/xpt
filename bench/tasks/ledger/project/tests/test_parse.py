from decimal import Decimal

import pytest

from ledger.parse import parse_amount


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("12", Decimal("12")),
        ("1,200.00", Decimal("1200.00")),
        ("$5", Decimal("5")),
        (" $1,200.00 ", Decimal("1200.00")),
        ("($3.50)", Decimal("-3.50")),
        ("(3.50)", Decimal("-3.50")),
    ],
)
def test_parse_amount(text, value):
    assert parse_amount(text) == value
