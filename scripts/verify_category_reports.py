"""Bounded real OpenCode ACP validation against an isolated knowledge snapshot."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        from backend.db import init_db, Session, Job, CategoryReport
        from backend.tasks import submit
        from backend.worker import tick
        from backend import acp_client

        init_db()
        data = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        results = []
        try:
            for kind in ["summary", "trends"]:
                j = submit(
                    "report",
                    {
                        "node_id": data["node_id"],
                        "report_kind": kind,
                        "snapshot": data,
                        "year_from": None,
                        "year_to": None,
                        "max_papers": 3,
                        "max_calls": 3,
                    },
                )
                tick()
                with Session() as s:
                    job = s.get(Job, j["id"])
                    r = s.get(CategoryReport, job.result.get("report_id", ""))
                    results.append(
                        {
                            "kind": kind,
                            "status": job.status,
                            "calls": job.calls_used,
                            "tokens": job.tokens_used,
                            "error": job.error,
                            "report_id": r.id if r else None,
                            "title": r.content["title"] if r else None,
                            "sections": len(r.content["sections"]) if r else 0,
                            "timeline_entries": len(r.content.get("timeline", []))
                            if r
                            else 0,
                        }
                    )
                print(json.dumps(results[-1], ensure_ascii=True), flush=True)
        finally:
            acp_client.shutdown()
        Path(sys.argv[2]).with_name("results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if any(not r["report_id"] for r in results):
            raise SystemExit(1)
        return
    from backend.config import ROOT, agent_config
    from backend.db import Session, Node
    from backend.insights import snapshot

    with Session() as s:
        node = s.get(Node, "prefetch") or s.get(Node, "arch")
        snap = snapshot(s, node.id, max_papers=3)
    if not snap["sources"]:
        raise SystemExit("没有可读且已分类的论文样本")
    validation_root = ROOT / "data" / "validation"
    validation_root.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="category-acp-check-", dir=validation_root))
    input_path = temp / "snapshot.json"
    input_path.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + str(temp / "check.db")
    env["WORKBENCH_DATA"] = str(temp / "data")
    cfg = agent_config()
    env["ACP_COMMAND"] = cfg["command"]
    env["ACP_MODEL"] = cfg["model"]
    env["ACP_HOME"] = str((ROOT / cfg["home"]).resolve()) if cfg["home"] else ""
    print(
        json.dumps(
            {
                "validation_directory": str(temp),
                "node": snap["node_name"],
                "papers": len(snap["sources"]),
                "model": cfg["model"],
            },
            ensure_ascii=True,
        ),
        flush=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.verify_category_reports",
            "--child",
            str(input_path),
        ],
        env=env,
        cwd=ROOT,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
