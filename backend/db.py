import time
import uuid
from sqlalchemy import (
    create_engine,
    String,
    Text,
    Integer,
    Float,
    Boolean,
    JSON,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import DATABASE_URL


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Version(Base):
    __tablename__ = "schema_versions"
    version: Mapped[int] = mapped_column(primary_key=True)


class Paper(Base):
    __tablename__ = "papers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    identity: Mapped[str] = mapped_column(String(600), unique=True)
    title: Mapped[str] = mapped_column(Text)
    doi: Mapped[str] = mapped_column(Text, default="")
    authors: Mapped[list] = mapped_column(JSON, default=list)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    venue: Mapped[str] = mapped_column(Text, default="未知来源")
    url: Mapped[str] = mapped_column(Text, default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    reading_level: Mapped[str] = mapped_column(String(20), default="metadata")
    coverage_note: Mapped[str] = mapped_column(Text, default="")
    current_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("paper_id", "sha256"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    paper_id: Mapped[str] = mapped_column(String(36), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_name: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20))
    pages: Mapped[list] = mapped_column(JSON, default=list)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    paper_id: Mapped[str] = mapped_column(String(36), index=True)
    document_id: Mapped[str] = mapped_column(String(36))
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(String(30), default="extract-v1")
    taxonomy_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="succeeded")
    summary: Mapped[str] = mapped_column(Text)
    limitations: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Technique(Base):
    __tablename__ = "techniques"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(Text)
    mechanism: Mapped[str] = mapped_column(Text)
    problem: Mapped[str] = mapped_column(Text)
    conditions: Mapped[str] = mapped_column(Text, default="未说明")
    limitations: Mapped[str] = mapped_column(Text, default="未说明")
    claims: Mapped[list] = mapped_column(JSON, default=list)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    document_id: Mapped[str] = mapped_column(String(36))
    block_id: Mapped[str] = mapped_column(String(40))
    page: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)


class Node(Base):
    __tablename__ = "taxonomy"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    dimension: Mapped[str] = mapped_column(String(20))
    parent_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)


class Classification(Base):
    __tablename__ = "classifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    paper_id: Mapped[str] = mapped_column(String(36), index=True)
    technique_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_id: Mapped[str] = mapped_column(String(36))
    node_id: Mapped[str] = mapped_column(String(80), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)


class PaperCategories(Base):
    __tablename__ = "paper_categories"
    paper_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_ids: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    manual: Mapped[bool] = mapped_column(Boolean, default=True)


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    paper_id: Mapped[str] = mapped_column(String(36))
    run_id: Mapped[str] = mapped_column(String(36))
    dimension: Mapped[str] = mapped_column(String(20))
    parent_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    name: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    resolved_node_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class CategoryReport(Base):
    __tablename__ = "category_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(String(36), unique=True)
    node_id: Mapped[str] = mapped_column(String(80), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="succeeded")
    scope: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)
    content: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(String(30), default="category-v1")
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (UniqueConstraint("job_id", "name", "input_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(100))
    input_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(80))
    output: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(20))
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    stage: Mapped[str] = mapped_column(Text, default="等待执行")
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    calls_used: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    max_calls: Mapped[int] = mapped_column(Integer, default=16)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30}
    if DATABASE_URL.startswith("sqlite")
    else {},
    pool_pre_ping=True,
)
if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def sqlite_setup(connection, _):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")


Session = sessionmaker(engine, expire_on_commit=False)

SEED = [
    ("arch", "technology", None, "体系结构"),
    ("core", "technology", "arch", "核与微架构"),
    ("cache", "technology", "arch", "Cache 系统"),
    ("prefetch", "technology", "cache", "Cache 预取"),
    ("replacement", "technology", "cache", "Cache 替换"),
    ("cache-compression", "technology", "cache", "Cache 压缩"),
    ("memory", "technology", "arch", "内存"),
    ("media", "technology", "memory", "内存介质"),
    ("memory-access", "technology", "memory", "访问效率优化"),
    ("memory-compression", "technology", "memory", "容量压缩"),
    ("compiler", "technology", None, "编译器"),
    ("os", "technology", None, "操作系统"),
    ("software", "technology", None, "软件与算法"),
    ("performance", "problem", None, "性能优化"),
    ("fetch", "problem", "performance", "取指优化"),
    ("execution", "problem", "performance", "执行效率优化"),
    ("memory-perf", "problem", "performance", "访存系统优化"),
    ("latency", "problem", "memory-perf", "访问延迟"),
    ("bandwidth", "problem", "memory-perf", "带宽利用"),
    ("locality", "problem", "memory-perf", "数据局部性"),
    ("energy", "problem", None, "低功耗与节能"),
    ("cost", "problem", None, "存储成本优化"),
    ("capacity", "problem", "cost", "容量需求降低"),
    ("media-cost", "problem", "cost", "存储介质成本降低"),
]


def ensure_columns():
    """Add columns introduced after schema v1 without rebuilding user data."""
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import OperationalError

    columns = {c["name"] for c in inspect(engine).get_columns("papers")}
    if "coverage_note" not in columns:
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE papers ADD COLUMN coverage_note TEXT DEFAULT ''")
                )
        except OperationalError as exc:
            # API and Worker may migrate the same database at startup.
            message = str(exc).lower()
            if "duplicate column" not in message and "already exists" not in message:
                raise


def init_db():
    Base.metadata.create_all(engine)
    ensure_columns()
    with Session.begin() as s:
        if not s.get(Version, 1):
            s.add(Version(version=1))
            for id_, dim, parent, name in SEED:
                s.add(Node(id=id_, dimension=dim, parent_id=parent, name=name))


def asdict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
