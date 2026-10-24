import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DATA = Path(os.getenv("WORKBENCH_DATA", str(ROOT / "data"))).resolve()
FILES = DATA / "files"
FILES.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///" + str(DATA / "workbench.db"))
MAX_FILE = 25 * 1024 * 1024


def model_config():
    return {
        k: os.getenv(k, "").strip()
        for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")
    }


def model_ready():
    c = model_config()
    return bool(c["LLM_BASE_URL"] and c["LLM_MODEL"])
