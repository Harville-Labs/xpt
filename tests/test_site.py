import hashlib
import itertools
import json
import runpy
import subprocess
import sys
import textwrap
from pathlib import Path

from xpt.site import CallSite, Unlocatable, short_ids, site_id

PRELUDE = """\
from xpt.site import call_site

SITES = []


def expect(*args, **kwargs):
    SITES.append(call_site(1, ROOT))
"""

PRELUDE_LINES = PRELUDE.count("\n")


def run(root: Path, body: str, name: str = "test_sample.py") -> list[CallSite | Unlocatable]:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PRELUDE + textwrap.dedent(body), encoding="utf-8")
    return runpy.run_path(str(path), init_globals={"ROOT": root})["SITES"]


def located(root: Path, body: str, name: str = "test_sample.py") -> list[CallSite]:
    sites = run(root, body, name)
    assert all(isinstance(s, CallSite) for s in sites), sites
    return [s for s in sites if isinstance(s, CallSite)]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_two_calls_on_one_line_get_ordinals_zero_and_one(tmp_path: Path):
    first, second = located(
        tmp_path,
        """
        def test_pair():
            expect(1, 1); expect(2, 2)

        test_pair()
        """,
    )
    assert (first.qualname, first.ordinal, first.arg_sha) == ("test_pair", 0, sha("1"))
    assert (second.qualname, second.ordinal, second.arg_sha) == ("test_pair", 1, sha("2"))
    assert first.line == second.line == PRELUDE_LINES + 3


def test_a_multiline_call_hashes_its_whole_expected_argument(tmp_path: Path):
    [site] = located(
        tmp_path,
        """
        def test_multiline():
            expect(
                {"a": 1},
                {
                    "a": 1,
                },
            )

        test_multiline()
        """,
    )
    assert site.ordinal == 0
    assert site.arg_sha == sha('{\n            "a": 1,\n        }')
    assert site.line == PRELUDE_LINES + 3


def test_non_ascii_text_before_the_call_on_its_line_is_located(tmp_path: Path):
    first, second = located(
        tmp_path,
        """
        def test_unicode():
            s = "héllo wörld ✓"; expect(s, "héllo wörld ✓"); expect(len(s), 13)

        test_unicode()
        """,
    )
    assert (first.ordinal, first.arg_sha) == (0, sha('"héllo wörld ✓"'))
    assert (second.ordinal, second.arg_sha) == (1, sha("13"))


def test_the_ordinal_counts_only_calls_with_the_same_callee_text(tmp_path: Path):
    sites = located(
        tmp_path,
        """
        def test_mixed():
            print("noise"); expect(1, 1)
            len([]); expect(2, 2)

        test_mixed()
        """,
    )
    assert [s.ordinal for s in sites] == [0, 1]


def test_the_expected_argument_can_be_a_keyword_or_absent(tmp_path: Path):
    keyword, absent = located(
        tmp_path,
        """
        def test_kinds():
            expect(1, expected=[1,  2])
            expect(1)

        test_kinds()
        """,
    )
    assert keyword.arg_sha == sha("[1,  2]")
    assert absent.arg_sha == ""


def test_methods_nested_functions_and_lambdas_are_named_by_qualname(tmp_path: Path):
    sites = located(
        tmp_path,
        """
        class TestParser:
            def test_add(self):
                expect(1, 1)

        def outer():
            def inner():
                expect(2, 2)
            inner()
            expect(3, 3)

        TestParser().test_add()
        outer()
        (lambda: expect(4, 4))()
        """,
    )
    assert [(s.qualname, s.ordinal) for s in sites] == [
        ("TestParser.test_add", 0),
        ("outer.<locals>.inner", 0),
        ("outer", 0),
        ("<lambda>", 0),
    ]


def test_an_async_test_is_named_by_its_coroutine_function(tmp_path: Path):
    [site] = located(
        tmp_path,
        """
        import asyncio

        async def test_async():
            await asyncio.sleep(0)
            expect(1, 1)

        asyncio.run(test_async())
        """,
    )
    assert (site.qualname, site.ordinal) == ("test_async", 0)


def test_a_decorated_test_is_named_by_the_test_not_the_wrapper(tmp_path: Path):
    [site] = located(
        tmp_path,
        """
        import functools

        def logged(fn):
            @functools.wraps(fn)
            def wrapper(*args):
                return fn(*args)
            return wrapper

        @logged
        def test_decorated():
            expect(1, 1)

        test_decorated()
        """,
    )
    assert site.qualname == "test_decorated"


def test_a_comprehension_belongs_to_its_enclosing_function(tmp_path: Path):
    sites = located(
        tmp_path,
        """
        def test_loop():
            [expect(i, i) for i in range(2)]
            expect(9, 9)

        test_loop()
        """,
    )
    assert [(s.qualname, s.ordinal) for s in sites] == [
        ("test_loop", 0),
        ("test_loop", 0),
        ("test_loop", 1),
    ]


def test_depth_one_from_a_helper_names_the_helpers_caller(tmp_path: Path):
    [site] = located(
        tmp_path,
        """
        def check(value, expected):
            SITES.append(call_site(1, ROOT))

        def test_uses_helper():
            check(1, [1])

        test_uses_helper()
        """,
    )
    assert (site.qualname, site.ordinal, site.arg_sha) == ("test_uses_helper", 0, sha("[1]"))


def test_a_module_level_call_is_named_module(tmp_path: Path):
    [site] = located(tmp_path, "expect(1, 1)\n")
    assert (site.qualname, site.ordinal) == ("<module>", 0)


def test_the_path_is_posix_and_relative_to_the_root(tmp_path: Path):
    [site] = located(tmp_path, "expect(1, 1)\n", name="tests/unit/test_nested.py")
    assert site.path == "tests/unit/test_nested.py"


