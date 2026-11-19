import re


def slugify(title: str) -> str:
    lowered = title.lower()
    return re.sub(r"[^a-z0-9]", "-", lowered).strip("-")
