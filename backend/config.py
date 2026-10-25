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


def unpaywall_email():
    return os.getenv("UNPAYWALL_EMAIL", "").strip()


def pdf_settings():
    try:
        candidates = int(os.getenv("PDF_MAX_CANDIDATES", "3"))
    except ValueError:
        candidates = 3
    try:
        dpi = int(os.getenv("PDF_RENDER_DPI", "110"))
    except ValueError:
        dpi = 110
    return {
        "max_candidates": min(5, max(1, candidates)),
        "render_dpi": min(160, max(72, dpi)),
    }
