from contextlib import asynccontextmanager
import time
from typing import Literal
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, func, or_, update
from .config import (
    ROOT,
    FILES,
    MAX_FILE,
    agent_config,
    agent_model,
    model_ready,
    DATABASE_URL,
    DATA,
)
from .db import (
    init_db,
    Session,
    Paper,
    Document,
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
    PaperCategories,
    CategoryReport,
)
from .tasks import submit, TERMINAL
from .workflows import save_file
from . import insights


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title="技术洞察 · 论文工作台", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    origin = request.headers.get("origin")
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and origin
        and origin.rstrip("/") != str(request.base_url).rstrip("/")
    ):
        return JSONResponse({"detail": "不允许跨站写请求"}, status_code=403)
    host = request.url.hostname
    if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
        return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
    return await call_next(request)


class ArchiveInput(BaseModel):
    title: str = Field("", max_length=1000)
    doi: str = Field("", max_length=300)
    url: str = Field("", max_length=2000)
    abstract: str = Field("", max_length=100000)
    text: str = Field("", max_length=500000)
    authors: list[str] = Field(default_factory=list, max_length=200)
    year: int | None = Field(None, ge=1800, le=2200)
    venue: str = Field("", max_length=500)
    max_calls: int = Field(16, ge=0, le=40)

    @model_validator(mode="after")
    def valid(self):
        if not any(x.strip() for x in [self.title, self.doi, self.url, self.text]):
            raise ValueError("请输入标题、DOI、链接或正文")
        if self.url and urlparse(self.url).scheme not in {"https", "http"}:
            raise ValueError("链接必须使用 HTTP/HTTPS")
        return self


class SearchInput(BaseModel):
    mode: Literal["topic", "venue"] = "topic"
    topic: str = Field("", max_length=500)
    venue: str = Field("", max_length=300)
    sources: list[Literal["crossref", "arxiv", "openalex"]] = Field(
        default_factory=lambda: ["crossref", "arxiv"],
        min_length=1,
        max_length=3,
    )
    year_from: int | None = Field(None, ge=1800, le=2200)
    year_to: int | None = Field(None, ge=1800, le=2200)
    limit: int = Field(5, ge=1, le=200)
    max_calls: int = Field(16, ge=0, le=40)
    min_relevance: float = Field(0.45, ge=0, le=1)

    @model_validator(mode="after")
    def valid(self):
        if self.mode == "topic":
            if len(self.topic.strip()) < 2:
                raise ValueError("主题至少需要 2 个字符")
            if self.limit > 20:
                raise ValueError("主题搜索最多自动归档 20 篇")
        else:
            if not self.venue.strip():
                raise ValueError("请输入会议或期刊名称")
            self.sources = ["openalex"]
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("起始年份不能晚于结束年份")
        self.sources = list(dict.fromkeys(self.sources))
        return self


def require(s, cls, id_):
    obj = s.get(cls, id_)
    if not obj:
        raise HTTPException(404, "记录不存在")
    return obj


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/settings")
def settings():
    c = agent_config()
    heartbeat = DATA / "worker.heartbeat"
    ready = model_ready()
    return {
        "worker_running": heartbeat.exists()
        and time.time() - heartbeat.stat().st_mtime < 45,
        "agent_ready": ready,
        "agent_mode": "acp",
        "agent_command": c["command"],
        "agent_model": agent_model(),
        "model": agent_model(),
        "database": "sqlite" if DATABASE_URL.startswith("sqlite") else "postgresql",
        "retrieval": "关键词与结构化筛选",
        "ocr": False,
    }


@app.post("/api/archive", status_code=202)
def archive_submit(data: ArchiveInput):
    return submit("archive", data.model_dump())


@app.post("/api/search", status_code=202)
def search_submit(data: SearchInput):
    return submit("search", data.model_dump())


@app.post("/api/upload", status_code=202)
async def upload(
    file: UploadFile = File(...), title: str = Form(""), doi: str = Form("")
):
    data = await file.read(MAX_FILE + 1)
    await file.close()
    if len(data) > MAX_FILE:
        raise HTTPException(413, "PDF 超过 25 MB 上限")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, "仅支持 PDF 文件")
    if len(title) > 1000 or len(doi) > 300:
        raise HTTPException(400, "标题或 DOI 过长")
    name, sha = save_file(data)
    return submit(
        "archive",
        {
            "title": title or file.filename or "上传论文",
            "doi": doi,
            "storage_name": name,
            "sha256": sha,
            "max_calls": 16,
        },
    )


