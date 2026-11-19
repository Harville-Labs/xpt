from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ledger.parse import parse_amount


class LedgerError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    day: date
    amount: Decimal
    category: str


def _entry(row: dict[str, str]) -> Entry:
    return Entry(
        day=date.fromisoformat(row["date"]),
        amount=parse_amount(row["amt"]),
        category=row["category"].strip().lower(),
    )


def load_entries(rows: list[dict[str, str]]) -> list[Entry]:
    entries = []
    for i, row in enumerate(rows):
        try:
            entries.append(_entry(row))
        except (KeyError, ValueError) as e:
            raise LedgerError(f"row {i} could not be read") from e
    return entries
