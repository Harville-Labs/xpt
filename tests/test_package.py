import importlib.metadata


def test_package_has_no_runtime_dependencies():
    assert importlib.metadata.requires("xpt") is None
