import pytest
from cli_support import xpt

from xpt.flaky import Volatile, accept_reason, paths, suggest
from xpt.path import format_path
from xpt.serialize import serialize

RUNS = ("20260101T000000-aaaa", "20260101T000100-bbbb", "20260101T000200-cccc")


def found(*values: object) -> list[tuple[str, str, str | None]]:
    """Each volatile path of `values`, one per run, with its kind and suggestion."""
    volatile = paths([serialize(v) for v in values], RUNS[: len(values)])
    return [(format_path(v.path), v.kind, v.suggestion) for v in volatile]


@pytest.mark.parametrize(
    ("values", "result"),
    [
        (
            ({"at": "2026-11-25T10:02:11", "n": 1}, {"at": "2026-11-25T10:04:57", "n": 1}),
            [('value["at"]', "timestamp", """ignore=['value["at"]']""")],
        ),
        (
            ({"id": "a1f"}, {"id": "9c2"}),
            [('value["id"]', "value", """ignore=['value["id"]']""")],
        ),
        (
            ({"tags": ["a", "b", "c"]}, {"tags": ["c", "a", "b"]}),
            [('value["tags"]', "order", "unordered=True")],
        ),
        (({"score": 0.1}, {"score": 0.1023}), [('value["score"]', "float", "atol=0.003")]),
        (
            ([{"id": 1, "x": 0}, {"id": 1, "x": 0}], [{"id": 2, "x": 0}, {"id": 1, "x": 0}]),
            [('value[0]["id"]', "value", """ignore=['value[...]["id"]']""")],
        ),
        (("a", "b"), [("value", "value", None)]),
        (([1, 2], [1, 2, 3]), [("value", "value", None)]),
        (({"a": 1}, {"a": 1}), []),
    ],
)
def test_a_path_that_differs_between_runs_is_volatile_with_a_suggestion_for_its_kind(
    values: tuple[object, object], result: list[tuple[str, str, str | None]]
):
    assert found(*values) == result


def test_a_path_with_order_and_value_changes_is_a_value_change():
    assert found({"t": [1, 2]}, {"t": [2, 1]}, {"t": [2, 3]}) == [
        ('value["t"]', "value", """ignore=['value["t"]']""")
    ]


def test_the_reason_counts_the_distinct_values_and_the_runs():
    [v] = paths([serialize({"id": i % 2}) for i in range(3)], RUNS)
    assert v.values == 2
    assert v.reason == (
        'value["id"] had 2 values in 3 runs with no source change. '
        """Consider ignore=['value["id"]']."""
    )


def test_a_value_that_cannot_be_ignored_says_to_make_it_deterministic():
    [v] = paths([serialize("a"), serialize("b")], RUNS[:2])
    assert v.reason == (
        "value had 2 values in 2 runs with no source change. "
        "Make it deterministic in the test, because ignore= removes only dict entries."
    )


def test_suggest_combines_the_options_for_every_path():
    volatile = paths(
        [
            serialize({"at": "2026-01-01", "id": 1, "tags": [1, 2], "x": 1.0}),
            serialize({"at": "2026-01-02", "id": 2, "tags": [2, 1], "x": 1.5}),
        ],
        RUNS[:2],
    )
    assert suggest(volatile) == (
        """ignore=['value["at"]', 'value["id"]'], unordered=True, atol=0.6"""
    )
    assert accept_reason(volatile).startswith(
        'value["at"], value["id"], value["tags"], value["x"] changed between 2 runs '
        "with no source change (rule: flaky). Consider ignore="
    )


def test_a_float_spread_is_rounded_up_past_the_spread():
    v = Volatile((), "float", 2, RUNS[:2], 0.003)
    assert v.suggestion == "atol=0.004"


SOURCE = """\
from datetime import datetime

from xpt import expect

COLORS = {"red", "green", "blue", "cyan", "plum", "teal"}


def order():
    return {"id": 7, "created_at": datetime.now().isoformat(), "tags": list(COLORS)}


def test_order():
    expect(order())


def test_steady():
    expect({"id": 7})
"""

TIMESTAMP = (
    'flaky: value["created_at"] looks like a timestamp and had 2 values in 2 runs '
    """with no source change. Consider ignore=['value["created_at"]']."""
)
TAGS = (
    'flaky: value["tags"] had its items in 2 orders in 2 runs with no source change. '
    "Consider unordered=True."
)


