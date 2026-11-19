import pytest

from inventory.sku import normalize_sku


@pytest.mark.parametrize(
    ("raw", "sku"),
    [
        ("BOLT-M4-20", "BOLT-M4-20"),
        ("bolt-m4-20", "BOLT-M4-20"),
        (" bolt m4 20 ", "BOLT-M4-20"),
        ("nut_m4", "NUT-M4"),
        ("Washer  M4", "WASHER-M4"),
    ],
)
def test_normalize_sku(raw, sku):
    assert normalize_sku(raw) == sku