@app.get("/api/taxonomy")
def taxonomy():
    with Session() as s:
        nodes = [asdict(n) for n in s.scalars(select(Node)).all()]
        return {
            "nodes": nodes,
            "version": max((n["version"] for n in nodes), default=1),
        }


def descendants(nodes, id_):
    result = {id_}
    while True:
        new = {n.id for n in nodes if n.parent_id in result}
        if new <= result:
            return result
        result |= new


def taxonomy_lock(s, expected):
    s.execute(update(Version).where(Version.version == 1).values(version=1))
    version = s.scalar(select(func.max(Node.version))) or 1
    if expected != version:
        raise HTTPException(409, "分类已被修改，请刷新后重试")
    return version


class NodeInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    dimension: Literal["technology", "problem"]
    parent_id: str | None = None
    expected_version: int


def validate_node(s, data, id_=None):
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "分类名称不能为空")
    if data.parent_id:
        parent = require(s, Node, data.parent_id)
        if parent.dimension != data.dimension:
            raise HTTPException(400, "父节点必须属于同一维度")
        if id_ and data.parent_id in descendants(s.scalars(select(Node)).all(), id_):
            raise HTTPException(400, "不能移动到自身或自己的子分类下")
    same = s.scalar(
        select(Node).where(
            Node.dimension == data.dimension,
            Node.parent_id == data.parent_id,
            Node.name == name,
        )
    )
    if same and same.id != id_:
        raise HTTPException(409, "同级已有同名分类")
    return name


@app.post("/api/taxonomy", status_code=201)
def create_node(data: NodeInput):
    with Session.begin() as s:
        version = taxonomy_lock(s, data.expected_version)
        name = validate_node(s, data)
        s.execute(update(Node).values(version=version + 1))
        n = Node(
            id=uid(),
            name=name,
            dimension=data.dimension,
            parent_id=data.parent_id,
            version=version + 1,
        )
        s.add(n)
        s.flush()
        return {"node": asdict(n), "version": version + 1}


@app.put("/api/taxonomy/{id_}")
def edit_node(id_: str, data: NodeInput):
    with Session.begin() as s:
        version = taxonomy_lock(s, data.expected_version)
        n = require(s, Node, id_)
        if n.dimension != data.dimension:
            raise HTTPException(400, "不能跨技术域与问题域移动")
        n.name = validate_node(s, data, id_)
        n.parent_id = data.parent_id
        s.execute(update(Node).values(version=version + 1))
        return {"node": asdict(n), "version": version + 1}


@app.delete("/api/taxonomy/{id_}")
def delete_node(id_: str, expected_version: int):
    with Session.begin() as s:
        version = taxonomy_lock(s, expected_version)
        n = require(s, Node, id_)
        referenced = s.scalar(
            select(Classification.id).where(Classification.node_id == id_).limit(1)
        )
        manual = any(
            id_ in c.node_ids for c in s.scalars(select(PaperCategories)).all()
        )
        proposed = s.scalar(
            select(Proposal.id)
            .where(or_(Proposal.parent_id == id_, Proposal.resolved_node_id == id_))
            .limit(1)
        )
        children = s.scalar(select(Node.id).where(Node.parent_id == id_).limit(1))
        if children or referenced or manual or proposed:
            raise HTTPException(409, "分类仍有子节点、论文或建议引用；请先调整这些关联")
        if s.scalar(select(func.count()).select_from(Node)) <= 1:
            raise HTTPException(409, "至少保留一个分类节点")
        s.delete(n)
        s.flush()
        s.execute(update(Node).values(version=version + 1))
        return {"version": version + 1}


def effective_categories(s, paper):
    manual = s.get(PaperCategories, paper.id)
    if manual and manual.manual:
        return [
            {"node_id": n, "reason": "用户手动分类", "confidence": 1}
            for n in manual.node_ids
        ]
    return [
        asdict(c)
        for c in s.scalars(
            select(Classification).where(
                Classification.paper_id == paper.id,
                Classification.run_id == paper.current_run_id,
            )
        )
    ]


class PaperCategoryInput(BaseModel):
    node_ids: list[str] = Field(default_factory=list, max_length=100)
    expected_revision: int
    expected_taxonomy_version: int
    manual: bool = True


