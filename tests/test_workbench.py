import time
from sqlalchemy import update
from backend.db import Session, Proposal, Job
from backend.tasks import submit, claim, reserve_call, BudgetExceeded, check, Cancelled
from backend.worker import tick
from backend import workflows, agents, sources
import pytest

ABSTRACT = "We propose a cache compression mechanism that reduces memory bandwidth consumption. The evaluation reports improved access efficiency for the studied workloads."


def output(blocks, nodes):
    return {
        "summary": "论文提出 Cache 压缩方法。",
        "techniques": [
            {
                "name": "Cache 压缩",
                "mechanism": "通过压缩减少访存带宽消耗。",
                "problem": "带宽消耗",
                "conditions": "研究中的工作负载",
                "limitations": "其他工作负载未说明",
                "evidence": [
                    {"block_id": blocks[0]["id"], "quote": blocks[0]["text"][:85]}
                ],
                "classifications": [
                    {
                        "node_id": "cache-compression",
                        "confidence": 0.9,
                        "reason": "原文描述压缩机制",
                    },
                    {
                        "node_id": "bandwidth",
                        "confidence": 0.8,
                        "reason": "目标为减少带宽消耗",
                    },
                ],
            }
        ],
        "proposals": [],
    }


def mock_ai(monkeypatch):
    monkeypatch.setattr(workflows, "model_ready", lambda: True)
    monkeypatch.setattr(workflows, "agent_model", lambda: "test-model")

    def extract(job_id, blocks, nodes):
        return agents.validate(output(blocks, nodes), blocks, nodes)

    monkeypatch.setattr(agents, "extract", extract)


def import_one(client, monkeypatch, **extra):
    mock_ai(monkeypatch)
    j = client.post(
        "/api/archive",
        json={
            "title": "Cache paper",
            "abstract": ABSTRACT,
            "venue": "TEST",
            "year": 2025,
            **extra,
        },
    ).json()
    assert tick()
    return client.get("/api/jobs/" + j["id"]).json()


def test_no_model_preserves_metadata_and_document(client):
    j = client.post("/api/archive", json={"title": "Test", "abstract": ABSTRACT}).json()
    tick()
    result = client.get("/api/jobs/" + j["id"]).json()
    assert result["status"] == "partial"
    assert "agent_not_configured" in result["result"]["warnings"][-1]
    paper = client.get("/api/papers/" + result["result"]["paper_id"]).json()
    assert (
        paper["reading_level"] == "abstract"
        and not paper["techniques"]
        and paper["documents"]
    )


def test_full_archive_evidence_and_three_dimensions(client, monkeypatch):
    j = import_one(client, monkeypatch)
    assert j["status"] == "succeeded"
    p = client.get("/api/papers/" + j["result"]["paper_id"]).json()
    assert p["techniques"][0]["name"] == "Cache 压缩"
    assert p["evidence"][0]["quote"] in ABSTRACT
    assert (
        client.get("/api/papers?technology=arch&problem=performance&venue=TEST").json()[
            "total"
        ]
        == 1
    )
    assert client.get("/api/papers?technology=compiler").json()["total"] == 0
    assert client.get("/api/papers?q=压缩").json()["total"] == 1


def test_duplicate_doi_no_duplicate_paper(client, monkeypatch):
    import_one(client, monkeypatch, doi="https://doi.org/10.123/test")
    import_one(client, monkeypatch, doi="10.123/TEST")
    assert client.get("/api/papers").json()["total"] == 1


def test_failed_reprocessing_does_not_replace_effective_run(client, monkeypatch):
    j = import_one(client, monkeypatch)
    pid = j["result"]["paper_id"]
    old = client.get("/api/papers/" + pid).json()["current_run_id"]
    monkeypatch.setattr(
        agents,
        "extract",
        lambda *args: (_ for _ in ()).throw(ValueError("invalid evidence")),
    )
    again = client.post("/api/papers/" + pid + "/reprocess").json()
    tick()
    assert client.get("/api/jobs/" + again["id"]).json()["status"] == "partial"
    assert client.get("/api/papers/" + pid).json()["current_run_id"] == old


