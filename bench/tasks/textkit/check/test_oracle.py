import pytest

from textkit.slug import slugify
from textkit.words import word_counts
from textkit.wrap import wrap


@pytest.mark.parametrize(("t", "s"), [("Hello,  World!", "hello-world"), ("--a--b--", "a-b"),
                                      ("Crème Brûlée", "creme-brulee"), ("Ünïcode Ëverywhere", "unicode-everywhere")])
def test_slug(t, s):
    assert slugify(t) == s


def test_words():
    assert word_counts("A a B") == {"a": 2, "b": 1}


def test_wrap():
    assert wrap("aa bb cc", 5) == "aa bb\ncc"
    assert wrap("one", 72) == "one"
    assert wrap("the quick brown fox jumps over the lazy dog", 15) == "the quick brown\nfox jumps over\nthe lazy dog"