@app.put("/api/papers/{id_}/categories")
def edit_paper_categories(id_: str, data: PaperCategoryInput):
    with Session.begin() as s:
        taxonomy_lock(s, data.expected_taxonomy_version)
        require(s, Paper, id_)
        s.execute(update(Paper).where(Paper.id == id_).values(title=Paper.title))
        row = s.get(PaperCategories, id_)
        if (row.revision if row else 0) != data.expected_revision:
            raise HTTPException(409, "论文分类已变化，请重新打开详情")
        for node_id in data.node_ids:
            require(s, Node, node_id)
        if row is None:
            row = PaperCategories(paper_id=id_, revision=0)
            s.add(row)
        row.node_ids = list(dict.fromkeys(data.node_ids))
        row.manual = data.manual
        row.revision += 1
        return {"revision": row.revision}


@app.get("/api/papers")
def papers(
    q: str = "",
    technology: str = "",
    problem: str = "",
    venue: str = "",
    page: int = 1,
    page_size: int = 20,
):
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    with Session() as s:
        query = select(Paper)
        if q:
            term = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
            tech = (
                select(Run.paper_id)
                .join(Technique, Technique.run_id == Run.id)
                .where(
                    or_(
                        Technique.name.ilike(term, escape="\\"),
                        Technique.mechanism.ilike(term, escape="\\"),
                    ),
                    Run.id == Paper.current_run_id,
                )
                .correlate(Paper)
                .exists()
            )
            query = query.where(
                or_(
                    Paper.title.ilike(term, escape="\\"),
                    Paper.abstract.ilike(term, escape="\\"),
                    tech,
                )
            )
        nodes = s.scalars(select(Node)).all()
        overrides = s.scalars(
            select(PaperCategories).where(PaperCategories.manual == True)
        ).all()
        for id_ in [technology, problem]:
            if id_:
                subtree = descendants(nodes, id_)
                overridden = [r.paper_id for r in overrides]
                matched = [
                    r.paper_id for r in overrides if subtree.intersection(r.node_ids)
                ]
                query = query.where(
                    or_(
                        Paper.id.in_(matched),
                        (~Paper.id.in_(overridden))
                        & select(Classification.id)
                        .where(
                            Classification.paper_id == Paper.id,
                            Classification.run_id == Paper.current_run_id,
                            Classification.node_id.in_(subtree),
                        )
                        .exists(),
                    )
                )
        if venue:
            query = query.where(Paper.venue == venue)
        total = s.scalar(select(func.count()).select_from(query.subquery()))
        items = []
        for p in s.scalars(
            query.order_by(Paper.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ):
            item = asdict(p)
            item["classifications"] = [
                asdict(c)
                for c in s.scalars(
                    select(Classification).where(
                        Classification.paper_id == p.id,
                        Classification.run_id == p.current_run_id,
                    )
                )
            ]
            item["technique_count"] = (
                s.scalar(
                    select(func.count())
                    .select_from(Technique)
                    .where(Technique.run_id == p.current_run_id)
                )
                if p.current_run_id
                else 0
            )
            item["classifications"] = effective_categories(s, p)
            items.append(item)
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "facets": {"venues": s.scalars(select(Paper.venue).distinct()).all()},
        }


@app.get("/api/papers/{id_}")
def paper_detail(id_: str):
    with Session() as s:
        p = require(s, Paper, id_)
        result = asdict(p)
        manual = s.get(PaperCategories, id_)
        result["effective_categories"] = effective_categories(s, p)
        result["category_revision"] = manual.revision if manual else 0
        result["category_manual"] = manual.manual if manual else False
        r = s.get(Run, p.current_run_id) if p.current_run_id else None
        result["run"] = asdict(r) if r else None
        result["techniques"] = (
            [
                asdict(t)
                for t in s.scalars(
                    select(Technique).where(Technique.run_id == p.current_run_id)
                )
            ]
            if r
            else []
        )
        result["evidence"] = (
            [
                asdict(e)
                for e in s.scalars(
                    select(Evidence).where(Evidence.run_id == p.current_run_id)
                )
            ]
            if r
            else []
        )
        result["classifications"] = (
            [
                asdict(c)
                for c in s.scalars(
                    select(Classification).where(
                        Classification.run_id == p.current_run_id
                    )
                )
            ]
            if r
            else []
        )
        result["documents"] = [
            {k: v for k, v in asdict(d).items() if k != "pages"}
            for d in s.scalars(select(Document).where(Document.paper_id == id_))
        ]
        result["history"] = [
            {"id": r.id, "created_at": r.created_at, "model": r.model}
            for r in s.scalars(
                select(Run).where(Run.paper_id == id_).order_by(Run.created_at.desc())
            )
        ]
        return result


