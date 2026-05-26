import importlib


def test_import_api_test_kit():
    module = importlib.import_module("api_test_kit")
    assert hasattr(module, "main")
