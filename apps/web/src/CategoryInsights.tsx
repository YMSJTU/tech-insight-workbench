import { useEffect, useRef, useState } from "react";
import {
  FileText,
  TrendingUp,
  X,
  Download,
  ArrowUpRight,
  RefreshCw,
} from "lucide-react";
import { TaxNode, nodePath } from "./KnowledgeTree";
type Props = {
  nodes: TaxNode[];
  nodeId: string;
  onNode: (id: string) => void;
  api: (path: string, method?: string, body?: unknown) => Promise<any>;
  onJob: (job: any) => Promise<void>;
  onPaper: (id: string) => void;
  onError: (e: unknown) => void;
  reportId?: string;
};
export default function CategoryInsights({
  nodes,
  nodeId,
  onNode,
  api,
  onJob,
  onPaper,
  onError,
  reportId,
}: Props) {
  const [reports, setReports] = useState<any[]>([]),
    [report, setReport] = useState<any>(null),
    [stats, setStats] = useState<any>(null),
    [busy, setBusy] = useState(false);
  const [options, setOptions] = useState({
    year_from: "",
    year_to: "",
    max_papers: 60,
    max_calls: 12,
  });
  const request = useRef(0);
  useEffect(() => {
    const n = ++request.current;
    setStats(null);
    setReports([]);
    if (!nodeId) return;
    Promise.all([
      api("/reports?node_id=" + nodeId),
      api("/taxonomy/" + nodeId + "/report-scope"),
    ])
      .then(([r, s]) => {
        if (n === request.current) {
          setReports(r);
          setStats(s.stats);
        }
      })
      .catch(onError);
  }, [nodeId]);
  useEffect(() => {
    if (reportId)
      api("/reports/" + reportId)
        .then(setReport)
        .catch(onError);
  }, [reportId]);
  async function generate(kind: string) {
    setBusy(true);
    try {
      const job = await api("/reports", "POST", {
        node_id: nodeId,
        report_kind: kind,
        ...options,
        year_from: options.year_from ? Number(options.year_from) : null,
        year_to: options.year_to ? Number(options.year_to) : null,
      });
      await onJob(job);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  async function open(id: string) {
    try {
      setReport(await api("/reports/" + id));
    } catch (e) {
      onError(e);
    }
  }
  function citations(ids: string[]) {
    return (
      <span className="report-citations">
        {ids.map((id) => {
          const s = report.snapshot.sources.find((s: any) => s.id === id);
          return (
            <button
              key={id}
              title={s?.title}
              onClick={() => s && onPaper(s.paper_id)}
            >
              [{id}]
            </button>
          );
        })}
      </span>
    );
  }
  return (
    <section className="category-insights">
      <div className="section-head">
        <div>
          <span className="eyebrow">CATEGORY RESEARCH</span>
          <h2>技术总结与发展趋势</h2>
        </div>
        <span className="badge">OpenCode ACP</span>
      </div>
      <p className="muted">
        按技术方向、上级分类或问题域汇总论文，包含全部子分类；每条结论可追溯到论文。
      </p>
      <label className="field-label">
        报告范围
        <select
          aria-label="报告分类范围"
          value={nodeId}
          onChange={(e) => onNode(e.target.value)}
        >
          <option value="">请选择技术域或问题域</option>
          {["technology", "problem"].map((dim) => (
            <optgroup
              key={dim}
              label={dim === "technology" ? "技术域" : "问题域"}
            >
              {nodes
                .filter((n) => n.dimension === dim)
                .map((n) => (
                  <option key={n.id} value={n.id}>
                    {nodePath(nodes, n.id)}
                  </option>
                ))}
            </optgroup>
          ))}
        </select>
      </label>
      {stats && (
        <p className="scope-stats">
          分类内 {stats.matched} 篇 · 可读 {stats.readable} 篇 · 仅题录{" "}
          {stats.metadata_only} 篇 · {stats.years.length} 个发表年份
        </p>
      )}
      <details>
        <summary>年份、论文数量与调用预算</summary>
        <div className="form-grid">
          <label>
            起始年份
            <input
              type="number"
              value={options.year_from}
              min="1800"
              max="2200"
              onChange={(e) =>
                setOptions({ ...options, year_from: e.target.value })
              }
            />
          </label>
          <label>
            结束年份
            <input
              type="number"
              value={options.year_to}
              min="1800"
              max="2200"
              onChange={(e) =>
                setOptions({ ...options, year_to: e.target.value })
              }
            />
          </label>
          <label>
            最多论文
            <input
              type="number"
              min="1"
              max="200"
              value={options.max_papers}
              onChange={(e) =>
                setOptions({ ...options, max_papers: Number(e.target.value) })
              }
            />
          </label>
          <label>
            ACP 调用上限
            <input
              type="number"
              min="1"
              max="40"
              value={options.max_calls}
              onChange={(e) =>
                setOptions({ ...options, max_calls: Number(e.target.value) })
              }
            />
          </label>
        </div>
        <p className="muted">
          超过数量上限时跨年份采样。报告基于知识库样本，不等同于整个领域的全量综述。
        </p>
      </details>
      <div className="actions">
        <button
          className="primary"
          disabled={!nodeId || busy || !stats?.readable}
          onClick={() => generate("summary")}
        >
          <FileText size={15} />
          生成技术总结
        </button>
        <button
          className="secondary"
          disabled={!nodeId || busy || !stats?.readable}
          onClick={() => generate("trends")}
        >
          <TrendingUp size={15} />
          生成发展趋势
        </button>
      </div>
      {nodeId && stats && !stats.readable && (
        <p className="warning">
          此分类尚无可读论文。先在论文详情修改分类，或完成论文归档，再生成报告。
        </p>
      )}
      {nodeId && (
        <details className="report-history" open={reports.length > 0}>
          <summary>历史报告 · {reports.length}</summary>
          {reports.length === 0 ? (
            <p className="muted">暂无报告。生成任务完成后可从任务中心查看。</p>
          ) : (
            reports.map((r) => (
              <button
                key={r.id}
                className="report-history-row"
                onClick={() => open(r.id)}
              >
                <span>
                  {r.kind === "trends" ? "趋势" : "总结"} · {r.title}
                  <small>
                    {new Date(r.created_at * 1000).toLocaleString("zh-CN")} ·{" "}
                    {r.stats.used} 篇样本
                  </small>
                </span>
                <ArrowUpRight size={15} />
              </button>
            ))
          )}
        </details>
      )}
      {report && (
        <div
          className="modal-backdrop report-backdrop"
          onClick={() => setReport(null)}
        >
          <section
            className="detail report-detail"
            role="dialog"
            aria-modal="true"
            aria-label="分类研究报告"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="detail-top">
              <span className="eyebrow">
                {report.kind === "trends"
                  ? "TECHNOLOGY TRENDS"
                  : "TECHNOLOGY SYNTHESIS"}
              </span>
              <button
                aria-label="关闭报告"
                className="icon-button"
                onClick={() => setReport(null)}
              >
                <X />
              </button>
            </div>
            <h1>{report.content.title}</h1>
            <p className="muted">
              {report.snapshot.node_name}及全部子分类 · {report.model} ·{" "}
              {new Date(report.created_at * 1000).toLocaleString("zh-CN")}
            </p>
            <div className="actions">
              <a
                className="secondary"
                href={"/api/reports/" + report.id + "/export"}
                download
              >
                <Download size={15} />
                导出 Markdown
              </a>
              <button className="secondary" onClick={() => open(report.id)}>
                <RefreshCw size={15} />
                检查资料变更
              </button>
            </div>
            {report.stale && (
              <div className="warning">
                分类或论文资料已变化。本报告保留生成时的快照，可重新生成新版本。
              </div>
            )}
            <div className="metric-row">
              <span>
                范围论文<strong>{report.snapshot.stats.matched}</strong>
              </span>
              <span>
                输入样本<strong>{report.snapshot.stats.used}</strong>
              </span>
              <span>
                实际完成
                <strong>
                  {report.snapshot.generated_source_ids?.length ??
                    report.snapshot.stats.used}
                </strong>
              </span>
            </div>
            {report.warnings.map((w: string, i: number) => (
              <p className="report-note" key={i}>
                {w}
              </p>
            ))}
            {report.content.sections.map((s: any, i: number) => (
              <section className="report-section" key={i}>
                <h2>{s.heading}</h2>
                {s.points.map((p: any, j: number) => (
                  <p key={j}>
                    <span className={"basis " + p.basis}>
                      {p.basis === "inference" ? "推断" : "文献观察"}
                    </span>
                    {p.text}
                    {citations(p.citations)}
                  </p>
                ))}
              </section>
            ))}
            {report.content.timeline?.length > 0 && (
              <section className="report-section">
                <h2>发展时间线</h2>
                {[...report.content.timeline]
                  .sort((a: any, b: any) => a.year - b.year)
                  .map((p: any, i: number) => (
                    <div className="timeline-item" key={i}>
                      <strong>{p.year}</strong>
                      <p>
                        <span className={"basis " + p.basis}>
                          {p.basis === "inference" ? "推断" : "文献观察"}
                        </span>
                        {p.text}
                        {citations(p.citations)}
                      </p>
                    </div>
                  ))}
              </section>
            )}
            <section className="report-section">
              <h2>局限与待验证问题</h2>
              {report.content.limitations.map((x: string, i: number) => (
                <p key={i}>{x}</p>
              ))}
            </section>
            <section className="report-section">
              <h2>来源清单与阅读范围</h2>
              {report.snapshot.sources.map((s: any) => (
                <div className="report-source" key={s.id}>
                  <button onClick={() => onPaper(s.paper_id)}>
                    [{s.id}] {s.title}
                    <ArrowUpRight size={12} />
                  </button>
                  <small>
                    {s.year || "年份未知"} · {s.venue} · {s.material_scope}
                  </small>
                  {s.evidence?.length > 0 && (
                    <details>
                      <summary>报告输入的原文证据摘录</summary>
                      {s.evidence.map((e: any, i: number) => (
                        <blockquote key={i}>
                          {e.quote}
                          <footer>
                            {e.page ? "第 " + e.page + " 页" : "摘要"}
                          </footer>
                        </blockquote>
                      ))}
                    </details>
                  )}
                </div>
              ))}
            </section>
          </section>
        </div>
      )}
    </section>
  );
}