@app.get("/api/documents/{id_}/file")
def document_file(id_: str):
    with Session() as s:
        d = require(s, Document, id_)
        if not d.storage_name:
            raise HTTPException(404, "该文档没有 PDF 文件")
        path = (FILES / d.storage_name).resolve()
        if path.parent != FILES.resolve() or not path.exists():
            raise HTTPException(404, "文件不存在")
        return FileResponse(
            path,
            media_type="application/pdf",
            filename="paper.pdf",
            content_disposition_type="inline",
        )


@app.post("/api/papers/{id_}/reprocess", status_code=202)
def reprocess(id_: str):
    with Session() as s:
        p = require(s, Paper, id_)
        docs = s.scalars(select(Document).where(Document.paper_id == id_)).all()
        d = next((d for d in docs if d.kind == "fulltext"), None)
        payload = {
            k: v
            for k, v in asdict(p).items()
            if k in {"title", "doi", "authors", "year", "venue", "url", "abstract"}
        }
        payload["paper_id"] = id_
        payload["max_calls"] = 16
        payload["force"] = True
        if d:
            if d.storage_name:
                payload.update(storage_name=d.storage_name, sha256=d.sha256)
            else:
                payload["text"] = "\n\n".join(p["text"] for p in d.pages)
    return submit("archive", payload)


@app.get("/api/jobs")
def jobs():
    with Session() as s:
        return [
            public_job(j)
            for j in s.scalars(
                select(Job)
                .where(Job.parent_id == None)
                .order_by(Job.created_at.desc())
                .limit(100)
            )
        ]


@app.get("/api/jobs/{id_}")
def job_detail(id_: str):
    with Session() as s:
        j = require(s, Job, id_)
        return {
            **public_job(j),
            "children": [
                public_job(c)
                for c in s.scalars(select(Job).where(Job.parent_id == id_))
            ],
        }


@app.post("/api/jobs/{id_}/cancel")
def cancel(id_: str):
    with Session.begin() as s:
        j = require(s, Job, id_)
        if j.status in TERMINAL:
            raise HTTPException(409, "任务已结束")
        for item in [j] + s.scalars(select(Job).where(Job.parent_id == id_)).all():
            if item.status not in TERMINAL:
                item.cancel_requested = True
                if item.status in {"queued", "waiting"}:
                    item.status = "cancelled"
                    item.stage = "已取消"
        return {"status": "cancel_requested"}


@app.post("/api/jobs/{id_}/retry", status_code=202)
def retry(id_: str):
    with Session() as s:
        j = require(s, Job, id_)
        if j.status not in {"failed", "partial", "cancelled"}:
            raise HTTPException(409, "只有失败、部分完成或取消的任务可以重试")
        return submit(j.kind, j.payload, resume_from=j.id)


@app.get("/api/proposals")
def proposals():
    with Session() as s:
        return [asdict(p) for p in s.scalars(select(Proposal)).all()]


class ResolveInput(BaseModel):
    action: Literal["accept", "reject"]
    expected_version: int
    name: str = Field("", max_length=100)


@app.post("/api/proposals/{id_}/resolve")
def resolve_proposal(id_: str, data: ResolveInput):
    with Session.begin() as s:
        # Serialize taxonomy changes on the seed root for both SQLite and PostgreSQL.
        taxonomy_lock(s, data.expected_version)
        p = require(s, Proposal, id_)
        version = s.scalar(select(func.max(Node.version)))
        if version != data.expected_version or p.status != "pending":
            raise HTTPException(409, "分类版本已变化或建议已处理，请刷新")
        if data.action == "reject":
            p.status = "rejected"
            return {"version": version}
        name = data.name.strip() or p.name
        node = s.scalar(
            select(Node).where(
                Node.name == name,
                Node.dimension == p.dimension,
                Node.parent_id == p.parent_id,
            )
        )
        if not node:
            node = Node(
                id=uid(),
                name=name,
                dimension=p.dimension,
                parent_id=p.parent_id,
                version=version + 1,
            )
            s.add(node)
            s.flush()
        p.status = "accepted"
        p.resolved_node_id = node.id
        s.add(
            Classification(
                paper_id=p.paper_id,
                run_id=p.run_id,
                node_id=node.id,
                confidence=1,
                reason="用户确认分类扩展建议",
            )
        )
        return {"version": max(version, node.version), "node_id": node.id}


