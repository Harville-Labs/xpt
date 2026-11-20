import pytest
from cli_support import xpt

SOURCE = """\
from xpt import expect

def parse(text):
    try:
        return int(text)
    except ValueError as error:
        return repr(error)

def test_error():
    expect(parse("x"))

def test_emptied():
    expect([], [1, 2])

def test_fine():
    expect(parse("3"))
"""


@pytest.fixture(autouse=True)
def utf_8_child_output(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")


@pytest.fixture
def ids(pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    (pytester.path / "test_it.py").write_text(SOURCE, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(failed=3)
    groups = xpt(capsys, "failures", "--json").json["groups"]
    return {g["site"]["qualname"]: g["id"] for g in groups}


def source(pytester: pytest.Pytester) -> str:
    return (pytester.path / "test_it.py").read_text(encoding="utf-8")


EXCEPTION_REASON = (
    "value looks like an exception or a traceback (rule: exception). "
    "Fix the code under test, or accept it with --force if it is right."
)


def test_accept_refuses_an_exception_repr_and_writes_nothing(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", ids["test_error"])
    assert ran.code == 1
    assert ran.out.splitlines() == [
        f"{ids['test_error']}  test_it.py:10  suspect",
        f"  {EXCEPTION_REASON}",
        "No files changed (1 suspect). "
        f"Next: fix the code under test, or run xpt accept --force {ids['test_error']}.",
    ]
    assert source(pytester) == SOURCE


def test_accept_json_reports_the_suspect_status_and_reason(
    capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", ids["test_emptied"], "--json")
    assert ran.code == 1
    [outcome] = ran.json["outcomes"]
    assert (outcome["status"], outcome["reason"]) == (
        "suspect",
        "value is empty where the expected value had items (rule: empty). "
        "Fix the code under test, or accept it with --force if it is right.",
    )
    assert ran.json["changed"] == []


def test_accept_all_writes_ordinary_values_and_refuses_suspect_ones(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", "--all")
    assert ran.code == 1
    statuses = [
        line.split()[2] for line in ran.out.splitlines() if line.startswith(tuple(ids.values()))
    ]
    assert statuses == ["suspect", "suspect", "accepted"]
    assert '    expect(parse("3"), 3)\n' in source(pytester)
    assert '    expect(parse("x"))\n' in source(pytester)


def test_force_accepts_a_suspect_value(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", "--force", ids["test_error"], ids["test_emptied"])
    assert ran.code == 0
    assert "    expect([], [])\n" in source(pytester)
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=2, failed=1)
