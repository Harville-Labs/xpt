import json

from xpt.equal import equal
from xpt.serialize import serialize
from xpt.source import to_source
from xpt.value import from_json, to_json


def rebuilt(obj: object, names: dict[str, str], scope: dict[str, object]) -> object:
    value = from_json(json.loads(json.dumps(to_json(serialize(obj)))))
    assert value == serialize(obj)
    return eval(to_source(value, names=names), scope)


def assert_rebuilds(obj: object, names: dict[str, str], scope: dict[str, object]) -> str:
    source = to_source(serialize(obj), names=names)
    assert equal(rebuilt(obj, names, scope), obj), source
    return source