def public_job(job):
    result = asdict(job)
    result["payload"] = {k: v for k, v in job.payload.items() if k != "snapshot"}
    return result


class ReportInput(BaseModel):
    node_id: str
    report_kind: Literal["summary", "trends"] = "summary"
    year_from: int | None = Field(None, ge=1800, le=2200)
    year_to: int | None = Field(None, ge=1800, le=2200)
    max_papers: int = Field(60, ge=1, le=200)
    max_calls: int = Field(12, ge=1, le=40)

    @model_validator(mode="after")
    def valid(self):
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("起始年份不能晚于结束年份")
        return self


@app.get("/api/taxonomy/{id_}/report-scope")
def report_scope(id_: str):
    with Session() as s:
        require(s, Node, id_)
        snap = insights.snapshot(s, id_)
        return {"node_name": snap["node_name"], "stats": snap["stats"]}


@app.post("/api/reports", status_code=202)
def create_report(data: ReportInput):
    with Session() as s:
        require(s, Node, data.node_id)
        snap = insights.snapshot(
            s, data.node_id, data.year_from, data.year_to, data.max_papers
        )
    if not snap["sources"]:
        raise HTTPException(422, "该分类及子分类暂无可读论文；请先归档或调整论文分类")
    j = submit(
        "report",
        {
            **data.model_dump(),
            "title": snap["node_name"]
            + (" · 技术总结" if data.report_kind == "summary" else " · 发展趋势"),
            "snapshot": snap,
        },
    )
    return {"id": j["id"], "status": j["status"], "stats": snap["stats"]}


@app.get("/api/reports")
def reports(node_id: str = ""):
    with Session() as s:
        q = select(CategoryReport).order_by(CategoryReport.created_at.desc()).limit(50)
        if node_id:
            q = q.where(CategoryReport.node_id == node_id)
        return [
            {
                "id": r.id,
                "job_id": r.job_id,
                "node_id": r.node_id,
                "title": r.content["title"],
                "kind": r.kind,
                "status": r.status,
                "created_at": r.created_at,
                "stats": r.snapshot["stats"],
                "model": r.model,
            }
            for r in s.scalars(q)
        ]


@app.get("/api/reports/{id_}")
def report_detail(id_: str):
    with Session() as s:
        r = require(s, CategoryReport, id_)
        try:
            current = insights.snapshot(s, **r.scope)
            stale = current["fingerprint"] != r.snapshot["fingerprint"]
        except ValueError:
            stale = True
        return {**asdict(r), "stale": stale}


@app.get("/api/reports/{id_}/export")
def export_report(id_: str):
    with Session() as s:
        r = require(s, CategoryReport, id_)
        lines = [
            "# " + r.content["title"],
            "",
            f"范围：{r.snapshot['node_name']}及子分类 · {r.kind} · 模型：{r.model}",
            "",
        ]
        for w in r.warnings:
            lines += ["> " + w, ""]
        for section in r.content["sections"]:
            lines += ["## " + section["heading"], ""]
            for p in section["points"]:
                lines += [
                    "- "
                    + ("【推断】" if p["basis"] == "inference" else "【文献观察】")
                    + p["text"]
                    + " "
                    + "".join("[" + c + "]" for c in p["citations"]),
                    "",
                ]
        if r.content.get("timeline"):
            lines += ["## 发展时间线", ""]
            for p in r.content["timeline"]:
                lines += [
                    f"- {p['year']}：{p['text']} "
                    + "".join("[" + c + "]" for c in p["citations"]),
                    "",
                ]
        lines += (
            ["## 局限", ""]
            + ["- " + x for x in r.content["limitations"]]
            + ["", "## 来源", ""]
        )
        for p in r.snapshot["sources"]:
            lines += [
                f"- [{p['id']}] {p['title']} ({p['year'] or '年份未知'}) — {p['venue']}；{p['material_scope']}；{p['url']}"
            ]
        return Response(
            "\n".join(lines),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="report-{r.id}.md"'},
        )


dist = ROOT / "apps" / "web" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="web")
