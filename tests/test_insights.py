import copy
import pytest
from backend.db import Session, Paper, PaperCategories
from backend import insights, agents, workflows, sources
from backend.tasks import reserve_call
from backend.worker import tick
from backend.reading import research_pages, merge_techniques


def seed(years=(2022, 2024), node="prefetch", text=True):
    ids = []
    with Session.begin() as s:
        for i, year in enumerate(years):
            p = Paper(
                identity=f"paper:{node}:{i}",
                title=f"Prefetch paper {i}",
                year=year,
                abstract="A mechanism uses temporal history to predict future memory access."
                if text
                else "",
                venue="TEST",
                reading_level="abstract" if text else "metadata",
            )
            s.add(p)
            s.flush()
            s.add(
                PaperCategories(paper_id=p.id, node_ids=[node, "bandwidth"], revision=1)
            )
            ids.append(p.id)
    return ids


def content(src):
    return {
        "title": "技术报告",
        "sections": [
            {
                "heading": "技术路线",
                "points": [
                    {
                        "text": "利用历史信息进行预取",
                        "citations": [src[0]["id"]],
                        "basis": "observed",
                    }
                ],
            }
        ],
        "timeline": [
            {
                "year": s["year"],
                "text": "该年份文献观察",
                "citations": [s["id"]],
                "basis": "observed",
            }
            for s in src
            if s.get("year")
        ],
        "limitations": ["样本有限"],
    }


def fake_report(monkeypatch):
    monkeypatch.setattr(insights, "model_ready", lambda: True)

    def call(job, kind, name, src, notes=None):
        reserve_call(job)
        return insights.validate_report(content(src), src)

    monkeypatch.setattr(insights, "report_call", call)


def test_subtree_manual_scope_and_metadata_exclusion(client):
    seed()
    seed((2023,), node="compiler")
    seed((2020,), node="replacement", text=False)
    r = client.get("/api/taxonomy/arch/report-scope").json()
    assert (
        r["stats"]["matched"] == 3
        and r["stats"]["readable"] == 2
        and r["stats"]["metadata_only"] == 1
    )
    assert (
        client.get("/api/taxonomy/bandwidth/report-scope").json()["stats"]["matched"]
        == 4
    )


def test_reports_history_citations_export_staleness(client, monkeypatch):
    ids = seed()
    fake_report(monkeypatch)
    for _ in range(2):
        j = client.post(
            "/api/reports",
            json={"node_id": "arch", "report_kind": "trends", "max_calls": 2},
        ).json()
        tick()
        job = client.get("/api/jobs/" + j["id"]).json()
        assert job["status"] == "succeeded"
        assert "snapshot" not in job["payload"]
        rid = job["result"]["report_id"]
    assert len(client.get("/api/reports?node_id=arch").json()) == 2
    report = client.get("/api/reports/" + rid).json()
    assert not report["stale"] and len(report["content"]["timeline"]) == 2
    assert report["snapshot"]["sources"][0]["paper_id"] in ids
    assert "[S001]" in client.get("/api/reports/" + rid + "/export").text
    with Session.begin() as s:
        s.get(Paper, ids[0]).abstract += " New findings."
    assert client.get("/api/reports/" + rid).json()["stale"]


def test_empty_scope_and_invalid_dates(client):
    assert client.post("/api/reports", json={"node_id": "arch"}).status_code == 422
    assert client.post("/api/reports", json={"node_id": "absent"}).status_code == 404
    assert (
        client.post(
            "/api/reports", json={"node_id": "arch", "year_from": 2026, "year_to": 2020}
        ).status_code
        == 422
    )


def test_unknown_citations_and_wrong_timeline_year():
    src = [{"id": "S001", "year": 2022}]
    data = content(src)
    data["sections"][0]["points"][0]["citations"] = ["invented"]
    with pytest.raises(ValueError, match="引用"):
        insights.validate_report(data, src)
    data = content(src)
    data["timeline"][0]["year"] = 2024
    with pytest.raises(ValueError, match="年份"):
        insights.validate_report(data, src)


def test_snapshot_is_frozen_and_small_sample_warning(client, monkeypatch):
    ids = seed((2024,))
    fake_report(monkeypatch)
    j = client.post(
        "/api/reports", json={"node_id": "prefetch", "report_kind": "trends"}
    ).json()
    with Session.begin() as s:
        s.get(Paper, ids[0]).abstract = "Changed after submission"
    tick()
    job = client.get("/api/jobs/" + j["id"]).json()
    r = client.get("/api/reports/" + job["result"]["report_id"]).json()
    assert "Changed" not in r["snapshot"]["sources"][0]["text"]
    assert any("不足两个" in w for w in r["warnings"]) and r["stale"]


def test_sampling_preserves_early_and_recent_years(client):
    seed((2000, 2010, 2020, 2025))
    with Session() as s:
        snap = insights.snapshot(s, "arch", max_papers=2)
    assert [x["year"] for x in snap["sources"]] == [2000, 2025]
    assert snap["stats"]["omitted"] == 2


def test_report_cancel_and_no_model(client):
    seed()
    j = client.post("/api/reports", json={"node_id": "arch"}).json()
    client.post("/api/jobs/" + j["id"] + "/cancel")
    assert not tick()
    j = client.post("/api/reports", json={"node_id": "arch"}).json()
    tick()
    assert client.get("/api/jobs/" + j["id"]).json()["status"] == "failed"
    assert client.get("/api/reports").json() == []


def test_reference_filter_preserves_body_and_page():
    pages = [
        {"page": 1, "text": "Our mechanism references prior work."},
        {
            "page": 2,
            "text": "Conclusion\nUseful conclusion.\n7 REFERENCES\n[1] Unrelated technique",
        },
        {"page": 3, "text": "[2] More citations"},
    ]
    body, cut = research_pages(pages)
    assert cut and len(body) == 2 and body[1]["page"] == 2
    assert "Useful" in body[1]["text"] and "[1]" not in body[1]["text"]


