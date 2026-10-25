import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from sqlalchemy import select, func, update
from .config import FILES, ROOT, agent_model, model_ready, pdf_settings
from .db import (
    Session,
    Paper,
    Document,
    DownloadAttempt,
    Run,
    Technique,
    Evidence,
    Classification,
    Proposal,
    Node,
    Job,
    asdict,
    uid,
    Version,
)
from .tasks import (
    stage,
    finish,
    check,
    Cancelled,
    BudgetExceeded,
    remaining_calls,
)
from . import sources, agents
from .reading import research_pages, merge_techniques, EXTRACT_VERSION


def save_file(data):
    sha = hashlib.sha256(data).hexdigest()
    name = sha + ".pdf"
    path = FILES / name
    if not path.exists():
        temp = FILES / (sha + "." + uid() + ".tmp")
        temp.write_bytes(data)
        temp.replace(path)
    return name, sha


def parse_file(name):
    path = FILES / Path(name).name
    r = subprocess.run(
        [sys.executable, "-m", "backend.parse_pdf", str(path)],
        cwd=ROOT,
        capture_output=True,
        timeout=120,
    )
    if r.returncode:
        raise ValueError("PDF 无法解析，可能加密或损坏")
    payload = json.loads(r.stdout)
    if isinstance(payload, dict):
        pages = payload.get("pages") or []
        markdown = payload.get("markdown") or ""
    else:
        pages = payload
        markdown = "\n\n".join(
            p.get("markdown") or p.get("text", "") for p in pages
        )
    if sum(len(p["text"].strip()) for p in pages) < 80:
        raise ValueError("PDF 可提取文本不足；扫描件需要 OCR，当前未启用")
    return pages, markdown


def record_download_attempt(paper_id, job_id, candidate, started, meta=None, error=None):
    elapsed = int((time.monotonic() - started) * 1000)
    with Session.begin() as s:
        s.add(
            DownloadAttempt(
                paper_id=paper_id,
                job_id=job_id,
                url=candidate["url"],
                source=candidate.get("source", ""),
                status_code=(meta or {}).get("status")
                if meta
                else getattr(error, "status", None),
                content_type=(meta or {}).get("content_type", "")
                if meta
                else getattr(error, "content_type", ""),
                bytes=(meta or {}).get("bytes", 0) if meta else 0,
                error_class="" if meta else getattr(error, "error_class", "error"),
                elapsed_ms=elapsed,
            )
        )


def blocks_for(pages):
    blocks = []
    for page in pages:
        text = page.get("markdown") or page["text"]
        for offset in range(0, len(text), 4500):
            if text[offset : offset + 4500].strip():
                blocks.append(
                    {
                        "id": "b" + str(len(blocks) + 1),
                        "page": page["page"],
                        "text": text[offset : offset + 4500],
                    }
                )
    return blocks


