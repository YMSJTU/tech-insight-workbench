"""Category research reports with immutable, cited input snapshots."""

import hashlib
import json
from sqlalchemy import select
from .db import (
    Session,
    Node,
    Paper,
    PaperCategories,
    Classification,
    Run,
    Evidence,
    Document,
    CategoryReport,
    Job,
)
from .config import agent_model
from .tasks import check
from .reading import research_pages
from .execution import REPORT_VERSION


def snapshot(s, node_id, year_from=None, year_to=None, max_papers=60):
    nodes = s.scalars(select(Node)).all()
    node = s.get(Node, node_id)
    if not node:
        raise ValueError("分类不存在")
    subtree = {node_id}
    while True:
        more = {n.id for n in nodes if n.parent_id in subtree}
        if more <= subtree:
            break
        subtree |= more
    overrides = {r.paper_id: r for r in s.scalars(select(PaperCategories)).all()}
    classes = {}
    for c in s.scalars(
        select(Classification).where(Classification.node_id.in_(subtree))
    ):
        classes.setdefault((c.paper_id, c.run_id), set()).add(c.node_id)
    query = select(Paper).order_by(Paper.year.asc().nulls_last(), Paper.id)
    if year_from:
        query = query.where(Paper.year >= year_from)
    if year_to:
        query = query.where(Paper.year <= year_to)
    sources = []
    matched = 0
    unreadable = 0
    for p in s.scalars(query):
        override = overrides.get(p.id)
        selected = (
            bool(subtree.intersection(override.node_ids))
            if override and override.manual
            else bool(classes.get((p.id, p.current_run_id)))
        )
        if not selected:
            continue
        matched += 1
        r = s.get(Run, p.current_run_id) if p.current_run_id else None
        excerpts = []
        text = ""
        scope = ""
        if r:
            text = r.summary[:2500]
            scope = "已有技术总结及原文证据摘录"
            for e in s.scalars(
                select(Evidence)
                .where(Evidence.run_id == r.id)
                .order_by(Evidence.id)
                .limit(5)
            ):
                excerpts.append(
                    {"page": e.page, "quote": e.quote[:450], "evidence_id": e.id}
                )
        elif p.abstract.strip():
            text = p.abstract[:4500]
            scope = "原始摘要"
        else:
            d = s.scalar(
                select(Document)
                .where(Document.paper_id == p.id, Document.kind == "fulltext")
                .order_by(Document.id)
            )
            if d:
                pages, _ = research_pages(d.pages)
                text = "\n".join(pg["text"] for pg in pages)[:4500]
                scope = "正文开头摘录"
        if not text.strip():
            unreadable += 1
            continue
        sources.append(
            {
                "paper_id": p.id,
                "title": p.title,
                "year": p.year,
                "venue": p.venue,
                "url": p.url,
                "reading_level": p.reading_level,
                "material_scope": scope,
                "run_id": p.current_run_id,
                "text": text,
                "evidence": excerpts,
                "category_revision": override.revision if override else 0,
            }
        )
    available = len(sources)
    # Even sampling across years avoids presenting only the earliest papers as a trend.
    if available > max_papers:
        indices = (
            [round(i * (available - 1) / (max_papers - 1)) for i in range(max_papers)]
            if max_papers > 1
            else [available - 1]
        )
        sources = [sources[i] for i in indices]
    for i, item in enumerate(sources):
        item["id"] = f"S{i + 1:03d}"
    result = {
        "node_id": node_id,
        "node_name": node.name,
        "dimension": node.dimension,
        "subtree": sorted(subtree),
        "taxonomy_version": max(n.version for n in nodes),
        "sources": sources,
        "stats": {
            "matched": matched,
            "readable": available,
            "used": len(sources),
            "metadata_only": unreadable,
            "omitted": available - len(sources),
            "years": sorted({p["year"] for p in sources if p["year"]}),
        },
    }
    result["fingerprint"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return result


def saved_report(job_id):
    with Session() as s:
        r = s.scalar(select(CategoryReport).where(CategoryReport.job_id == job_id))
        return {"id": r.id, "status": r.status, "warnings": r.warnings} if r else None


def save_report(job_id, token, payload, snap, content, warnings, partial, used):
    with Session.begin() as s:
        check(s, job_id, token)
        r = CategoryReport(
            job_id=job_id,
            node_id=payload["node_id"],
            kind=payload["report_kind"],
            status="partial" if partial else "succeeded",
            scope={
                k: payload.get(k)
                for k in ["node_id", "year_from", "year_to", "max_papers"]
            },
            snapshot=snap,
            content=content,
            model=agent_model(),
            prompt_version=REPORT_VERSION,
            warnings=warnings,
        )
        s.add(r)
        s.flush()
        rid = r.id
        s.get(Job, job_id).result = {
            "report_id": rid,
            "warnings": warnings,
            "generated_papers": len(used),
        }
    return rid
