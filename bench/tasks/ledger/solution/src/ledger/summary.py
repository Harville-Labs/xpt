from collections import defaultdict
from decimal import Decimal

from ledger.entries import Entry


def totals_by_category(entries: list[Entry]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for e in entries:
        totals[e.category] += e.amount
    return dict(totals)


def monthly_summary(entries: list[Entry]) -> dict[str, dict[str, str]]:
    months: dict[str, dict[str, Decimal]] = {}
    for e in entries:
        key = f"{e.day.year}-{e.day.month:02d}"
        bucket = months.setdefault(key, {"in": Decimal(0), "out": Decimal(0)})
        if e.amount >= 0:
            bucket["in"] += e.amount
        else:
            bucket["out"] += -e.amount
    return {k: {side: str(v) for side, v in b.items()} for k, b in sorted(months.items())}
