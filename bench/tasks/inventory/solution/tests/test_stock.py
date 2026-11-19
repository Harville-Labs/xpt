from xpt import expect

from inventory.stock import apply_receipt, reorder_list, stock_value

ON_HAND = {"BOLT-M4-20": 120, "NUT-M4": 15, "WASHER-M4": 0}


def test_a_receipt_adds_to_stock_on_hand():
    expect(
        apply_receipt(ON_HAND, [("BOLT-M4-20", 30), ("nut m4", 5), ("SPRING-S1", 8)]),
        {"BOLT-M4-20": 150, "NUT-M4": 20, "WASHER-M4": 0, "SPRING-S1": 8},
    )


def test_reorder_lists_skus_below_their_minimum():
    assert reorder_list(ON_HAND, {"BOLT-M4-20": 50, "NUT-M4": 20, "WASHER-M4": 10}) == [
        "NUT-M4",
        "WASHER-M4",
    ]


def test_stock_value_skips_empty_bins():
    expect(stock_value(ON_HAND, {"BOLT-M4-20": 12, "NUT-M4": 4, "WASHER-M4": 1}), {"lines": 2, "total_cents": 1500})


def test_stock_value_of_an_empty_warehouse():
    expect(stock_value({}, {}), {"lines": 0, "total_cents": 0})