@pytest.fixture(autouse=True)
def utf_8_child_output(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")


def run_tests(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, seed: str):
    monkeypatch.setenv("PYTHONHASHSEED", seed)
    return pytester.runpytest_subprocess("-p", "no:cacheprovider")


@pytest.fixture
def twice(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> dict[str, str]:
    """Run the suite twice with different set orders, and return the ids by function."""
    (pytester.path / "test_it.py").write_text(SOURCE, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    run_tests(pytester, monkeypatch, "1").assert_outcomes(failed=2)
    run_tests(pytester, monkeypatch, "2").assert_outcomes(failed=2)
    groups = xpt(capsys, "failures", "--json").json["groups"]
    return {g["site"]["qualname"]: g["id"] for g in groups}


def test_one_run_finds_nothing_flaky(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    (pytester.path / "test_it.py").write_text(SOURCE, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    run_tests(pytester, monkeypatch, "1").assert_outcomes(failed=2)
    assert "flaky" not in xpt(capsys, "failures").out
    ran = xpt(capsys, "flaky")
    assert (ran.code, ran.out) == (0, "No flaky call sites in 1 stored run.\n")


def test_failures_names_the_timestamp_and_the_set_order_after_two_runs(
    capsys: pytest.CaptureFixture[str], twice: dict[str, str]
):
    ran = xpt(capsys, "failures")
    lines = ran.out.splitlines()
    assert f"  {TIMESTAMP}" in lines
    assert f"  {TAGS}" in lines
    assert lines[-1].endswith(f"1 acceptable. Next: xpt accept {twice['test_steady']}")


def test_failures_json_carries_each_volatile_path(
    capsys: pytest.CaptureFixture[str], twice: dict[str, str]
):
    groups = xpt(capsys, "failures", "--json").json["groups"]
    by_name = {g["site"]["qualname"]: g for g in groups}
    assert by_name["test_steady"]["flaky"] == []
    created, tags = by_name["test_order"]["flaky"]
    runs = created["runs"]
    assert len(runs) == 2
    assert created == {
        "path": 'value["created_at"]',
        "kind": "timestamp",
        "values": 2,
        "runs": runs,
        "spread": None,
        "suggestion": """ignore=['value["created_at"]']""",
    }
    assert tags == {
        "path": 'value["tags"]',
        "kind": "order",
        "values": 2,
        "runs": runs,
        "spread": None,
        "suggestion": "unordered=True",
    }


def test_show_names_the_volatile_paths(capsys: pytest.CaptureFixture[str], twice: dict[str, str]):
    ran = xpt(capsys, "show", twice["test_order"])
    assert f"  {TIMESTAMP}" in ran.out.splitlines()
    assert xpt(capsys, "show", twice["test_order"], "--json").json["group"]["flaky"]


def test_flaky_lists_the_volatile_call_sites(
    capsys: pytest.CaptureFixture[str], twice: dict[str, str]
):
    id = twice["test_order"]
    ran = xpt(capsys, "flaky")
    assert ran.code == 1
    assert ran.out.splitlines() == [
        f"{id}  test_it.py:13  flaky  2 runs",
        f"  {TIMESTAMP}",
        f"  {TAGS}",
        "1 flaky call site in 2 stored runs. "
        "Next: add the suggested options to the tests, then run them again.",
    ]
    data = xpt(capsys, "flaky", "--json").json
    assert (data["command"], len(data["runs"])) == ("flaky", 2)
    [site] = data["sites"]
    assert site["id"] == id
    assert site["site"] == {"path": "test_it.py", "line": 13, "qualname": "test_order"}
    assert site["run"] == data["runs"][-1]
    assert [v["path"] for v in site["flaky"]] == ['value["created_at"]', 'value["tags"]']


def test_accept_refuses_a_flaky_value_and_writes_nothing(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], twice: dict[str, str]
):
    id = twice["test_order"]
    ran = xpt(capsys, "accept", id)
    assert ran.code == 1
    assert ran.out.splitlines() == [
        f"{id}  test_it.py:13  suspect",
        '  value["created_at"], value["tags"] changed between 2 runs with no source change '
        """(rule: flaky). Consider ignore=['value["created_at"]'], unordered=True, """
        "or accept it with --force if it is right.",
        "No files changed (1 suspect). "
        f"Next: make the tests deterministic, or run xpt accept --force {id}.",
    ]
    assert (pytester.path / "test_it.py").read_text(encoding="utf-8") == SOURCE


def test_accept_all_writes_the_steady_value_and_refuses_the_flaky_one(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], twice: dict[str, str]
):
    ran = xpt(capsys, "accept", "--all", "--json")
    statuses = {o["id"]: o["status"] for o in ran.json["outcomes"]}
    assert statuses == {twice["test_order"]: "suspect", twice["test_steady"]: "accepted"}
    assert "    expect(order())\n" in (pytester.path / "test_it.py").read_text(encoding="utf-8")


def test_force_accepts_a_flaky_value(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], twice: dict[str, str]
):
    ran = xpt(capsys, "accept", "--force", twice["test_order"])
    assert ran.code == 0
    assert "    expect(\n        order(),\n" in (pytester.path / "test_it.py").read_text(
        encoding="utf-8"
    )


def test_the_pytest_report_names_the_flaky_paths_and_does_not_offer_them(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
):
    (pytester.path / "test_it.py").write_text(SOURCE, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    first = run_tests(pytester, monkeypatch, "1")
    assert "flaky" not in first.stdout.str()
    second = run_tests(pytester, monkeypatch, "2")
    lines = second.stdout.lines
    assert f"  {TIMESTAMP}" in lines
    assert f"  {TAGS}" in lines
    assert sum(line.startswith("  accept: xpt accept") for line in lines) == 1


def test_a_source_change_between_runs_is_not_flaky(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    (pytester.path / "test_it.py").write_text(SOURCE, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    run_tests(pytester, monkeypatch, "1").assert_outcomes(failed=2)
    (pytester.path / "app.py").write_text("VERSION = 2\n", encoding="utf-8")
    run_tests(pytester, monkeypatch, "2").assert_outcomes(failed=2)
    assert "flaky" not in xpt(capsys, "failures").out