def archive(job_id, token, payload):
    stage(job_id, token, "解析文献身份")
    with Session() as s:
        previous = s.get(Job, job_id).result
        if previous.get("run_id") and s.get(Run, previous["run_id"]):
            finish(
                job_id,
                token,
                "partial" if previous.get("warnings") else "succeeded",
                previous,
            )
            return
    warnings = []
    try:
        c = sources.resolve(payload)
    except Exception as exc:
        c = dict(payload)
        c["title"] = c.get("title") or c.get("doi") or c.get("url") or "未命名论文"
        warnings.append("元数据解析失败：" + safe_error(exc))
    if c.get("doi"):
        c["doi"] = sources.norm_doi(c["doi"])
    if c.get("text"):
        c["sha256"] = hashlib.sha256(c["text"].encode()).hexdigest()
    key = sources.identity(c)
    with Session.begin() as s:
        check(s, job_id, token)
        paper = (
            s.get(Paper, c.get("paper_id", ""))
            if c.get("paper_id")
            else s.scalar(select(Paper).where(Paper.identity == key))
        )
        if not paper and c.get("sha256"):
            doc = s.scalar(select(Document).where(Document.sha256 == c["sha256"]))
            if doc:
                paper = s.get(Paper, doc.paper_id)
        if not paper:
            paper = Paper(identity=key, title=c.get("title") or "未命名论文")
            s.add(paper)
            s.flush()
        for field in ["title", "doi", "authors", "year", "venue", "url", "abstract"]:
            if c.get(field):
                setattr(paper, field, c[field])
        paper_id = paper.id
        j = s.get(Job, job_id)
        j.result = {**j.result, "paper_id": paper_id, "warnings": warnings}
    stage(job_id, token, "获取与解析正文")
    pages = None
    markdown = ""
    kind = "abstract"
    storage = c.get("storage_name", "")
    sha = c.get("sha256", "")
    pdf_report = {
        "candidates": 0,
        "attempts": [],
        "success": False,
        "error_classes": [],
    }
    if storage:
        try:
            pages, markdown = parse_file(storage)
            kind = "fulltext"
        except Exception as exc:
            warnings.append(safe_error(exc))
    elif not c.get("text"):
        candidates = sources.static_pdf_candidates(c)
        discovered = False
        if not candidates:
            candidates = sources.discover_pdf_candidates(c)
            discovered = True
        pdf_report["candidates"] = len(candidates)
        max_candidates = pdf_settings()["max_candidates"]
        index = 0
        tried = 0
        while index < len(candidates) and tried < max_candidates:
            candidate = candidates[index]
            index += 1
            tried += 1
            started = time.monotonic()
            try:
                data, meta = sources.fetch_pdf(candidate["url"])
            except sources.DownloadError as exc:
                record_download_attempt(paper_id, job_id, candidate, started, error=exc)
                pdf_report["attempts"].append(
                    {
                        "url": candidate["url"],
                        "source": candidate.get("source", ""),
                        "error_class": exc.error_class,
                        "status": exc.status,
                    }
                )
                pdf_report["error_classes"].append(exc.error_class)
            except Exception as exc:
                download_error = sources.DownloadError(
                    "error", f"{type(exc).__name__}: {exc}"
                )
                record_download_attempt(
                    paper_id, job_id, candidate, started, error=download_error
                )
                pdf_report["attempts"].append(
                    {
                        "url": candidate["url"],
                        "source": candidate.get("source", ""),
                        "error_class": "error",
                        "status": None,
                    }
                )
                pdf_report["error_classes"].append("error")
            else:
                try:
                    storage, sha = save_file(data)
                    pages, markdown = parse_file(storage)
                    kind = "fulltext"
                except Exception as exc:
                    parse_error = sources.DownloadError("parse_error", str(exc))
                    record_download_attempt(
                        paper_id, job_id, candidate, started, error=parse_error
                    )
                    pdf_report["attempts"].append(
                        {
                            "url": candidate["url"],
                            "source": candidate.get("source", ""),
                            "error_class": "parse_error",
                            "status": meta.get("status"),
                        }
                    )
                    pdf_report["error_classes"].append("parse_error")
                else:
                    record_download_attempt(
                        paper_id, job_id, candidate, started, meta=meta
                    )
                    pdf_report["attempts"].append(
                        {
                            "url": candidate["url"],
                            "source": candidate.get("source", ""),
                            "error_class": "",
                            "status": meta.get("status"),
                        }
                    )
                    pdf_report["success"] = True
                    break
            if (
                index >= len(candidates)
                and not discovered
                and tried < max_candidates
            ):
                extra = sources.discover_pdf_candidates(c)
                known = {item["url"] for item in candidates}
                for item in extra:
                    if item["url"] not in known:
                        candidates.append(item)
                        known.add(item["url"])
                discovered = True
                pdf_report["candidates"] = len(candidates)
        if not pdf_report["success"] and pdf_report["attempts"]:
            classes = sorted(set(pdf_report["error_classes"]))
            warnings.append("全文获取失败：" + "、".join(classes))
    if not pages and c.get("text"):
        pages = [{"page": 1, "text": c["text"]}]
        markdown = c["text"]
        kind = "fulltext"
    if not pages and c.get("abstract"):
        pages = [{"page": 0, "text": c["abstract"]}]
        markdown = c["abstract"]
        kind = "abstract"
    # A metadata-only reimport must never replace an existing full-text analysis with an abstract.
    if kind != "fulltext":
        with Session() as s:
            full = s.scalar(
                select(Document).where(
                    Document.paper_id == paper_id, Document.kind == "fulltext"
                )
            )
            if full:
                pages, kind, storage, sha = (
                    full.pages,
                    full.kind,
                    full.storage_name,
                    full.sha256,
                )
                markdown = full.markdown or "\n\n".join(
                    p.get("markdown") or p.get("text", "") for p in pages
                )
    if not pages:
        note = "元数据：未获取到摘要或全文"
        with Session.begin() as s:
            p = s.get(Paper, paper_id)
            p.coverage_note = note
        finish(
            job_id,
            token,
            "partial",
            {
                "paper_id": paper_id,
                "coverage_note": note,
                "download": pdf_report,
                "warnings": warnings + ["没有可阅读内容，仅保存元数据"],
            },
        )
        return
    if kind == "fulltext":
        if storage:
            visuals = sum(
                len(page.get("images") or []) + len(page.get("tables") or [])
                for page in pages
            )
            suffix = f"，图表 {visuals} 处" if visuals else ""
            note = f"全文：已解析 PDF（{len(pages)} 页{suffix}）"
        else:
            note = "全文：已保存正文"
    else:
        note = "摘要：未获取到可下载全文，仅归档摘要"
    if not sha:
        sha = hashlib.sha256(json.dumps(pages, sort_keys=True).encode()).hexdigest()
    with Session.begin() as s:
        check(s, job_id, token)
        d = s.scalar(
            select(Document).where(
                Document.paper_id == paper_id, Document.sha256 == sha
            )
        )
        if not d:
            d = Document(
                paper_id=paper_id,
                sha256=sha,
                storage_name=storage,
                kind=kind,
                pages=pages,
                markdown=markdown,
            )
            s.add(d)
            s.flush()
        else:
            d.pages = pages
            d.kind = kind
            d.markdown = markdown
        doc_id = d.id
        p = s.get(Paper, paper_id)
        if p.reading_level != "fulltext":
            p.reading_level = kind
        p.coverage_note = note
    if not model_ready() or remaining_calls(job_id) <= 0:
        reason = (
            "agent_not_configured：已保存文献，请配置 ACP agent 后重试"
            if not model_ready()
            else "model_budget_exhausted：已保存文献，未执行 AI 技术提取"
        )
        finish(
            job_id,
            token,
            "partial",
            {
                "paper_id": paper_id,
                "coverage_note": note,
                "download": pdf_report,
                "warnings": warnings + [reason],
            },
        )
        return
    with Session() as s:
        nodes = [asdict(n) for n in s.scalars(select(Node)).all()]
        version = max(n["version"] for n in nodes)
        p = s.get(Paper, paper_id)
        prior = s.get(Run, p.current_run_id) if p.current_run_id else None
        if (
            prior
            and not payload.get("force")
            and prior.document_id == doc_id
            and prior.model == agent_model()
            and prior.taxonomy_version == version
            and not prior.limitations
            and prior.prompt_version == EXTRACT_VERSION
        ):
            finish(
                job_id,
                token,
                "succeeded",
                {
                    "paper_id": paper_id,
                    "run_id": prior.id,
                    "coverage_note": note,
                    "download": pdf_report,
                    "warnings": warnings,
                    "reused": True,
                },
            )
            return
    body_pages, references_removed = research_pages(pages)
    blocks = blocks_for(body_pages)
    batches = [blocks[i : i + 4] for i in range(0, len(blocks), 4)]
    if len(batches) > 8:
        warnings.append("长文超过 32 个文本块，仅处理前 8 批；总结覆盖不完整")
    outputs = []
    for index, batch in enumerate(batches[:8]):
        stage(job_id, token, f"AI 阅读与分类 {index + 1}/{min(len(batches), 8)}")
        try:
            outputs.append(agents.extract(job_id, batch, nodes))
        except Cancelled:
            raise
        except Exception as exc:
            warnings.append("部分提取失败：" + safe_error(exc))
            if isinstance(exc, BudgetExceeded):
                break
    if not outputs or not any(o["techniques"] for o in outputs):
        finish(
            job_id,
            token,
            "partial",
            {
                "paper_id": paper_id,
                "coverage_note": note,
                "download": pdf_report,
                "warnings": warnings + ["没有通过证据校验的技术输出"],
            },
        )
        return
    combined = merge_techniques(outputs)
    if (
        len(outputs) > 1
        and len(combined["techniques"]) > 1
        and remaining_calls(job_id) > 0
    ):
        stage(job_id, token, "ACP 跨批技术归并")
        try:
            combined = agents.consolidate(job_id, [combined])
        except Cancelled:
            raise
        except Exception as exc:
            warnings.append("语义归并失败，保留按名称合并结果：" + safe_error(exc))
    outputs = [combined]
    stage(job_id, token, "保存技术、证据与分类")
    block_map = {b["id"]: b for b in blocks}
    with Session.begin() as s:
        check(s, job_id, token)
        s.execute(update(Version).where(Version.version == 1).values(version=1))
        if s.scalar(select(func.max(Node.version))) != version:
            raise ValueError("分析过程中分类树发生变化，请重试以使用最新分类")
        run = Run(
            paper_id=paper_id,
            document_id=doc_id,
            model=agent_model(),
            prompt_version=EXTRACT_VERSION,
            taxonomy_version=version,
            summary="\n\n".join(o["summary"] for o in outputs),
            limitations=warnings,
        )
        s.add(run)
        s.flush()
        for out in outputs:
            for t in out["techniques"]:
                tech = Technique(
                    run_id=run.id,
                    **{
                        k: t[k]
                        for k in [
                            "name",
                            "mechanism",
                            "problem",
                            "conditions",
                            "limitations",
                        ]
                    },
                )
                s.add(tech)
                s.flush()
                refs = []
                for e in t["evidence"]:
                    ev = Evidence(
                        run_id=run.id,
                        document_id=doc_id,
                        block_id=e["block_id"],
                        page=block_map[e["block_id"]]["page"],
                        quote=e["quote"],
                    )
                    s.add(ev)
                    s.flush()
                    refs.append(ev.id)
                tech.claims = refs
                for cl in t.get("classifications", []):
                    s.add(
                        Classification(
                            paper_id=paper_id,
                            technique_id=tech.id,
                            run_id=run.id,
                            **{k: cl[k] for k in ["node_id", "confidence", "reason"]},
                        )
                    )
            for pr in out.get("proposals", []):
                s.add(
                    Proposal(
                        paper_id=paper_id,
                        run_id=run.id,
                        dimension=pr["dimension"],
                        parent_id=pr.get("parent_id"),
                        name=pr["name"],
                        reason=pr.get("reason", ""),
                    )
                )
        p = s.get(Paper, paper_id)
        p.current_run_id = run.id
        p.reading_level = kind
        s.get(Job, job_id).result = {
            "paper_id": paper_id,
            "run_id": run.id,
            "coverage_note": note,
            "download": pdf_report,
            "warnings": warnings,
            "references_removed": references_removed,
        }
    finish(
        job_id,
        token,
        "partial" if warnings else "succeeded",
        {
            "paper_id": paper_id,
            "run_id": run.id,
            "coverage_note": note,
            "download": pdf_report,
            "warnings": warnings,
            "references_removed": references_removed,
        },
    )


