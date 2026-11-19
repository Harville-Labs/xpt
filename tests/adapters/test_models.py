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


def note_extensions():
    descriptor_pb2 = pytest.importorskip("google.protobuf.descriptor_pb2")
    from google.protobuf import descriptor_pool

    pool = descriptor_pool.Default()
    try:
        return pool.FindExtensionByName("xpt_test.note"), pool.FindExtensionByName("xpt_test.tags")
    except KeyError:
        pass
    field = descriptor_pb2.FieldDescriptorProto
    file = descriptor_pb2.FileDescriptorProto(
        name="xpt_test.proto",
        package="xpt_test",
        dependency=["google/protobuf/descriptor.proto"],
        syntax="proto2",
    )
    for name, number, label, kind in [
        ("note", 50001, field.LABEL_OPTIONAL, field.TYPE_STRING),
        ("tags", 50002, field.LABEL_REPEATED, field.TYPE_INT32),
    ]:
        file.extension.add(
            name=name,
            number=number,
            label=label,
            type=kind,
            extendee=".google.protobuf.FieldOptions",
        )
    pool.AddSerializedFile(file.SerializeToString())
    return pool.FindExtensionByName("xpt_test.note"), pool.FindExtensionByName("xpt_test.tags")


def test_protobuf_extensions_are_written_through_expect_with_extensions():
    descriptor_pb2 = pytest.importorskip("google.protobuf.descriptor_pb2")
    note, tags = note_extensions()
    options = descriptor_pb2.FieldOptions(deprecated=True)
    options.Extensions[note] = "hi"
    options.Extensions[tags].extend([1, 2])
    names = {
        "google.protobuf.descriptor_pb2:FieldOptions": "pb.FieldOptions",
        "xpt:expect.with_extensions": "expect.with_extensions",
    }
    source = assert_rebuilds(options, names, {"pb": descriptor_pb2, "expect": expect})
    assert source == (
        "expect.with_extensions(\n"
        "    pb.FieldOptions(deprecated=True),\n"
        '    extensions={"xpt_test.note": "hi", "xpt_test.tags": [1, 2]},\n'
        ")"
    )
