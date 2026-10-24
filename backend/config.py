import os
import shlex
import shutil
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DATA = Path(os.getenv("WORKBENCH_DATA", str(ROOT / "data"))).resolve()
FILES = DATA / "files"
FILES.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///" + str(DATA / "workbench.db"))
MAX_FILE = 25 * 1024 * 1024


def agent_config():
    """ACP agent invocation. Model and credentials live in the agent config."""
    try:
        timeout = float(os.getenv("ACP_TIMEOUT", "300"))
    except ValueError:
        timeout = 300.0
    return {
        "command": os.getenv("ACP_COMMAND", "opencode acp").strip(),
        "model": os.getenv("ACP_MODEL", "").strip(),
        "home": os.getenv("ACP_HOME", "").strip(),
        "timeout": timeout,
    }


def agent_model():
    return agent_config()["model"] or "agent-default"


def model_ready():
    """True when the configured ACP agent executable can be located."""
    command = agent_config()["command"]
    if not command:
        return False
    try:
        first = shlex.split(command, posix=False)[0].strip('"')
    except ValueError:
        return False
    return bool(shutil.which(first) or Path(first).exists())
