import os
import tempfile
from pathlib import Path
import pytest

TEST_ROOT = Path(tempfile.mkdtemp(prefix="paper-workbench-tests-"))
os.environ["DATABASE_URL"] = "sqlite:///" + str(TEST_ROOT / "test.db")
os.environ["WORKBENCH_DATA"] = str(TEST_ROOT / "data")
os.environ["LLM_BASE_URL"] = ""
os.environ["LLM_MODEL"] = ""
os.environ["LLM_API_KEY"] = ""

from backend.db import Base, engine, init_db


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