def test_evidence_must_be_verbatim():
    blocks = [{"id": "b1", "page": 1, "text": ABSTRACT}]
    nodes = [{"id": "cache-compression"}, {"id": "bandwidth"}]
    data = output(blocks, nodes)
    data["techniques"][0]["evidence"][0]["quote"] = (
        "This text does not exist in the source"
    )
    with pytest.raises(ValueError, match="逐字"):
        agents.validate(data, blocks, nodes)


def test_evidence_allows_pdf_whitespace_differences():
    blocks = [
        {
            "id": "b1",
            "page": 1,
            "text": "A long line with\nhyphen-\nation and  spaces.",
        }
    ]
    nodes = [
        {"id": "cache-compression", "dimension": "technology"},
        {"id": "bandwidth", "dimension": "problem"},
    ]
    data = output(blocks, nodes)
    data["techniques"][0]["evidence"][0]["quote"] = (
        "A long line with hyphenation and spaces."
    )
    assert agents.validate(data, blocks, nodes)


def test_proposal_parent_dimension_validation():
    blocks = [{"id": "b1", "page": 1, "text": ABSTRACT}]
    nodes = [
        {"id": "cache-compression", "dimension": "technology"},
        {"id": "bandwidth", "dimension": "problem"},
    ]
    data = output(blocks, nodes)
    data["proposals"] = [
        {"dimension": "problem", "parent_id": "cache-compression", "name": "Wrong"}
    ]
    with pytest.raises(ValueError, match="维度"):
        agents.validate(data, blocks, nodes)


def test_cancellation_and_retry(client):
    j = client.post(
        "/api/archive", json={"title": "Cancel", "abstract": ABSTRACT}
    ).json()
    assert client.post("/api/jobs/" + j["id"] + "/cancel").status_code == 200
    assert not tick()
    assert client.get("/api/jobs/" + j["id"]).json()["status"] == "cancelled"
    retry = client.post("/api/jobs/" + j["id"] + "/retry")
    assert retry.status_code == 202 and retry.json()["id"] != j["id"]


def test_lease_fencing_and_recovery():
    submit("archive", {"title": "Recover"})
    id_, old = claim()
    with Session.begin() as s:
        s.execute(update(Job).where(Job.id == id_).values(lease_until=time.time() - 1))
    new_id, new = claim()
    assert new_id == id_ and new != old
    with Session.begin() as s:
        with pytest.raises(Cancelled):
            check(s, id_, old)
        check(s, id_, new)


def test_parent_budget_shared():
    parent = submit("search", {"max_calls": 1})
    a = submit("archive", {}, parent["id"])
    b = submit("archive", {}, parent["id"])
    reserve_call(a["id"])
    with pytest.raises(BudgetExceeded):
        reserve_call(b["id"])
    with Session() as s:
        assert s.get(Job, parent["id"]).calls_used == 1


def test_search_to_archive_end_to_end(client, monkeypatch):
    mock_ai(monkeypatch)
    monkeypatch.setattr(agents, "plan", lambda *args: ["cache compression"])
    fixture = {
        "title": "Cache candidate",
        "doi": "10.123/candidate",
        "abstract": ABSTRACT,
        "venue": "CONF",
        "year": 2025,
        "source": "crossref",
    }
    monkeypatch.setattr(sources, "crossref", lambda *args, **kwargs: [fixture])
    monkeypatch.setattr(
        sources, "arxiv", lambda *args, **kwargs: [{**fixture, "source": "arxiv"}]
    )
    j = client.post("/api/search", json={"topic": "cache compression"}).json()
    tick()
    parent = client.get("/api/jobs/" + j["id"]).json()
    assert parent["status"] == "waiting" and len(parent["children"]) == 1
    tick()
    parent = client.get("/api/jobs/" + j["id"]).json()
    assert parent["status"] == "succeeded"
    assert client.get("/api/papers").json()["total"] == 1


