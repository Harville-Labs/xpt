import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from cli_support import xpt

from xpt.apimodel import Param, Signature, annotation, signature_text, summary

FILES = {
    "xptshapes/__init__.py": '''
"""Shapes for tests."""
from ._impl import Circle, area
from .units import *
from . import extra

__all__ = ["Circle", "area", "Meters", "parse", "VERSION", "made_later", "extra", "Color"]

VERSION: str = "1.0"
globals()["made_later"] = lambda x, y=2: x


def _private():
    pass


from xptshapes._impl import Color
''',
    "xptshapes/_impl.py": '''
import enum
import os
from typing import overload


class Base:
    def __init__(self, name: str, *, tag: "str | None" = None) -> None:
        pass

    def describe(self) -> str:
        """Describe the shape. More text follows."""

    def _hidden(self):
        pass


class Circle(Base):
    """A round shape.

    A long description that is not shown.
    """

    @property
    def radius(self) -> float:
        """The radius."""

    @radius.setter
    def radius(self, value: float) -> None:
        pass

    @classmethod
    def unit(cls, scale: float = 1.0) -> "Circle":
        pass

    @staticmethod
    def pi() -> float:
        pass

    async def spin(self, turns: int) -> None:
        pass


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


@overload
def area(shape: Circle) -> float: ...
@overload
def area(shape: None) -> None: ...
def area(shape):
    """Area of a shape."""
''',
    "xptshapes/units.py": """
from dataclasses import dataclass, field

__all__ = ["Meters", "parse"]


@dataclass
class Meters:
    value: float
    label: str = "m"
    tags: list[str] = field(default_factory=list)


def parse(text):
    pass
""",
    "xptshapes/units.pyi": """
from dataclasses import dataclass

__all__ = ["Meters", "parse"]

@dataclass
class Meters:
    value: float
    label: str = "m"

def parse(text: str, default: str = "a default too long to show") -> Meters:
    '''Parse a length like 3m.'''
""",
    "xptshapes/extra/__init__.py": '''
"""Extra helpers."""


def helper(x, *rest, flag=False, **options):
    """Help."""
''',
    "xptshapes-1.2.3.dist-info/METADATA": "Name: xptshapes\nVersion: 1.2.3\n",
    "xptshapes-1.2.3.dist-info/top_level.txt": "xptshapes\n",
}


@pytest.fixture
def shapes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    for name, text in FILES.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.lstrip("\n"), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in [m for m in sys.modules if m.split(".")[0] == "xptshapes"]:
        del sys.modules[name]


LISTING = """\
xptshapes 1.2.3
Circle(name: str, *, tag: str | None = None) -> None  A round shape.
  .radius: float  The radius.
  .unit(scale: float = 1.0) -> Circle
  .pi() -> float
  async .spin(turns: int) -> None
  .describe() -> str  Describe the shape.
area(shape: Circle) -> float  Area of a shape.
area(shape: None) -> None
Meters(value: float, label: str = 'm')
parse(text: str, default: str = ...) -> Meters  Parse a length like 3m.
VERSION: str = '1.0'
made_later  unresolved: xptshapes binds made_later at import time
extra  module xptshapes.extra  Extra helpers.
Color(value)
  .RED = 'red'
  .BLUE = 'blue'
1 unresolved: not readable from source or stubs. Rerun with --import to read them from the \
imported module, which runs its code.
"""


def test_api_lists_a_package_from_source_and_stubs_without_importing_it(
    shapes: Path, capsys: pytest.CaptureFixture[str]
):
    ran = xpt(capsys, "api", "xptshapes")
    assert (ran.code, ran.err) == (0, "")
    assert ran.out == LISTING
    assert "xptshapes" not in sys.modules


def test_api_import_resolves_what_the_source_binds_at_import_time(
    shapes: Path, capsys: pytest.CaptureFixture[str]
):
    ran = xpt(capsys, "api", "xptshapes", "--import", "--grep", "made")
    assert ran.out == "xptshapes 1.2.3\nmade_later(x, y=2)\n"
    assert "xptshapes" in sys.modules


