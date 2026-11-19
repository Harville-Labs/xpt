from call_site import expect

def parse(s):
    return {"type": "addition", "left": "x", "right": "y", "ünï": "çødé"}

def test_two_on_one_line():
    expect(1); expect(2, 3)  # comment kept

def test_nested_multiline():
    assert True
    expect(
        parse("x + y"),  # trailing comment
        {"type": "sub"},
    )

def test_unicode_prefix():
    s = "ñ✓"; expect(parse(s))

class TestK:
    def test_method(self):
        for i in range(2):
            expect(i)