def test_search_partial_source_failure(client, monkeypatch):
    monkeypatch.setattr(sources, "crossref", lambda *a, **k: [])
    monkeypatch.setattr(
        sources, "arxiv", lambda *a, **k: (_ for _ in ()).throw(ValueError("down"))
    )
    j = client.post("/api/search", json={"topic": "cache compression"}).json()
    tick()
    assert client.get("/api/jobs/" + j["id"]).json()["status"] == "partial"


def test_search_empty_success(client, monkeypatch):
    monkeypatch.setattr(sources, "crossref", lambda *a, **k: [])
    j = client.post(
        "/api/search", json={"topic": "no matches", "sources": ["crossref"]}
    ).json()
    tick()
    assert client.get("/api/jobs/" + j["id"]).json()["status"] == "succeeded"


def test_all_sources_failed(client, monkeypatch):
    monkeypatch.setattr(
        sources, "crossref", lambda *a, **k: (_ for _ in ()).throw(ValueError("down"))
    )
    j = client.post(
        "/api/search", json={"topic": "cache compression", "sources": ["crossref"]}
    ).json()
    tick()
    assert client.get("/api/jobs/" + j["id"]).json()["status"] == "failed"


def test_taxonomy_accept_and_version_conflict(client, monkeypatch):
    j = import_one(client, monkeypatch)
    with Session.begin() as s:
        p = Proposal(
            paper_id=j["result"]["paper_id"],
            run_id=j["result"]["run_id"],
            dimension="technology",
            parent_id="cache",
            name="New cache method",
            reason="test",
        )
        s.add(p)
        s.flush()
        pid = p.id
    r = client.post(
        "/api/proposals/" + pid + "/resolve",
        json={"action": "accept", "expected_version": 1},
    )
    assert r.status_code == 200 and r.json()["version"] == 2
    assert (
        client.post(
            "/api/proposals/" + pid + "/resolve",
            json={"action": "accept", "expected_version": 1},
        ).status_code
        == 409
    )