def test_api_depth_lists_submodules_and_each_name_once(
    shapes: Path, capsys: pytest.CaptureFixture[str]
):
    out = xpt(capsys, "api", "xptshapes", "--depth", "1").out.splitlines()
    module = out.index("module xptshapes.extra  Extra helpers.")
    assert out[module - 1 : module + 2] == [
        "  .BLUE = 'blue'",
        "module xptshapes.extra  Extra helpers.",
        "helper(x, *rest, flag=False, **options)  Help.",
    ]
    assert sum(line.startswith(("Circle(", "Meters(", "parse(")) for line in out) == 3


def test_api_dotted_targets_name_a_class_a_method_or_a_submodule(
    shapes: Path, capsys: pytest.CaptureFixture[str]
):
    assert xpt(capsys, "api", "xptshapes.Circle.describe").out == (
        "xptshapes 1.2.3\nCircle.describe() -> str  Describe the shape.\n"
    )
    assert xpt(capsys, "api", "xptshapes.Circle", "--grep", "unit").out == (
        "xptshapes 1.2.3\n"
        "Circle(name: str, *, tag: str | None = None) -> None  A round shape.\n"
        "  .unit(scale: float = 1.0) -> Circle\n"
    )
    assert xpt(capsys, "api", "xptshapes.extra").out == (
        "xptshapes 1.2.3\nhelper(x, *rest, flag=False, **options)  Help.\n"
    )
    assert xpt(capsys, "api", "xptshapes.Circle._hidden").out == (
        "xptshapes 1.2.3\nCircle._hidden()\n"
    )


def test_api_limit_caps_the_names_and_says_how_many_are_left(
    shapes: Path, capsys: pytest.CaptureFixture[str]
):
    ran = xpt(capsys, "api", "xptshapes", "--limit", "3")
    assert ran.out == (
        "xptshapes 1.2.3\n"
        "Circle(name: str, *, tag: str | None = None) -> None  A round shape.\n"
        "  .radius: float  The radius.\n"
        "  .unit(scale: float = 1.0) -> Circle\n"
        "... 12 more. Use --grep WORD to filter, or --limit 0 to list all.\n"
    )


def test_api_json_keeps_the_full_signature_and_its_parameters(
    shapes: Path, capsys: pytest.CaptureFixture[str]
):
    data = xpt(capsys, "api", "xptshapes", "--json", "--grep", "parse").json
    assert data["command"] == "api"
    assert (data["package"], data["version"], data["stdlib"]) == ("xptshapes", "1.2.3", False)
    [module] = data["modules"]
    assert module["entries"] == [
        {
            "name": "parse",
            "kind": "function",
            "module": "xptshapes.units",
            "resolved": "static",
            "signatures": [
                {
                    "text": "(text: str, default: str = 'a default too long to show') -> Meters",
                    "params": [
                        {
                            "name": "text",
                            "kind": "positional",
                            "annotation": "str",
                            "default": None,
                        },
                        {
                            "name": "default",
                            "kind": "positional",
                            "annotation": "str",
                            "default": "'a default too long to show'",
                        },
                    ],
                    "returns": "Meters",
                }
            ],
            "doc": "Parse a length like 3m.",
        }
    ]
    assert (data["more"], data["unresolved"], data["notes"]) == (0, 0, [])


def test_api_reads_stdlib_targets_by_dotted_name(capsys: pytest.CaptureFixture[str]):
    out = xpt(capsys, "api", "json.dumps").out.splitlines()
    assert out[0].startswith("json stdlib (Python 3.")
    assert out[1].startswith("dumps(obj, *, skipkeys=False, ensure_ascii=True,")
    assert out[1].endswith("  Serialize `obj` to a JSON formatted `str`.")
    assert len(out) == 2
    out = xpt(capsys, "api", "argparse.ArgumentParser.add_argument").out.splitlines()
    assert out[1].startswith("ArgumentParser.add_argument(")
    out = xpt(capsys, "api", "pathlib.Path", "--limit", "0").out.splitlines()
    assert out[1].startswith("Path(")
    for member in ("  .read_text(", "  .with_suffix(suffix)", "  .stem", "  .exists("):
        assert any(line.startswith(member) for line in out), member


