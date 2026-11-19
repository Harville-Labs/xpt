import pytest
from cli_support import xpt

pytest.importorskip("numpy")
pytest.importorskip("torch")

SOURCE = """\
import numpy as np
import torch

from xpt import expect


def test_array():
    expect(np.arange(4, dtype=np.float32).reshape(2, 2) / 4)


def test_tensor():
    expect(torch.tensor([1, 2]) * 2, torch.tensor([2, 4], dtype=torch.int32))
"""


def test_accept_writes_arrays_and_tensors_that_then_pass(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    (pytester.path / "test_it.py").write_text(SOURCE, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(failed=2)
    ran = xpt(capsys, "accept", "--all")
    assert ran.code == 0, ran
    text = (pytester.path / "test_it.py").read_text(encoding="utf-8")
    assert 'np.array([[0.0, 0.25], [0.5, 0.75]], dtype="float32")' in text
    assert "torch.tensor([2, 4], dtype=torch.int64)" in text
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=2)