def test_upload_type_and_cross_origin(client):
    assert (
        client.post(
            "/api/upload", files={"file": ("bad.pdf", b"not pdf", "application/pdf")}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/archive",
            json={"title": "test"},
            headers={"origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert client.get("/api/documents/../../.env/file").status_code in {404, 405}


def test_ssrf_blocks_private_addresses():
    with pytest.raises(ValueError):
        sources.public_addresses("http://127.0.0.1/a.pdf")
    with pytest.raises(ValueError):
        sources.public_addresses("file:///etc/passwd")
    with pytest.raises(ValueError):
        sources.public_addresses("http://localhost:9000/a.pdf")


def test_candidate_dedupe_and_year_filter():
    records = [
        {"title": "A", "doi": "10.1/a", "year": 2025, "source": "crossref"},
        {
            "title": "A",
            "doi": "https://doi.org/10.1/A",
            "year": 2025,
            "source": "arxiv",
        },
        {"title": "Unknown"},
    ]
    result = sources.ranked(records, "cache", 2024, 2026)
    assert len(result) == 1 and len(result[0]["provenance"]) == 2


def test_arxiv_parser():
    xml = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1234.5678v2</id><title>Cache paper</title><published>2025-01-01T00:00:00Z</published><summary>Summary</summary><author><name>A</name></author><link title="pdf" href="http://arxiv.org/pdf/1234.5678v2"/></entry></feed>'
    c = sources.parse_arxiv(xml)[0]
    assert (
        c["year"] == 2025
        and c["pdf_url"].startswith("https:")
        and sources.identity(c) == "arxiv:1234.5678"
    )


def test_search_input_validation(client):
    assert (
        client.post(
            "/api/search", json={"topic": "cache", "year_from": 2026, "year_to": 2020}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/search", json={"topic": "cache", "sources": []}).status_code
        == 422
    )
    assert (
        client.post("/api/search", json={"topic": "cache", "limit": 200}).status_code
        == 422
    )


def test_cancel_parent_propagates(client, monkeypatch):
    monkeypatch.setattr(
        sources,
        "crossref",
        lambda *a, **k: [{"title": "A", "abstract": ABSTRACT, "source": "crossref"}],
    )
    j = client.post(
        "/api/search", json={"topic": "cache", "sources": ["crossref"]}
    ).json()
    tick()
    client.post("/api/jobs/" + j["id"] + "/cancel")
    result = client.get("/api/jobs/" + j["id"]).json()
    assert result["status"] == "cancelled" and all(
        c["status"] == "cancelled" for c in result["children"]
    )


def test_successful_identical_document_reuses_run(client, monkeypatch):
    first = import_one(client, monkeypatch, doi="10.123/reuse")
    second = import_one(client, monkeypatch, doi="10.123/reuse")
    assert first["result"]["run_id"] == second["result"]["run_id"]
    assert second["result"]["reused"] is True


def test_abstract_reimport_preserves_fulltext(client, monkeypatch):
    first = import_one(client, monkeypatch, doi="10.123/full", text=ABSTRACT)
    second = import_one(
        client, monkeypatch, doi="10.123/full", abstract="A shorter abstract."
    )
    p = client.get("/api/papers/" + second["result"]["paper_id"]).json()
    assert p["reading_level"] == "fulltext"
    assert p["current_run_id"] == first["result"]["run_id"]


def test_uploaded_pdf_parsing(client, monkeypatch):
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(("BT /F1 12 Tf 50 700 Td (" + ABSTRACT + ") Tj ET").encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO()
    writer.write(buffer)
    mock_ai(monkeypatch)
    j = client.post(
        "/api/upload",
        files={"file": ("paper.pdf", buffer.getvalue(), "application/pdf")},
    ).json()
    tick()
    result = client.get("/api/jobs/" + j["id"]).json()
    assert result["status"] == "succeeded"
    p = client.get("/api/papers/" + result["result"]["paper_id"]).json()
    assert p["reading_level"] == "fulltext"
    assert p["evidence"][0]["page"] == 1
    assert client.get(
        "/api/documents/" + p["documents"][0]["id"] + "/file"
    ).content.startswith(b"%PDF-")


def test_parse_json_handles_code_fence():
    assert agents.parse_json('```json\n{"queries": ["cache"]}\n```') == {
        "queries": ["cache"]
    }


def test_acp_protocol_round_trip(monkeypatch):
    import sys
    from pathlib import Path

    from backend.acp_client import AcpProcess

    script = Path(__file__).with_name("fake_acp_agent.py")
    monkeypatch.setenv("ACP_COMMAND", f"{sys.executable} {script}")
    process = AcpProcess()
    try:
        result = process.prompt("hello", "fake/model")
    finally:
        process.close()
    assert result.text == '{"queries": ["cache compression"]}'
    assert result.stop_reason == "end_turn"
    assert result.usage == 42


def test_agent_call_uses_acp_and_records_usage(monkeypatch):
    from backend import acp_client

    class Result:
        text = '{"queries":["cache compression"]}'
        usage = 42

    monkeypatch.setattr(agents, "model_ready", lambda: True)
    monkeypatch.setattr(acp_client, "prompt", lambda *a, **k: Result())
    j = submit("search", {"max_calls": 2})
    assert agents.plan(j["id"], "缓存压缩") == ["cache compression"]
    with Session() as s:
        row = s.get(Job, j["id"])
        assert row.calls_used == 1 and row.tokens_used == 42


def test_agent_rank_validates_scores(monkeypatch):
    monkeypatch.setattr(
        agents,
        "call",
        lambda *args: {
            "ranking": [
                {"index": 0, "score": 0.9, "reason": "直接相关"},
                {"index": 1, "score": 0.2, "reason": "领域不同"},
            ]
        },
    )
    result = agents.rank("job", "cache prefetching", [{"title": "A"}, {"title": "B"}])
    assert result == {0: (0.9, "直接相关"), 1: (0.2, "领域不同")}


def test_openalex_item_rebuilds_abstract_and_pdf():
    assert sources.FRONT_MATTER.match("Sponsors")
    assert sources.FRONT_MATTER.match("Message from Program Chair")
    assert not sources.FRONT_MATTER.match("SPPAM: Signature Pattern Prediction")
    work = {
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.123/ABC",
        "title": "Cache paper",
        "publication_year": 2024,
        "authorships": [{"author": {"display_name": "Ada"}}],
        "primary_location": {"source": {"display_name": "HPCA"}},
        "open_access": {"oa_url": "https://example.org/paper.pdf"},
        "locations": [{"pdf_url": "https://example.org/paper.pdf"}],
        "abstract_inverted_index": {"Cache": [0], "compression": [1]},
    }
    candidate = sources.openalex_item(work)
    assert candidate["doi"] == "10.123/abc"
    assert candidate["abstract"] == "Cache compression"
    assert candidate["pdf_url"] == "https://example.org/paper.pdf"
    assert candidate["venue"] == "HPCA"


def test_static_pdf_candidates_order_and_arxiv_fallback():
    assert sources.static_pdf_candidates(
        {
            "pdf_urls": ["https://example.org/a.pdf", "https://example.org/b.pdf"],
            "pdf_url": "https://example.org/b.pdf",
            "arxiv_id": "2401.00001",
        }
    ) == [
        {"url": "https://example.org/a.pdf", "source": "candidate"},
        {"url": "https://example.org/b.pdf", "source": "candidate"},
        {"url": "https://arxiv.org/pdf/2401.00001", "source": "arxiv"},
    ]


def test_venue_search_discovers_all_and_marks_abstract_scope(client, monkeypatch):
    items = [
        {
            "title": "HPCA Paper A",
            "doi": "10.123/hpca-a",
            "year": 2024,
            "venue": "HPCA",
            "abstract": ABSTRACT,
            "source": "openalex",
            "pdf_url": "",
        },
        {
            "title": "HPCA Paper B",
            "doi": "10.123/hpca-b",
            "year": 2024,
            "venue": "HPCA",
            "abstract": ABSTRACT,
            "source": "openalex",
            "pdf_url": "",
        },
    ]
    monkeypatch.setattr(
        sources,
        "openalex_venue",
        lambda *args, **kwargs: (
            items,
            {
                "id": "S1",
                "name": "HPCA",
                "type": "conference",
                "total": 2,
                "fetched": 2,
                "truncated": False,
                "alternatives": [],
            },
        ),
    )
    job = client.post(
        "/api/search",
        json={
            "mode": "venue",
            "venue": "HPCA",
            "year_from": 2024,
            "year_to": 2024,
            "limit": 2,
            "max_calls": 0,
        },
    ).json()
    for _ in range(4):
        tick()
    parent = client.get("/api/jobs/" + job["id"]).json()
    assert parent["result"]["mode"] == "venue"
    assert parent["result"]["venue"]["name"] == "HPCA"
    assert parent["result"]["discovered"] == 2
    assert parent["result"]["coverage"]["expected_papers"] == 2
    assert parent["result"]["coverage"]["ratio"] == 1
    assert parent["result"]["archive_stats"]["abstract"] == 2
    assert len(parent["children"]) == 2
    papers = client.get("/api/papers").json()["items"]
    assert len(papers) == 2
    assert all(p["reading_level"] == "abstract" for p in papers)
    assert all(p["coverage_note"].startswith("摘要") for p in papers)


def test_venue_search_requires_a_name(client):
    response = client.post("/api/search", json={"mode": "venue", "venue": " "})
    assert response.status_code == 422


def test_metadata_only_archive_marks_metadata_scope(client):
    job = client.post("/api/archive", json={"title": "Metadata only"}).json()
    tick()
    result = client.get("/api/jobs/" + job["id"]).json()
    paper = client.get("/api/papers/" + result["result"]["paper_id"]).json()
    assert paper["reading_level"] == "metadata"
    assert paper["coverage_note"].startswith("元数据")


def test_archive_records_failed_pdf_attempts(client, monkeypatch):
    from backend.db import DownloadAttempt

    monkeypatch.setattr(
        sources,
        "static_pdf_candidates",
        lambda candidate: [
            {"url": "https://example.org/a.pdf", "source": "openalex"}
        ],
    )
    monkeypatch.setattr(sources, "discover_pdf_candidates", lambda candidate: [])

    def fail(url):
        raise sources.DownloadError("http_403", "forbidden", 403, "text/html")

    monkeypatch.setattr(sources, "fetch_pdf", fail)
    job = client.post(
        "/api/archive",
        json={
            "title": "Fail PDF",
            "abstract": ABSTRACT,
            "url": "https://example.org/a.pdf",
            "max_calls": 0,
        },
    ).json()
    tick()
    result = client.get("/api/jobs/" + job["id"]).json()
    paper = client.get("/api/papers/" + result["result"]["paper_id"]).json()
    assert paper["reading_level"] == "abstract"
    assert result["result"]["download"]["success"] is False
    assert result["result"]["download"]["attempts"][0]["error_class"] == "http_403"
    with Session() as s:
        attempts = s.query(DownloadAttempt).all()
        assert len(attempts) == 1
        assert attempts[0].error_class == "http_403"
        assert attempts[0].status_code == 403


def test_pdf_assets_preserve_images_and_tables(client):
    from io import BytesIO

    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80), False)
    for y in range(80):
        for x in range(120):
            pix.set_pixel(x, y, (x * 2 % 256, y * 3 % 256, (x + y) % 256))
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Visual paper " * 40)
    page.insert_image(pymupdf.Rect(72, 260, 192, 340), stream=pix.tobytes("png"))
    for x in [72, 150, 228]:
        page.draw_line((x, 100), (x, 180))
    for y in [100, 120, 140, 160, 180]:
        page.draw_line((72, y), (228, y))
    for index, (x, y) in enumerate(
        [(80, 115), (158, 115), (80, 155), (158, 155)]
    ):
        page.insert_text((x, y), "cell" + str(index))
    buffer = BytesIO()
    doc.save(buffer)
    job = client.post(
        "/api/upload",
        files={"file": ("visual.pdf", buffer.getvalue(), "application/pdf")},
    ).json()
    tick()
    result = client.get("/api/jobs/" + job["id"]).json()
    detail = client.get("/api/papers/" + result["result"]["paper_id"]).json()
    document = detail["documents"][0]
    assets = document["assets"]
    assert document["markdown_available"] is True
    assert any(asset["render"] for asset in assets)
    assert any(asset["images"] for asset in assets)
    assert any(asset["tables"] for asset in assets)
    markdown = client.get(
        "/api/documents/" + document["id"] + "/markdown"
    ).text
    assert "## Page 1" in markdown
    assert "| --- |" in markdown
    assert "![figure]" in markdown or "![page layout]" in markdown
    assert "/api/documents/" in markdown and "/assets/" in markdown
    render = next(asset["render"] for asset in assets if asset["render"])
    response = client.get(
        "/api/documents/" + document["id"] + "/assets/" + render
    )
    assert response.status_code == 200
    assert response.content.startswith(b"\xff\xd8")


def test_no_pdf_text_reports_partial(client):
    from io import BytesIO
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=100, height=100)
    buf = BytesIO()
    w.write(buf)
    j = client.post(
        "/api/upload", files={"file": ("blank.pdf", buf.getvalue(), "application/pdf")}
    ).json()
    tick()
    result = client.get("/api/jobs/" + j["id"]).json()
    assert result["status"] == "partial" and any(
        "OCR" in w for w in result["result"]["warnings"]
    )