def test_api_reads_an_extension_module_only_with_import(capsys: pytest.CaptureFixture[str]):
    out = xpt(capsys, "api", "math").out.splitlines()
    assert len(out) == 2
    assert "can't be read statically. Rerun with --import" in out[1]
    out = xpt(capsys, "api", "math", "--import", "--grep", "sqrt").out.splitlines()
    assert out[1:] == [
        "isqrt(n, /)  Return the integer part of the square root of the input.",
        "sqrt(x, /)  Return the square root of x.",
    ]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            ["xptmissing"],
            "xpt: error: no module xptmissing is installed for ",
        ),
        (
            ["json..x"],
            "xpt: error: 'json..x' is not a dotted Python name, as in json or json.dumps.",
        ),
        (
            ["json.JSONDecoder.nothing"],
            "xpt: error: json.JSONDecoder has no member nothing in its source. "
            "Run xpt api on the class to list its members.",
        ),
    ],
)
def test_api_reports_targets_it_cant_find(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
):
    ran = xpt(capsys, "api", *argv)
    assert ran.code == 2
    assert ran.err.startswith(message)


def test_signature_text_drops_optional_parameters_and_annotations_past_the_width():
    many = tuple(Param(f"option_{i}", "keyword_only", "int", "0") for i in range(10))
    sig = Signature((Param("self_", "positional", "Mapping[str, Sequence[int]]"), *many), "int")
    assert signature_text(sig) == (
        "(self_: Mapping[str, Sequence[int]], *, option_0: int = 0, option_1: int = 0, ...) -> int"
    )
    required = tuple(Param(f"required_{i}", "keyword_only", "dict[str, int]") for i in range(6))
    assert signature_text(Signature(required)) == (
        "(*, required_0: dict[str, int], required_1: dict[str, int], required_2, required_3, "
        "required_4, required_5)"
    )
    posonly = Signature((Param("x", "positional_only"), Param("y", "positional", default="1")))
    assert signature_text(posonly) == "(x, /, y=1)"


def test_annotation_folds_long_subscripts_and_drops_typing_prefixes():
    assert annotation("typing.Optional[str]") == "Optional[str]"
    assert annotation("dict[str, list[tuple[int, int, int]]] | None") == "dict[...] | None"
    assert annotation("x" * 50) == "x" * 37 + "..."


def test_summary_is_the_first_sentence_without_an_echoed_signature():
    assert summary("error(message: string)\n\nPrints a usage message. Then exits.") == (
        "Prints a usage message."
    )
    assert summary("Uses ``code`` here.\nAnd more.") == "Uses `code` here."
    assert summary(None) == ""
    assert summary("word " * 30).endswith("word...")


MORE = {
    "xptmore/__init__.py": """
import sys
from xptmore import parts
from xptmore.parts import *

__all__ = ["Pair", "Options"] + parts.__all__

if sys.version_info >= (3, 0):
    def new(a: int) -> int: ...
else:
    def new(a: str) -> str: ...

__all__ += ["new"]
""",
    "xptmore/parts.py": """
from dataclasses import dataclass
from typing import NamedTuple, TypedDict

__all__ = ["Pair", "Options", "Point"]


class Pair(NamedTuple):
    left: int


@dataclass
class Options:
    debug: bool = False


class Point(TypedDict):
    x: int
""",
    "xptmore-stubs/parts.pyi": """
from dataclasses import KW_ONLY, dataclass
from typing import ClassVar, Literal, NamedTuple, TypedDict

__all__ = ["Pair", "Options", "Point"]

class Pair(NamedTuple):
    left: int
    right: 'list["Pair"]' = []

@dataclass
class Options:
    path: str
    count: ClassVar[int] = 0
    _: KW_ONLY
    debug: bool = False
    mode: Literal["a", "b"] = "a"

class Point(TypedDict):
    x: int
""",
    "xptdynamic.py": """
__all__ = [name for name in ("run",)]


def run(): ...
def _hidden(): ...
""",
}


def test_api_follows_computed_all_stub_packages_and_version_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    for name, text in MORE.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.lstrip("\n"), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert xpt(capsys, "api", "xptmore").out == (
        "xptmore (no installed distribution found)\n"
        "Pair(left: int, right: list[Pair] = [])\n"
        "Options(path: str, *, debug: bool = False, mode: Literal['a', 'b'] = 'a')\n"
        "Point(*, x: int)\n"
        "new(a: int) -> int\n"
    )
    assert xpt(capsys, "api", "xptdynamic").out == (
        "xptdynamic (no installed distribution found)\n"
        "run()\n"
        "xptdynamic.__all__ is computed at import time, so this lists the names without a "
        "leading underscore. Rerun with --import to read __all__.\n"
    )
    assert "xptmore" not in sys.modules