def safe_error(exc):
    # Never expose model request headers or response bodies.
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        return f"远程服务 HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.RequestError):
        return "网络请求失败或超时"
    return str(exc)[:400]


def search(job_id, token, payload):
    with Session() as s:
        previous = s.get(Job, job_id).result
        if previous.get("discovery_complete"):
            finish(job_id, token, "waiting", previous)
            return
    mode = payload.get("mode") or "topic"
    warnings = []
    venue_info = None
    semantic_applied = False
    excluded_count = 0
    source_stats = {}
    if mode == "venue":
        stage(job_id, token, "解析会议/期刊")
        try:
            candidates, venue_info = sources.openalex_venue(
                payload["venue"],
                payload.get("year_from"),
                payload.get("year_to"),
            )
        except Exception as exc:
            reason = safe_error(exc)
            finish(
                job_id,
                token,
                "failed",
                {"mode": "venue", "warnings": [reason]},
                error=reason,
            )
            return
        if venue_info.get("alternatives"):
            warnings.append(
                "匹配到多个来源，使用："
                + venue_info["name"]
                + "（其他："
                + "、".join(venue_info["alternatives"])
                + "）"
            )
        if venue_info.get("truncated"):
            warnings.append(
                f"目录有 {venue_info.get('raw_total')} 条记录，仅处理 {venue_info.get('processed_records')} 条、获取论文 "
                f"{venue_info.get('fetched')} 篇；不能视为全量目录"
            )
        ranked = sources.ranked_venue(
            candidates, payload.get("year_from"), payload.get("year_to")
        )
        queries = [payload["venue"]]
        ranking_mode = (
            "会议/期刊目录（OpenAlex，"
            + ("部分抓取" if venue_info.get("truncated") else "本次目录抓取完成")
            + "）"
        )
        successes = 1
    else:
        stage(job_id, token, "生成检索计划")
        queries = [payload["topic"]]
        if model_ready() and payload.get("max_calls", 16) > 0:
            try:
                queries = agents.plan(job_id, payload["topic"])
            except Cancelled:
                raise
            except Exception as exc:
                warnings.append("查询规划回退原始主题：" + safe_error(exc))
        candidates = []
        successes = 0
        for source in payload["sources"]:
            for query in queries:
                stage(job_id, token, "检索 " + source)
                try:
                    items = getattr(sources, source)(
                        query,
                        min(60, payload["limit"] * 3),
                        year_from=payload.get("year_from"),
                        year_to=payload.get("year_to"),
                    )
                    candidates.extend([{**x, "query": query} for x in items])
                    successes += 1
                    stats = source_stats.setdefault(
                        source, {"queries": 0, "fetched": 0, "errors": []}
                    )
                    stats["queries"] += 1
                    stats["fetched"] += len(items)
                except Exception as exc:
                    stats = source_stats.setdefault(
                        source, {"queries": 0, "fetched": 0, "errors": []}
                    )
                    stats["queries"] += 1
                    stats["errors"].append(safe_error(exc))
                    warnings.append(source + "：" + safe_error(exc))
        ranked = sources.ranked(
            candidates,
            " ".join(queries),
            payload.get("year_from"),
            payload.get("year_to"),
        )
        ranking_mode = "来源排名与关键词匹配"
        if model_ready() and remaining_calls(job_id) > 0 and len(ranked) > 1:
            try:
                scores = agents.rank(job_id, payload["topic"], ranked[:30])
                for index, (score, reason) in scores.items():
                    ranked[index]["agent_score"] = score
                    ranked[index]["score"] = score
                    ranked[index]["reason"] = f"Agent 相关性 {score:.2f}：" + (
                        reason or "与主题相关"
                    )
                ranked.sort(key=lambda c: c.get("agent_score", -1), reverse=True)
                ranking_mode = "关键词召回 + ACP 语义重排"
                semantic_applied = True
            except Cancelled:
                raise
            except Exception as exc:
                warnings.append("相关性重排跳过：" + safe_error(exc))
    if semantic_applied:
        eligible = [
            c
            for c in ranked
            if c.get("agent_score", -1) >= payload.get("min_relevance", 0.45)
        ]
        excluded_count = len(ranked) - len(eligible)
        selected = eligible[: payload["limit"]]
    else:
        selected = ranked[: payload["limit"]]
    if mode == "venue":
        expected = venue_info.get("total") or len(ranked)
        coverage = {
            "mode": "venue",
            "source": venue_info.get("name"),
            "source_total": venue_info.get("raw_total"),
            "non_paper_filtered": venue_info.get("filtered_out", 0),
            "expected_papers": expected,
            "discovered": len(ranked),
            "selected": len(selected),
            "truncated": bool(venue_info.get("truncated")),
            "ratio": round(min(1, len(ranked) / expected), 3) if expected else 0,
        }
    else:
        coverage = {
            "mode": "topic",
            "sources": source_stats,
            "discovered": len(ranked),
            "selected": len(selected),
            "truncated": False,
        }
    stage(job_id, token, "提交自动归档")
    # Child creation and discovery result are atomic, so a recovered search cannot duplicate children.
    with Session.begin() as s:
        check(s, job_id, token)
        j = s.get(Job, job_id)
        existing = s.scalars(select(Job).where(Job.parent_id == job_id)).all()
        if not existing:
            for c in selected:
                child = Job(
                    kind="archive",
                    parent_id=job_id,
                    payload={**c, "max_calls": payload.get("max_calls", 16)},
                    max_calls=payload.get("max_calls", 16),
                )
                s.add(child)
                s.flush()
                c["job_id"] = child.id
        j.result = {
            "mode": mode,
            "venue": venue_info,
            "queries": queries,
            "candidates": ranked,
            "selected": selected,
            "warnings": warnings,
            "discovered": len(ranked),
            "coverage": coverage,
            "ranking_mode": ranking_mode,
            "discovery_complete": True,
            "semantic_threshold_applied": semantic_applied,
            "min_relevance": payload.get("min_relevance", 0.45),
            "excluded_or_unscored": excluded_count,
        }
    if mode == "venue":
        status = "waiting" if selected else "succeeded"
    else:
        status = (
            "waiting"
            if selected
            else (
                "failed" if successes == 0 else "partial" if warnings else "succeeded"
            )
        )
    finish(job_id, token, status, j.result)
