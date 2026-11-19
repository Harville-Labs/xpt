def _break_line(words: list[str], width: int) -> tuple[str, list[str]]:
    line = words[0]
    i = 1
    while len(line) + 1 + len(words[i]) <= width:
        line += " " + words[i]
        i += 1
    return line, words[i:]


def _lines(words: list[str], width: int) -> list[str]:
    out = []
    while words:
        line, words = _break_line(words, width)
        out.append(line)
    return out


def wrap(text: str, width: int = 72) -> str:
    return "\n".join(_lines(text.split(), width))
