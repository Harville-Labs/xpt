import re
from collections import Counter

WORD = re.compile(r"[A-Za-z']+")


def word_counts(text: str) -> dict[str, int]:
    return dict(Counter(WORD.findall(text)).most_common())
