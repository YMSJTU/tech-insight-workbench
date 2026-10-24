"""Run a real search/archive smoke check in an isolated temporary database, without paid model calls."""

import json
import os
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="workbench-live-check-"))
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "verify.db")
os.environ["WORKBENCH_DATA"] = str(root / "data")
os.environ["LLM_BASE_URL"] = ""
os.environ["LLM_MODEL"] = ""
os.environ["LLM_API_KEY"] = ""

from backend.db import init_db, Session, Job, Paper, Document
from backend.tasks import submit
from backend.worker import tick
from sqlalchemy import select

init_db()
job = submit(
    "search",
    {"topic": "cache compression", "sources": ["arxiv"], "limit": 1, "max_calls": 0},
)
tick()
tick()
with Session() as s:
    parent = s.get(Job, job["id"])
    papers = s.scalars(select(Paper)).all()
    docs = s.scalars(select(Document)).all()
    children = s.scalars(select(Job).where(Job.parent_id == job["id"])).all()
    report = {
        "isolated_database": str(root / "verify.db"),
        "parent_status": parent.status,
        "paper_count": len(papers),
        "document_count": len(docs),
        "papers": [
            {"title": p.title, "reading_level": p.reading_level} for p in papers
        ],
        "warnings": parent.result.get("warnings", [])
        + [w for j in children for w in j.result.get("warnings", [])],
        "paid_model_calls": parent.calls_used,
    }
print(json.dumps(report, ensure_ascii=True, indent=2))
if not papers:
    raise SystemExit(1)
