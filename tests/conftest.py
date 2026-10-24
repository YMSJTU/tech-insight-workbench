import atexit
import os
import shutil
import tempfile
from pathlib import Path
import pytest

TEST_ROOT = Path(tempfile.mkdtemp(prefix="paper-workbench-tests-"))
os.environ["DATABASE_URL"] = "sqlite:///" + str(TEST_ROOT / "test.db")
os.environ["WORKBENCH_DATA"] = str(TEST_ROOT / "data")
os.environ["ACP_COMMAND"] = ""
os.environ["ACP_MODEL"] = ""
os.environ["ACP_HOME"] = ""

from backend.db import Base, engine, init_db


def _cleanup():
    engine.dispose()
    shutil.rmtree(TEST_ROOT, ignore_errors=True)


atexit.register(_cleanup)


@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    init_db()
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.app import app

    with TestClient(app) as c:
        yield c
