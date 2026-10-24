import React, { useEffect, useState, useRef } from "react";
import { createRoot } from "react-dom/client";
import {
  BookOpen,
  Search,
  Upload,
  Activity,
  Network,
  Settings,
  ArrowUpRight,
  Plus,
  X,
  ChevronRight,
  FileText,
  Check,
  RefreshCw,
  Library,
  Layers,
  ArrowRight,
  LoaderCircle,
} from "lucide-react";
import "./style.css";
import KnowledgeTree, { PaperCategoryEditor, nodePath } from "./KnowledgeTree";

type Row = Record<string, any>;
type Node = {
  id: string;
  name: string;
  dimension: string;
  parent_id: string | null;
  version: number;
};
const statusNames: Row = {
  queued: "排队中",
  running: "执行中",
  waiting: "等待归档",
  partial: "部分完成",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};
const reading: Row = {
  metadata: "元数据",
  abstract: "摘要阅读",
  fulltext: "全文可读",
};
async function api(path: string, method = "GET", body?: unknown) {
  const res = await fetch("/api" + path, {
    method,
    headers:
      body instanceof FormData
        ? {}
        : body
          ? { "Content-Type": "application/json" }
          : {},
    body:
      body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail || data),
    );
  return data;
}
function safeUrl(url: string) {
  try {
    const x = new URL(url);
    return ["http:", "https:"].includes(x.protocol) ? url : "#";
  } catch {
    return "#";
  }
}
const dt = (n: number) =>
  new Date(n * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
function Badge({ status }: { status: string }) {
  return (
    <span className={"badge " + status}>{statusNames[status] || status}</span>
  );
}

function App() {
  const libraryRequest = useRef(0);
  const [view, setView] = useState("library"),
    [nodes, setNodes] = useState<Node[]>([]),
    [version, setVersion] = useState(1),
    [settings, setSettings] = useState<Row>({}),
    [flash, setFlash] = useState("");
  const [items, setItems] = useState<Row[]>([]),
    [total, setTotal] = useState(0),
    [venues, setVenues] = useState<string[]>([]),
    [filters, setFilters] = useState({
      q: "",
      technology: "",
      problem: "",
      venue: "",
    }),
    [page, setPage] = useState(1),
    [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<Row | null>(null),
    [jobs, setJobs] = useState<Row[]>([]),
    [job, setJob] = useState<Row | null>(null),
    [proposals, setProposals] = useState<Row[]>([]),
    [busy, setBusy] = useState(false);
  const [topic, setTopic] = useState(""),
    [searchForm, setSearchForm] = useState({
      year_from: "",
      year_to: "",
      limit: 5,
      max_calls: 16,
      crossref: true,
      arxiv: true,
    });
  const [importForm, setImportForm] = useState({
      title: "",
      doi: "",
      url: "",
      venue: "",
      year: "",
      abstract: "",
      text: "",
    }),
    [file, setFile] = useState<File | null>(null);
  function notify(e: unknown) {
    setFlash(e instanceof Error ? e.message : String(e));
  }
  async function loadTax() {
    const x = await api("/taxonomy");
    setNodes(x.nodes);
    setVersion(x.version);
  }
  async function loadLibrary() {
    const request = ++libraryRequest.current;
    setLoading(true);
    try {
      const x = await api(
        "/papers?" + new URLSearchParams({ ...filters, page: String(page) }),
      );
      if (request !== libraryRequest.current) return;
      setItems(x.items);
      setTotal(x.total);
      setVenues(x.facets.venues);
    } catch (e) {
      notify(e);
    } finally {
      if (request === libraryRequest.current) setLoading(false);
    }
  }
  async function loadJobs() {
    try {
      setJobs(await api("/jobs"));
      if (job) setJob(await api("/jobs/" + job.id));
    } catch (e) {
      notify(e);
    }
  }
  useEffect(() => {
    loadTax().catch(notify);
    api("/settings").then(setSettings).catch(notify);
  }, []);
  useEffect(() => {
    if (view === "library") loadLibrary();
  }, [view, filters, page]);
  useEffect(() => {
    if (view === "library") api("/proposals").then(setProposals).catch(notify);
  }, [view]);
  useEffect(() => {
    if (view !== "tasks") return;
    loadJobs();
    const id = setInterval(loadJobs, 2500);
    return () => clearInterval(id);
  }, [view, job?.id]);
  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(""), 10000);
    return () => clearTimeout(t);
  }, [flash]);
  async function inspect(id: string) {
    try {
      setDetail(await api("/papers/" + id));
    } catch (e) {
      notify(e);
    }
  }
  async function watch(j: Row) {
    setJob(await api("/jobs/" + j.id));
    setView("tasks");
  }
  async function search(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const j = await api("/search", "POST", {
        topic,
        sources: [
          ...(searchForm.crossref ? ["crossref"] : []),
          ...(searchForm.arxiv ? ["arxiv"] : []),
        ],
        year_from: searchForm.year_from ? Number(searchForm.year_from) : null,
        year_to: searchForm.year_to ? Number(searchForm.year_to) : null,
        limit: searchForm.limit,
        max_calls: searchForm.max_calls,
      });
      await watch(j);
    } catch (e) {
      notify(e);
    } finally {
      setBusy(false);
    }
  }
  async function importPaper(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      let j;
      if (file) {
        const f = new FormData();
        f.append("file", file);
        f.append("title", importForm.title);
        f.append("doi", importForm.doi);
        j = await api("/upload", "POST", f);
      } else {
        j = await api("/archive", "POST", {
          ...importForm,
          year: importForm.year ? Number(importForm.year) : null,
        });
      }
      await watch(j);
    } catch (e) {
      notify(e);
    } finally {
      setBusy(false);
    }
  }
  async function taskAction(id: string, action: string) {
    try {
      const x = await api("/jobs/" + id + "/" + action, "POST");
      if (action === "retry") await watch(x);
      await loadJobs();
    } catch (e) {
      notify(e);
    }
  }
  async function resolve(id: string, action: string) {
    try {
      await api("/proposals/" + id + "/resolve", "POST", {
        action,
        expected_version: version,
      });
      await loadTax();
      await loadLibrary();
      setProposals(await api("/proposals"));
    } catch (e) {
      notify(e);
    }
  }
  const label = (id: string) => nodes.find((n) => n.id === id)?.name || id;
  const nav = [
    { id: "library", name: "技术知识库", icon: Library },
    { id: "search", name: "论文搜索", icon: Search },
    { id: "import", name: "导入论文", icon: Upload },
    { id: "tasks", name: "任务中心", icon: Activity },
    { id: "settings", name: "运行配置", icon: Settings },
  ];
  function jobPanel(j: Row) {
    return (
      <div className="job-panel">
        <div className="section-head">
          <div>
            <span className="eyebrow">TASK DETAIL</span>
            <h2>{j.kind === "search" ? "搜索与自动归档" : "论文归档"}</h2>
          </div>
          <Badge status={j.status} />
        </div>
        <p className="job-topic">
          {j.payload.topic || j.payload.title || j.payload.doi || j.payload.url}
        </p>
        <div className="metric-row">
          <span>
            当前阶段<strong>{j.stage}</strong>
          </span>
          <span>
            模型调用
            <strong>
              {j.calls_used} / {j.max_calls}
            </strong>
          </span>
          <span>
            已返回 Token<strong>{j.tokens_used.toLocaleString()}</strong>
          </span>
        </div>
        {j.error && <div className="warning">{j.error}</div>}
        {j.result.warnings?.map((w: string, i: number) => (
          <div className="warning" key={i}>
            {w}
          </div>
        ))}
        {j.result.queries && (
          <p className="muted">检索式：{j.result.queries.join(" / ")}</p>
        )}
        {j.result.paper_id && (
          <button
            className="secondary"
            onClick={() => inspect(j.result.paper_id)}
          >
            查看归档论文 <ArrowUpRight size={15} />
          </button>
        )}
        {j.children?.length > 0 && (
          <>
            <h3>逐篇归档 · {j.children.length}</h3>
            {j.children.map((c: Row) => (
              <div className="child" key={c.id}>
                <div>
                  <strong>{c.payload.title}</strong>
                  <small>{c.stage}</small>
                  {c.result.warnings?.map((w: string, i: number) => (
                    <small className="warn-text" key={i}>
                      {w}
                    </small>
                  ))}
                </div>
                <Badge status={c.status} />
                {c.result.paper_id && (
                  <button
                    className="icon-button"
                    title="查看论文"
                    onClick={() => inspect(c.result.paper_id)}
                  >
                    <ArrowUpRight size={17} />
                  </button>
                )}
              </div>
            ))}
          </>
        )}
        {j.result.selected?.length > 0 && (
          <details>
            <summary>候选入选依据</summary>
            {j.result.selected.map((c: Row, i: number) => (
              <p key={i}>
                <b>{c.title}</b>
                <br />
                <span className="muted">
                  {c.reason} · {c.source}
                </span>
              </p>
            ))}
          </details>
        )}
        <div className="actions">
          {["queued", "running", "waiting"].includes(j.status) && (
            <button
              className="secondary"
              onClick={() => taskAction(j.id, "cancel")}
            >
              取消任务
            </button>
          )}
          {["partial", "failed", "cancelled"].includes(j.status) && (
            <button
              className="primary"
              onClick={() => taskAction(j.id, "retry")}
            >
              <RefreshCw size={15} />
              重试任务
            </button>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">
            <Layers size={24} />
          </div>
          <div>
            技术洞察<small>RESEARCH WORKBENCH</small>
          </div>
        </div>
        <div className="workspace">
          <span className="workspace-dot" />
          个人研究空间<span className="local-tag">LOCAL</span>
        </div>
        <nav>
          {nav.map((n) => (
            <button
              key={n.id}
              onClick={() => setView(n.id)}
              className={view === n.id ? "active" : ""}
            >
              <n.icon size={19} />
              {n.name}
              {view === n.id && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="nav-separator" />
        <div className="future">
          <span>知识库应用</span>
          <small>扩展探索 / 技术迁移</small>
          <span className="planned">后续规划</span>
        </div>
        <div className="sidebar-foot">
          <div
            className={"connection " + (settings.agent_ready ? "ready" : "")}
          >
            <i />
            {settings.agent_ready ? "Agent 已就绪" : "Agent 待配置"}
          </div>
          <span>从论文到可追溯的技术知识</span>
        </div>
      </aside>
      <main>
        <header>
          <div className="breadcrumb">
            工作空间 <ChevronRight size={14} />
            <strong>{nav.find((n) => n.id === view)?.name}</strong>
          </div>
          <span className="header-right">
            <span className="live-dot" />
            本地工作台 <span className="avatar">研</span>
          </span>
        </header>
        <div className="content">
          {view === "library" && (
            <>
              <div className="page-head">
                <div>
                  <span className="eyebrow">YOUR RESEARCH, CONNECTED</span>
                  <h1>
                    技术知识库<span className="count">{total}</span>
                  </h1>
                  <p>沿技术、问题与来源三个维度，组织你的研究积累。</p>
                </div>
                <button className="primary" onClick={() => setView("import")}>
                  <Plus size={18} />
                  导入论文
                </button>
              </div>
              <div className="overview">
                <div>
                  <BookOpen size={22} />
                  <span>
                    论文与原文证据<strong>每个技术结论，都有出处</strong>
                  </span>
                </div>
                <div>
                  <Network size={22} />
                  <span>
                    三维交叉归档<strong>技术域 · 问题域 · 来源域</strong>
                  </span>
                </div>
                <button onClick={() => setView("search")}>
                  发现新的研究
                  <ArrowUpRight size={20} />
                </button>
              </div>
              <div className="knowledge-layout">
                <KnowledgeTree
                  nodes={nodes}
                  version={version}
                  selected={filters}
                  onSelect={(dim, id) => {
                    setFilters((f) => ({ ...f, [dim]: id }));
                    setPage(1);
                  }}
                  api={api}
                  onError={notify}
                  onChange={async () => {
                    await loadTax();
                    await loadLibrary();
                    setProposals(await api("/proposals"));
                  }}
                />
                <div className="knowledge-results">
                  <div className="active-categories">
                    {(["technology", "problem"] as const)
                      .filter((dim) => filters[dim])
                      .map((dim) => (
                        <button
                          key={dim}
                          onClick={() => {
                            setFilters({ ...filters, [dim]: "" });
                            setPage(1);
                          }}
                        >
                          {nodePath(nodes, filters[dim])}
                          <X size={12} />
                        </button>
                      ))}
                  </div>
                  <div className="filterbar">
                    <label className="searchbox">
                      <Search size={18} />
                      <input
                        aria-label="搜索知识库"
                        placeholder="搜索论文标题、摘要或技术机制…"
                        value={filters.q}
                        onChange={(e) => {
                          setFilters({ ...filters, q: e.target.value });
                          setPage(1);
                        }}
                      />
                    </label>
                    <select
                      aria-label="来源域"
                      value={filters.venue}
                      onChange={(e) => {
                        setFilters({ ...filters, venue: e.target.value });
                        setPage(1);
                      }}
                    >
                      <option value="">全部来源</option>
                      {venues.map((v) => (
                        <option key={v}>{v}</option>
                      ))}
                    </select>
                  </div>
                  <div className="section-head">
                    <span className="muted">
                      {total} 篇论文 · 按归档时间排序
                    </span>
                    <button className="text-button" onClick={loadLibrary}>
                      <RefreshCw size={14} />
                      刷新
                    </button>
                  </div>
                  {loading ? (
                    <div className="empty">
                      <LoaderCircle className="spin" />
                      正在读取知识库…
                    </div>
                  ) : items.length === 0 ? (
                    <div className="empty">
                      <div className="empty-icon">
                        <BookOpen size={34} />
                      </div>
                      <h2>
                        {Object.values(filters).some(Boolean)
                          ? "没有符合条件的论文"
                          : "让第一篇论文成为研究的起点"}
                      </h2>
                      <p>
                        上传论文或发起主题搜索，自动整理技术方案与原文证据。
                      </p>
                      <div className="actions">
                        <button
                          className="primary"
                          onClick={() => setView("search")}
                        >
                          搜索论文 <ArrowRight size={16} />
                        </button>
                        <button
                          className="secondary"
                          onClick={() => setView("import")}
                        >
                          导入已有论文
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="paper-list">
                      {items.map((p) => (
                        <button
                          className="paper-card"
                          key={p.id}
                          onClick={() => inspect(p.id)}
                        >
                          <div className="paper-symbol">
                            <FileText size={24} />
                          </div>
                          <div className="paper-body">
                            <div className="paper-meta">
                              <span>{p.venue}</span>
                              <span>{p.year || "年份未知"}</span>
                              <span className="reading">
                                {reading[p.reading_level]}
                              </span>
                            </div>
                            <h2>{p.title}</h2>
                            <p>
                              {p.abstract || "尚未获取摘要，打开查看归档详情。"}
                            </p>
                            <div className="tags">
                              {[
                                ...new Set<string>(
                                  p.classifications.map((c: Row) => c.node_id),
                                ),
                              ]
                                .slice(0, 5)
                                .map((id) => (
                                  <span key={id}>{label(id)}</span>
                                ))}
                              <small>{p.technique_count} 个技术条目</small>
                            </div>
                          </div>
                          <ArrowUpRight className="paper-arrow" size={20} />
                        </button>
                      ))}
                    </div>
                  )}
                  {total > 20 && (
                    <div className="pagination">
                      <button
                        disabled={page === 1}
                        onClick={() => setPage(page - 1)}
                      >
                        上一页
                      </button>
                      <span>第 {page} 页</span>
                      <button
                        disabled={page * 20 >= total}
                        onClick={() => setPage(page + 1)}
                      >
                        下一页
                      </button>
                    </div>
                  )}
                  <details className="inline-proposals">
                    <summary>
                      Agent 分类建议 ·{" "}
                      {proposals.filter((p) => p.status === "pending").length}{" "}
                      项待处理
                    </summary>
                    <div className="section-head">
                      <h2>Agent 扩展建议</h2>
                      <span className="muted">
                        {proposals.filter((p) => p.status === "pending").length}{" "}
                        项待处理
                      </span>
                    </div>
                    {proposals.length === 0 ? (
                      <div className="quiet-empty">
                        暂无扩展建议。遇到新的技术方向时，Agent
                        会在这里提出建议。
                      </div>
                    ) : (
                      proposals.map((p) => (
                        <div className="proposal" key={p.id}>
                          <div>
                            <strong>{p.name}</strong>
                            <p>
                              {p.parent_id ? label(p.parent_id) : "根节点"} →{" "}
                              {p.name} ·{" "}
                              {p.dimension === "technology"
                                ? "技术域"
                                : "问题域"}
                            </p>
                            <small>{p.reason}</small>
                          </div>
                          {p.status === "pending" ? (
                            <div className="actions">
                              <button
                                className="secondary"
                                onClick={() => resolve(p.id, "reject")}
                              >
                                拒绝
                              </button>
                              <button
                                className="primary"
                                onClick={() => resolve(p.id, "accept")}
                              >
                                <Check size={15} />
                                接受
                              </button>
                            </div>
                          ) : (
                            <span className="badge">
                              {p.status === "accepted" ? "已接受" : "已拒绝"}
                            </span>
                          )}
                        </div>
                      ))
                    )}
                  </details>
                </div>
              </div>
            </>
          )}
          {view === "search" && (
            <>
              <div className="page-head">
                <div>
                  <span className="eyebrow">DISCOVER & ARCHIVE</span>
                  <h1>从一个问题，开始探索</h1>
                  <p>输入研究主题，自动检索论文、阅读内容并归档到知识库。</p>
                </div>
              </div>
              <form onSubmit={search} className="form-card">
                <label className="field-label">研究主题</label>
                <textarea
                  className="topic-input"
                  rows={3}
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  required
                  minLength={2}
                  placeholder="例如：Cache compression techniques for memory bandwidth optimization"
                />
                <div className="suggestions">
                  试试：
                  {[
                    "Cache prefetching",
                    "Memory compression",
                    "Compiler optimization",
                  ].map((t) => (
                    <button type="button" key={t} onClick={() => setTopic(t)}>
                      {t}
                    </button>
                  ))}
                </div>
                <div className="form-divider" />
                <div className="form-grid">
                  <label>
                    起始年份
                    <input
                      type="number"
                      min="1800"
                      max="2200"
                      placeholder="不限"
                      value={searchForm.year_from}
                      onChange={(e) =>
                        setSearchForm({
                          ...searchForm,
                          year_from: e.target.value,
                        })
                      }
                    />
                  </label>
                  <label>
                    结束年份
                    <input
                      type="number"
                      min="1800"
                      max="2200"
                      placeholder="不限"
                      value={searchForm.year_to}
                      onChange={(e) =>
                        setSearchForm({
                          ...searchForm,
                          year_to: e.target.value,
                        })
                      }
                    />
                  </label>
                  <label>
                    最多自动归档
                    <input
                      type="number"
                      min="1"
                      max="20"
                      value={searchForm.limit}
                      onChange={(e) =>
                        setSearchForm({
                          ...searchForm,
                          limit: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    模型调用上限
                    <input
                      type="number"
                      min="0"
                      max="40"
                      value={searchForm.max_calls}
                      onChange={(e) =>
                        setSearchForm({
                          ...searchForm,
                          max_calls: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                </div>
                <div className="source-options">
                  <span>检索来源</span>
                  <label>
                    <input
                      type="checkbox"
                      checked={searchForm.crossref}
                      onChange={(e) =>
                        setSearchForm({
                          ...searchForm,
                          crossref: e.target.checked,
                        })
                      }
                    />
                    Crossref
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={searchForm.arxiv}
                      onChange={(e) =>
                        setSearchForm({
                          ...searchForm,
                          arxiv: e.target.checked,
                        })
                      }
                    />
                    arXiv
                  </label>
                </div>
                {!settings.agent_ready && (
                  <div className="warning">
                    ACP agent 尚未就绪：可检索并保存论文，AI
                    技术提取需配置后重试。建议先使用英文检索词。
                  </div>
                )}
                <div className="form-footer">
                  <span>搜索后自动归档，无需逐篇确认。</span>
                  <button
                    className="primary"
                    disabled={
                      busy || (!searchForm.crossref && !searchForm.arxiv)
                    }
                  >
                    {busy ? (
                      <LoaderCircle className="spin" size={17} />
                    ) : (
                      <Search size={17} />
                    )}
                    开始搜索
                  </button>
                </div>
              </form>
              <div className="steps">
                <div>
                  <b>01</b>
                  <h3>发现论文</h3>
                  <p>检索学术来源，合并重复文献。</p>
                </div>
                <div>
                  <b>02</b>
                  <h3>阅读与提取</h3>
                  <p>提取技术机制，并保留原文依据。</p>
                </div>
                <div>
                  <b>03</b>
                  <h3>自动归档</h3>
                  <p>按技术、问题与发表来源组织知识。</p>
                </div>
              </div>
            </>
          )}
          {view === "import" && (
            <>
              <div className="page-head">
                <div>
                  <span className="eyebrow">BUILD YOUR KNOWLEDGE</span>
                  <h1>导入论文</h1>
                  <p>从 PDF、DOI、公开链接或已有文本开始，自动建立知识条目。</p>
                </div>
              </div>
              <form className="form-card" onSubmit={importPaper}>
                <label className="upload-area">
                  <Upload size={28} />
                  <strong>{file ? file.name : "选择一篇 PDF 论文"}</strong>
                  <span>最大 25 MB · 支持可提取文本的 PDF</span>
                  <input
                    aria-label="选择 PDF"
                    type="file"
                    accept="application/pdf,.pdf"
                    onChange={(e) => setFile(e.target.files?.[0] || null)}
                  />
                </label>
                {file && (
                  <button
                    className="text-button"
                    type="button"
                    onClick={() => setFile(null)}
                  >
                    移除文件，改用文本导入
                  </button>
                )}
                <div className="form-grid two">
                  <label>
                    论文标题
                    <input
                      value={importForm.title}
                      onChange={(e) =>
                        setImportForm({ ...importForm, title: e.target.value })
                      }
                      placeholder="上传 PDF 时可选"
                    />
                  </label>
                  <label>
                    DOI
                    <input
                      value={importForm.doi}
                      onChange={(e) =>
                        setImportForm({ ...importForm, doi: e.target.value })
                      }
                      placeholder="10.xxxx/xxxxx"
                    />
                  </label>
                </div>
                {!file && (
                  <>
                    <label className="field-label">
                      论文链接
                      <input
                        value={importForm.url}
                        onChange={(e) =>
                          setImportForm({ ...importForm, url: e.target.value })
                        }
                        placeholder="公开 PDF、arXiv 链接或论文页面"
                      />
                    </label>
                    <div className="form-grid two">
                      <label>
                        发表来源
                        <input
                          value={importForm.venue}
                          onChange={(e) =>
                            setImportForm({
                              ...importForm,
                              venue: e.target.value,
                            })
                          }
                          placeholder="会议 / 期刊（未知可留空）"
                        />
                      </label>
                      <label>
                        年份
                        <input
                          type="number"
                          value={importForm.year}
                          onChange={(e) =>
                            setImportForm({
                              ...importForm,
                              year: e.target.value,
                            })
                          }
                        />
                      </label>
                    </div>
                    <label className="field-label">
                      摘要
                      <textarea
                        rows={4}
                        value={importForm.abstract}
                        onChange={(e) =>
                          setImportForm({
                            ...importForm,
                            abstract: e.target.value,
                          })
                        }
                        placeholder="粘贴原始摘要；仅有摘要时会明确标记阅读范围"
                      />
                    </label>
                    <details>
                      <summary>直接粘贴论文正文</summary>
                      <textarea
                        aria-label="论文正文"
                        rows={7}
                        value={importForm.text}
                        onChange={(e) =>
                          setImportForm({ ...importForm, text: e.target.value })
                        }
                      />
                    </details>
                  </>
                )}
                <div className="form-footer">
                  <span>分类与技术总结自动入库，原文证据可追溯。</span>
                  <button className="primary" disabled={busy}>
                    {busy ? (
                      <LoaderCircle size={16} className="spin" />
                    ) : (
                      <Plus size={16} />
                    )}
                    导入并归档
                  </button>
                </div>
              </form>
            </>
          )}
          {view === "tasks" && (
            <>
              <div className="page-head">
                <div>
                  <span className="eyebrow">AGENT ACTIVITY</span>
                  <h1>任务中心</h1>
                  <p>查看搜索、阅读与归档进展，保留每一步的结果与异常。</p>
                </div>
                <button className="secondary" onClick={loadJobs}>
                  <RefreshCw size={16} />
                  刷新
                </button>
              </div>
              {jobs.length === 0 ? (
                <div className="empty">
                  <Activity size={34} />
                  <h2>还没有任务</h2>
                  <p>导入论文或发起搜索后，执行进度会显示在这里。</p>
                </div>
              ) : (
                <div className="task-layout">
                  <div className="task-list">
                    {jobs.map((j) => (
                      <button
                        className={
                          "task-card " + (job?.id === j.id ? "selected" : "")
                        }
                        key={j.id}
                        onClick={() =>
                          api("/jobs/" + j.id)
                            .then(setJob)
                            .catch(notify)
                        }
                      >
                        <div>
                          <span>
                            {j.kind === "search" ? "论文搜索" : "论文归档"}
                          </span>
                          <Badge status={j.status} />
                        </div>
                        <strong>
                          {j.payload.topic ||
                            j.payload.title ||
                            j.payload.doi ||
                            "论文导入"}
                        </strong>
                        <small>
                          {dt(j.created_at)} · {j.stage}
                        </small>
                      </button>
                    ))}
                  </div>
                  {job ? (
                    jobPanel(job)
                  ) : (
                    <div className="empty">
                      <p>选择一个任务查看详情</p>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
          {view === "settings" && (
            <>
              <div className="page-head">
                <div>
                  <span className="eyebrow">WORKSPACE SETTINGS</span>
                  <h1>运行配置</h1>
                  <p>模型调用通过本机 ACP agent 会话执行，凭证由 agent 自己管理。</p>
                </div>
              </div>
              <div className="form-card">
                <div className="section-head">
                  <h2>Agent 连接</h2>
                  <Badge status={settings.agent_ready ? "已配置" : "待配置"} />
                </div>
                <p className="muted">
                  在项目根目录 .env 中配置 ACP_COMMAND 与可选 ACP_MODEL，随后重启
                  API 与 Worker。
                </p>
                <pre>
                  ACP_COMMAND=opencode acp{"\n"}
                  ACP_MODEL=arkchat/doubao-seed-2.1-pro
                </pre>
                <div className="settings-row">
                  <span>连接方式</span>
                  <strong>ACP 子进程（{settings.agent_mode || "acp"}）</strong>
                </div>
                <div className="settings-row">
                  <span>Agent 命令</span>
                  <strong>{settings.agent_command || "未配置"}</strong>
                </div>
                <div className="settings-row">
                  <span>当前模型</span>
                  <strong>{settings.agent_model || "由 agent 默认决定"}</strong>
                </div>
                <div className="settings-row">
                  <span>模型凭证</span>
                  <strong>由 agent（opencode）管理</strong>
                </div>
                <div className="settings-row">
                  <span>后台 Worker</span>
                  <strong>
                    {settings.worker_running
                      ? "运行中"
                      : "未检测到，请检查启动日志"}
                  </strong>
                </div>
                <div className="settings-row">
                  <span>存储模式</span>
                  <strong>{settings.database}</strong>
                </div>
                <div className="settings-row">
                  <span>检索方式</span>
                  <strong>{settings.retrieval}</strong>
                </div>
                <div className="settings-row">
                  <span>扫描件 OCR</span>
                  <strong>未启用</strong>
                </div>
                <p className="muted">
                  长文将分批处理，任务记录实际覆盖范围。当前版本未实现向量语义检索与模块三。
                </p>
                <button
                  className="secondary"
                  onClick={() =>
                    api("/settings").then(setSettings).catch(notify)
                  }
                >
                  <RefreshCw size={15} />
                  刷新状态
                </button>
              </div>
            </>
          )}
        </div>
      </main>
      {detail && (
        <div className="modal-backdrop" onClick={() => setDetail(null)}>
          <section
            className="detail"
            role="dialog"
            aria-modal="true"
            aria-label="论文详情"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="detail-top">
              <span className="eyebrow">PAPER INSIGHT</span>
              <button
                className="icon-button"
                aria-label="关闭详情"
                onClick={() => setDetail(null)}
              >
                <X />
              </button>
            </div>
            <div className="paper-meta">
              {detail.venue} · {detail.year || "年份未知"}{" "}
              <span className="reading">{reading[detail.reading_level]}</span>
            </div>
            <h1>{detail.title}</h1>
            <p className="muted">{detail.authors.join(" · ")}</p>
            <div className="actions">
              {detail.url && (
                <a
                  className="secondary"
                  href={safeUrl(detail.url)}
                  target="_blank"
                  rel="noreferrer"
                >
                  原始来源
                  <ArrowUpRight size={15} />
                </a>
              )}
              {detail.documents
                .filter((d: Row) => d.storage_name)
                .map((d: Row) => (
                  <a
                    key={d.id}
                    className="secondary"
                    href={"/api/documents/" + d.id + "/file"}
                    target="_blank"
                    rel="noreferrer"
                  >
                    查看 PDF
                  </a>
                ))}
              <button
                className="secondary"
                onClick={async () => {
                  try {
                    const j = await api(
                      "/papers/" + detail.id + "/reprocess",
                      "POST",
                    );
                    setDetail(null);
                    await watch(j);
                  } catch (e) {
                    notify(e);
                  }
                }}
              >
                <RefreshCw size={15} />
                重新分析
              </button>
            </div>
            <PaperCategoryEditor
              key={detail.id}
              paper={detail}
              nodes={nodes}
              version={version}
              api={api}
              onError={notify}
              onSaved={async () => {
                await inspect(detail.id);
                await loadLibrary();
              }}
            />
            <h2>研究概要</h2>
            <p className="summary-text">
              {detail.run?.summary ||
                detail.abstract ||
                "暂无可读内容。可补充原文后重新分析。"}
            </p>
            {!detail.run && (
              <div className="warning">
                尚未生成 AI 技术分析，请在任务中心查看原因或配置模型后重新分析。
              </div>
            )}
            {detail.run?.limitations.map((w: string, i: number) => (
              <div className="warning" key={i}>
                {w}
              </div>
            ))}
            <h2>
              技术方案 <span className="count">{detail.techniques.length}</span>
            </h2>
            {detail.techniques.map((t: Row, i: number) => (
              <article className="technique" key={t.id}>
                <span className="tech-number">
                  TECHNIQUE {String(i + 1).padStart(2, "0")}
                </span>
                <h3>{t.name}</h3>
                <p>{t.mechanism}</p>
                <div className="tech-grid">
                  <div>
                    <label>解决的问题</label>
                    <p>{t.problem}</p>
                  </div>
                  <div>
                    <label>适用条件</label>
                    <p>{t.conditions}</p>
                  </div>
                </div>
                <label>局限与边界</label>
                <p>{t.limitations}</p>
                <div className="tags">
                  {detail.classifications
                    .filter((c: Row) => c.technique_id === t.id)
                    .map((c: Row) => (
                      <span title={c.reason} key={c.id}>
                        {label(c.node_id)} · {Math.round(c.confidence * 100)}%
                      </span>
                    ))}
                </div>
                <details open>
                  <summary>原文证据 · {t.claims.length}</summary>
                  {t.claims.map((id: string) => {
                    const e = detail.evidence.find((e: Row) => e.id === id);
                    return (
                      e && (
                        <blockquote key={id}>
                          {e.quote}
                          <footer>
                            {e.page ? "第 " + e.page + " 页" : "摘要"} ·{" "}
                            {e.block_id}
                          </footer>
                        </blockquote>
                      )
                    );
                  })}
                </details>
              </article>
            ))}
            {detail.run && (
              <p className="muted">
                分析模型：{detail.run.model} · 分类版本{" "}
                {detail.run.taxonomy_version} · {detail.history.length}{" "}
                次成功处理记录
              </p>
            )}
          </section>
        </div>
      )}
      {flash && (
        <div className="toast" role="alert">
          <span>{flash}</span>
          <button
            className="icon-button"
            onClick={() => setFlash("")}
            aria-label="关闭提示"
          >
            <X size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