def test_a_file_outside_the_root_keeps_its_absolute_posix_path(tmp_path: Path):
    path = tmp_path / "elsewhere" / "test_out.py"
    path.parent.mkdir()
    path.write_text(PRELUDE + "expect(1, 1)\n", encoding="utf-8")
    [site] = runpy.run_path(str(path), init_globals={"ROOT": tmp_path / "root"})["SITES"]
    assert isinstance(site, CallSite)
    assert site.path == path.resolve().as_posix()


def test_exec_code_is_unlocatable_with_a_reason(tmp_path: Path):
    [site] = run(tmp_path, 'exec("expect(1, 1)")\n')
    assert site == Unlocatable(
        "<string> has no source file; the comparison still runs, but xpt cannot rewrite it.",
        "<string>",
        1,
    )


def test_a_file_that_no_longer_parses_is_unlocatable(tmp_path: Path):
    path = tmp_path / "test_broken.py"
    path.write_text(PRELUDE + "def test_it():\n    expect(1, 1)\n", encoding="utf-8")
    namespace = runpy.run_path(str(path), init_globals={"ROOT": tmp_path})
    path.write_text(PRELUDE + "def test_it(:\n", encoding="utf-8")
    namespace["test_it"]()
    [site] = namespace["SITES"]
    assert isinstance(site, Unlocatable)
    assert "no longer parses" in site.reason


_RUN_WITH_ROOT = (
    "import pathlib, runpy, sys; "
    "runpy.run_path(sys.argv[1], init_globals={'ROOT': pathlib.Path(sys.argv[2])})"
)


def run_without_debug_ranges(tmp_path: Path, body: str) -> list[CallSite | Unlocatable]:
    path = tmp_path / "test_ranges.py"
    path.write_text(
        PRELUDE
        + textwrap.dedent(body)
        + "import dataclasses, json\n"
        + "print(json.dumps([[type(s).__name__, dataclasses.asdict(s)] for s in SITES]))\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-X", "no_debug_ranges", "-c", _RUN_WITH_ROOT, str(path), str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    kinds = {"CallSite": CallSite, "Unlocatable": Unlocatable}
    return [kinds[kind](**fields) for kind, fields in json.loads(proc.stdout)]


def test_without_debug_ranges_one_call_per_line_is_located(tmp_path: Path):
    [site] = run_without_debug_ranges(
        tmp_path,
        """
        def test_plain():
            expect(len("ab"), 2)

        test_plain()
        """,
    )
    assert isinstance(site, CallSite)
    assert (site.qualname, site.ordinal, site.arg_sha) == ("test_plain", 0, sha("2"))


def test_without_debug_ranges_two_calls_on_a_line_are_refused(tmp_path: Path):
    [site] = run_without_debug_ranges(
        tmp_path,
        """
        def test_pair():
            x = len("a"); expect(x, 1)

        test_pair()
        """,
    )
    assert isinstance(site, Unlocatable)
    assert f"line {PRELUDE_LINES + 3}" in site.reason
    assert "-X no_debug_ranges" in site.reason


TWO_CALLS = """
def test_it():
    expect(1, {expected})
    expect(2, 2)

test_it()
"""


def test_arg_sha_changes_with_the_expected_text_and_not_with_lines_above(tmp_path: Path):
    before = located(tmp_path / "a", TWO_CALLS.format(expected="1"))
    shifted = located(tmp_path / "b", "\n\n" + TWO_CALLS.format(expected="1"))
    edited = located(tmp_path / "c", TWO_CALLS.format(expected="[1]"))
    assert [s.arg_sha for s in shifted] == [s.arg_sha for s in before]
    assert edited[0].arg_sha != before[0].arg_sha
    assert edited[1].arg_sha == before[1].arg_sha


def test_ids_stay_the_same_when_lines_move(tmp_path: Path):
    before = located(tmp_path / "a", TWO_CALLS.format(expected="1"))
    shifted = located(tmp_path / "b", "\n\n" + TWO_CALLS.format(expected="1"))
    assert [s.line + 2 for s in before] == [s.line for s in shifted]
    assert [site_id(s) for s in before] == [site_id(s) for s in shifted]
    assert site_id(before[0]) != site_id(before[1])


def test_site_id_is_the_sha256_of_the_identity_key():
    site = CallSite("tests/test_a.py", "test_x", 2, sha("1"), 10)
    assert site_id(site) == sha(json.dumps(["tests/test_a.py", "test_x", 2]))
    assert site_id(site) == site_id(CallSite("tests/test_a.py", "test_x", 2, "", 99))


def test_short_ids_are_four_characters_and_lengthen_on_a_collision():
    def site(ordinal: int) -> CallSite:
        return CallSite("tests/test_a.py", "test_x", ordinal, "", 1)

    seen: dict[str, int] = {}
    for ordinal in itertools.count():
        prefix = site_id(site(ordinal))[:4]
        if prefix in seen:
            a, b = site(seen[prefix]), site(ordinal)
            break
        seen[prefix] = ordinal
    other = site(next(i for i in itertools.count() if site_id(site(i))[:4] not in seen))
    ids = short_ids([a, b, other, a])
    full_a, full_b = site_id(a), site_id(b)
    common = next(i for i, (x, y) in enumerate(zip(full_a, full_b, strict=True)) if x != y)
    assert ids == {
        ("tests/test_a.py", "test_x", a.ordinal): full_a[: common + 1],
        ("tests/test_a.py", "test_x", b.ordinal): full_b[: common + 1],
        ("tests/test_a.py", "test_x", other.ordinal): site_id(other)[:4],
    }
