import os
import sys
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from harness_eol.data import load_variant  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "req(*ids): SPEC requirement ids this test verifies")
    config.addinivalue_line("markers", "it(id): SPEC integration test id")


@pytest.fixture(scope="session")
def cmd():
    return load_variant("cmd")


@pytest.fixture(scope="session")
def base():
    return load_variant("base")
