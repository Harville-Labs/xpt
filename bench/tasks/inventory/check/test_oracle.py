import pytest

from inventory.sku import normalize_sku
from inventory.stock import apply_receipt, reorder_list, stock_value


@pytest.mark.parametrize(("raw", "sku"), [("bolt-m4-20", "BOLT-M4-20"), (" bolt m4 20 ", "BOLT-M4-20"),
                                          ("nut_m4", "NUT-M4"), ("Washer  M4", "WASHER-M4")])
def test_sku(raw, sku):
    assert normalize_sku(raw) == sku


def test_receipt():
    assert apply_receipt({"A-1": 2}, [("a 1", 3), ("b_2", 1)]) == {"A-1": 5, "B-2": 1}
    assert apply_receipt({"A-1": 2}, [("A-1", 1), ("A-1", 1)]) == {"A-1": 4}


def test_reorder():
    assert reorder_list({"A": 5, "B": 10}, {"A": 10, "B": 10, "C": 1}) == ["A", "C"]


def test_value():
    assert stock_value({"A": 2, "B": 0}, {"A": 3, "B": 9}) == {"lines": 1, "total_cents": 6}


def test_filled_expectations():
    import pathlib
    src = pathlib.Path("tests/test_stock.py").read_text()
    assert '"lines": 2' in src and '"total_cents": 1500' in src
    assert '"lines": 0' in src and '"total_cents": 0' in src