def test_evidence_preserving_merge():
    t = {
        "name": "Prophet",
        "mechanism": "Mechanism",
        "problem": "Bandwidth",
        "conditions": "Condition",
        "limitations": "None",
        "evidence": [{"block_id": "b1", "quote": "Original evidence"}],
        "classifications": [
            {"node_id": "prefetch", "confidence": 0.8, "reason": "Evidence"}
        ],
    }
    u = copy.deepcopy(t)
    u["evidence"] = [{"block_id": "b2", "quote": "Another evidence"}]
    u["classifications"][0]["confidence"] = 0.9
    merged = merge_techniques(
        [{"summary": "Summary", "techniques": [t, u], "proposals": []}]
    )
    assert (
        len(merged["techniques"]) == 1 and len(merged["techniques"][0]["evidence"]) == 2
    )
    assert merged["techniques"][0]["classifications"][0]["confidence"] == 0.9


def test_consolidation_rejects_lost_indices(monkeypatch):
    monkeypatch.setattr(agents, "call", lambda *a: {"groups": [[0]]})
    with pytest.raises(ValueError, match="完整"):
        agents.consolidate(
            "x",
            [
                {
                    "techniques": [
                        {"name": "A", "mechanism": "A", "problem": "A"},
                        {"name": "B", "mechanism": "B", "problem": "B"},
                    ]
                }
            ],
        )


def test_openalex_truncation_not_counted_as_filter(monkeypatch):
    monkeypatch.setattr(sources, "OPENALEX_MAX", 2)
    monkeypatch.setattr(sources, "resolve_venue", lambda v: {"name": v, "id": "S1"})
    monkeypatch.setattr(
        sources,
        "_openalex",
        lambda *a: {
            "results": [
                {"title": "Sponsors"},
                {"title": "A"},
                {"title": "B"},
                {"title": "C"},
            ],
            "meta": {"count": 10, "next_cursor": "next"},
        },
    )
    monkeypatch.setattr(sources, "openalex_item", lambda x: x)
    rows, info = sources.openalex_venue("HPCA")
    assert (
        len(rows) == 2
        and info["truncated"]
        and info["filtered_out"] == 1
        and info["processed_records"] == 3
    )


def test_rank_requires_all_candidates(monkeypatch):
    monkeypatch.setattr(
        agents, "call", lambda *a: {"ranking": [{"index": 0, "score": 0.9}]}
    )
    with pytest.raises(ValueError, match="全部"):
        agents.rank("x", "cache", [{"title": "A"}, {"title": "B"}])


def test_search_semantic_threshold(client, monkeypatch):
    monkeypatch.setattr(workflows, "model_ready", lambda: True)
    monkeypatch.setattr(agents, "plan", lambda *a: ["cache"])
    monkeypatch.setattr(
        sources,
        "crossref",
        lambda *a, **kw: [
            {"title": "Processor cache", "doi": "10.1/a", "source": "crossref"},
            {"title": "Network content cache", "doi": "10.1/b", "source": "crossref"},
        ],
    )
    monkeypatch.setattr(
        agents, "rank", lambda *a: {0: (0.9, "processor"), 1: (0.1, "network")}
    )
    j = client.post(
        "/api/search",
        json={
            "topic": "processor cache",
            "sources": ["crossref"],
            "min_relevance": 0.5,
            "limit": 2,
        },
    ).json()
    tick()
    r = client.get("/api/jobs/" + j["id"]).json()["result"]
    assert (
        len(r["selected"]) == 1
        and r["semantic_threshold_applied"]
        and r["excluded_or_unscored"] == 1
    )


def test_report_batches_partial_budget(client, monkeypatch):
    seed((2020, 2021, 2022))
    fake_report(monkeypatch)
    monkeypatch.setattr(insights, "batches_for", lambda src: [[p] for p in src])
    j = client.post("/api/reports", json={"node_id": "arch", "max_calls": 2}).json()
    tick()
    job = client.get("/api/jobs/" + j["id"]).json()
    assert job["status"] == "partial" and job["result"]["generated_papers"] == 1


def test_acp_ignores_stale_session_notifications():
    from backend.acp_client import AcpProcess, AcpError

    p = AcpProcess.__new__(AcpProcess)
    p._session_id = "current"
    p._capture = {"parts": [], "tool_calls": 0}
    msg = {
        "method": "session/update",
        "params": {
            "sessionId": "stale",
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "wrong session"},
            },
        },
    }
    p._handle_notification(msg)
    assert p._capture["parts"] == []
    msg["params"]["sessionId"] = "current"
    p._handle_notification(msg)
    assert p._capture["parts"] == ["wrong session"]
    msg["params"]["update"] = {"sessionUpdate": "tool_call"}
    with pytest.raises(AcpError, match="不允许工具"):
        p._handle_notification(msg)


def test_acp_explicit_model_cannot_silently_fall_back():
    from backend.acp_client import AcpProcess, AcpError

    p = AcpProcess.__new__(AcpProcess)
    p.cwd = "."
    p.request = lambda *a, **kw: {"sessionId": "s"}
    with pytest.raises(AcpError, match="模型配置"):
        p.new_session("required-model")


def test_acp_command_handles_quoted_paths(monkeypatch, tmp_path):
    from backend.acp_client import _command
    import sys

    file = tmp_path / "fake agent.py"
    file.write_text("")
    monkeypatch.setenv("ACP_COMMAND", f'"{sys.executable}" "{file}"')
    assert _command()[1] == str(file)
