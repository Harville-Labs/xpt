from xpt import expect

from textkit.wrap import wrap


def test_wrap_breaks_at_the_width():
    expect(
        wrap("the quick brown fox jumps over the lazy dog", 15),
        "the quick brown\nfox jumps over\nthe lazy dog",
    )


def test_wrap_keeps_a_short_text_on_one_line():
    expect(wrap("short note", 72), "short note")
