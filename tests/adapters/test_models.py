import pytest
from support import assert_rebuilds

from xpt import expect
from xpt.serialize import UnsupportedValue, serialize


def test_a_pydantic_model_is_written_by_its_fields_and_aliases():
    pydantic = pytest.importorskip("pydantic")

    class Inner(pydantic.BaseModel):
        n: int

    class Outer(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(extra="allow")
        name: str = pydantic.Field(alias="Name")
        inner: Inner

    obj = Outer.model_validate({"Name": "a", "inner": {"n": 1}, "more": [2]})
    names = {f"{__name__}:{c.__qualname__}": c.__name__ for c in (Inner, Outer)}
    source = assert_rebuilds(obj, names, {"Inner": Inner, "Outer": Outer})
    assert source == 'Outer(Name="a", inner=Inner(n=1), more=[2])'


def test_an_attrs_class_is_written_by_its_init_aliases():
    attrs = pytest.importorskip("attrs")

    @attrs.define
    class Box:
        _size: int
        label: str = "x"

    names = {f"{__name__}:{Box.__qualname__}": "Box"}
    source = assert_rebuilds(Box(3), names, {"Box": Box})
    assert source == 'Box(size=3, label="x")'


def test_an_attrs_class_without_eq_is_unsupported():
    attrs = pytest.importorskip("attrs")

    @attrs.define(eq=False)
    class Box:
        size: int

    with pytest.raises(UnsupportedValue, match="identity"):
        serialize(Box(1))


def test_a_protobuf_message_lists_only_its_set_fields():
    descriptor_pb2 = pytest.importorskip("google.protobuf.descriptor_pb2")
    message = descriptor_pb2.DescriptorProto(name="M")
    message.field.add(name="f", number=1)
    names = {
        "google.protobuf.descriptor_pb2:DescriptorProto": "pb.DescriptorProto",
        "google.protobuf.descriptor_pb2:FieldDescriptorProto": "pb.FieldDescriptorProto",
    }
    source = assert_rebuilds(message, names, {"pb": descriptor_pb2})
    assert (
        source
        == 'pb.DescriptorProto(name="M", field=[pb.FieldDescriptorProto(name="f", number=1)])'
    )


def test_a_protobuf_field_set_on_one_side_reads_as_added():
    descriptor_pb2 = pytest.importorskip("google.protobuf.descriptor_pb2")
    with pytest.raises(AssertionError) as raised:
        expect(descriptor_pb2.DescriptorProto(name="M"), descriptor_pb2.DescriptorProto())
    assert "value.name" in str(raised.value)
