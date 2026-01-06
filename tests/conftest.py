# tests/conftest.py

import os
from pathlib import Path


def pytest_ignore_collect(collection_path: Path, config):
    """
    Prevent collection of Dynamo/Revit integration tests unless explicitly enabled.

    Enable by setting:
        VOP_RUN_DYNAMO_TESTS=1
    """
    run_dynamo = os.environ.get("VOP_RUN_DYNAMO_TESTS", "").strip() == "1"
    if run_dynamo:
        return False

    p = str(collection_path).replace("\\", "/")
    return "/tests/dynamo/" in p
