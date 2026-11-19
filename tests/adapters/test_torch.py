import pytest
from support import assert_rebuilds

from xpt import expect
from xpt.equal import equal

torch = pytest.importorskip("torch")

NAMES = {"torch:tensor": "torch.tensor", "torch:zeros": "torch.zeros"}
SCOPE = {"torch": torch}


def names(*dtypes: str) -> dict[str, str]:
    return NAMES | {f"torch:{d}": f"torch.{d}" for d in dtypes}


def test_a_tensor_is_written_with_a_torch_dtype():
    source = assert_rebuilds(torch.tensor([[0.1, 2.0]]), names("float32"), SCOPE)
    assert source == "torch.tensor([[0.1, 2.0]], dtype=torch.float32)"


def test_integer_bool_and_half_tensors_rebuild():
    assert_rebuilds(torch.tensor([1, 2]), names("int64"), SCOPE)
    assert_rebuilds(torch.tensor([True]), names("bool"), SCOPE)
    assert_rebuilds(torch.tensor([0.1], dtype=torch.float16), names("float16"), SCOPE)


def test_a_tensor_that_requires_grad_is_recorded_by_its_values():
    assert_rebuilds(torch.tensor([1.0], requires_grad=True), names("float32"), SCOPE)


def test_an_empty_tensor_keeps_its_shape():
    source = assert_rebuilds(torch.zeros((2, 0)), names("float32"), SCOPE)
    assert source == "torch.zeros((2, 0), dtype=torch.float32)"


def test_equal_tensors_pass_and_dtype_or_shape_differences_fail():
    expect(torch.tensor([1, 2]), torch.tensor([1, 2]))
    assert not equal(torch.tensor([1]), torch.tensor([1.0]))
    assert not equal(torch.tensor([1]), torch.tensor([[1]]))


def test_a_mismatch_points_at_the_differing_element():
    with pytest.raises(AssertionError) as raised:
        expect(torch.tensor([1, 2, 3]), torch.tensor([1, 9, 3]))
    assert "value[1]" in str(raised.value)


def test_nan_elements_in_the_same_place_are_equal():
    expect(torch.tensor([float("nan"), 1.0]), torch.tensor([float("nan"), 1.0]))


def test_a_complex_tensor_rebuilds():
    tensor = torch.tensor([1 + 2j], dtype=torch.complex64)
    source = assert_rebuilds(tensor, names("complex64") | {"builtins:complex": "complex"}, SCOPE)
    assert source == "torch.tensor([complex(real=1.0, imag=2.0)], dtype=torch.complex64)"


def test_a_tensor_off_the_cpu_records_its_device_and_compares_it():
    if not torch.backends.mps.is_available():
        pytest.skip("needs a non-CPU device; this machine has only the CPU")
    tensor = torch.tensor([1.0], device="mps")
    source = assert_rebuilds(tensor, names("float32"), SCOPE)
    assert source == 'torch.tensor([1.0], dtype=torch.float32, device="mps:0")'
    assert not equal(tensor, tensor.cpu())
