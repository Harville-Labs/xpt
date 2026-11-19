import re


def normalize_sku(raw: str) -> str:
    parts = re.split(r"[\s_]+", raw.strip())
    return "-".join(p for p in parts if p)
