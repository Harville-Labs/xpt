from decimal import Decimal


def _clean(text: str) -> str:
    return text.replace(",", "")


def parse_amount(text: str) -> Decimal:
    cleaned = _clean(text)
    if cleaned.startswith("(") and cleaned.endswith(")"):
        return -Decimal(cleaned[1:-1])
    return Decimal(cleaned)
