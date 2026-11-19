import pytest

from textkit.slug import slugify


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Hello", "hello"),
        ("Hello World", "hello-world"),
        ("Hello,  World!", "hello-world"),
        ("  Getting started -- part 2 ", "getting-started-part-2"),
        ("Café au lait", "cafe-au-lait"),
        ("Ünïcode Ëverywhere", "unicode-everywhere"),
    ],
)
def test_slugify(title, slug):
    assert slugify(title) == slug
