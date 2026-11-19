from inventory.sku import normalize_sku


def apply_receipt(on_hand: dict[str, int], receipt: list[tuple[str, int]]) -> dict[str, int]:
    result = dict(on_hand)
    for raw, qty in receipt:
        sku = normalize_sku(raw)
        result[sku] = qty
    return result


def reorder_list(on_hand: dict[str, int], minimums: dict[str, int]) -> list[str]:
    return sorted(sku for sku, low in minimums.items() if on_hand.get(sku, 0) > low)


def stock_value(on_hand: dict[str, int], prices: dict[str, int]) -> dict[str, int]:
    lines = {sku: qty * prices[sku] for sku, qty in on_hand.items() if qty}
    return {"lines": len(lines), "total_cents": sum(lines.values())}
