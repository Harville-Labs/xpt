import importlib.metadata


def test_package_has_no_runtime_dependencies():
    assert importlib.metadata.requires("xpt") is None


def test_the_api_module_exports_every_name_it_lists():
    from xpt import api

    assert all(hasattr(api, name) for name in api.__all__)
    assert {"load_run", "group", "accept", "Group", "Outcome", "AcceptResult"} <= set(api.__all__)
