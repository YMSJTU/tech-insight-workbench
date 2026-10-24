import { useState, useEffect } from "react";
import {
  ChevronRight,
  ChevronDown,
  Plus,
  Pencil,
  X,
  Trash2,
} from "lucide-react";

export type TaxNode = {
  id: string;
  name: string;
  dimension: string;
  parent_id: string | null;
  version: number;
};
type Props = {
  nodes: TaxNode[];
  version: number;
  selected: { technology: string; problem: string };
  onSelect: (dim: string, id: string) => void;
  api: (path: string, method?: string, body?: unknown) => Promise<any>;
  onChange: () => Promise<void>;
  onError: (e: unknown) => void;
};

export function nodePath(nodes: TaxNode[], id: string): string {
  const parts: string[] = [];
  const seen = new Set<string>();
  while (id && !seen.has(id)) {
    seen.add(id);
    const n = nodes.find((n) => n.id === id);
    if (!n) break;
    parts.unshift(n.name);
    id = n.parent_id || "";
  }
  return parts.join(" / ");
}
export default function KnowledgeTree({
  nodes,
  version,
  selected,
  onSelect,
  api,
  onChange,
  onError,
}: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    try {
      const saved = JSON.parse(
        localStorage.getItem("knowledge-tree-expanded") || "null",
      );
      if (Array.isArray(saved))
        return new Set(
          saved.filter((x) => typeof x === "string"),
        ) as Set<string>;
    } catch {
      /* Use defaults if browser storage is unavailable. */
    }
    return new Set(["arch", "cache", "performance", "memory-perf"]);
  });
  useEffect(() => {
    try {
      localStorage.setItem(
        "knowledge-tree-expanded",
        JSON.stringify([...expanded]),
      );
    } catch {
      /* Storage is optional. */
    }
  }, [expanded]);
  const [editor, setEditor] = useState<{
    id?: string;
    name: string;
    dimension: string;
    parent_id: string;
    expected_version: number;
  } | null>(null);
  const [busy, setBusy] = useState(false),
    [confirmDelete, setConfirmDelete] = useState(false),
    [drag, setDrag] = useState<string | null>(null);
  function edit(n: TaxNode) {
    setEditor({
      ...n,
      parent_id: n.parent_id || "",
      expected_version: version,
    });
    setConfirmDelete(false);
  }
  function create(dim: string, parent_id = "") {
    setEditor({
      name: "",
      dimension: dim,
      parent_id,
      expected_version: version,
    });
    setConfirmDelete(false);
  }
  function toggle(id: string) {
    setExpanded((old) => {
      const n = new Set(old);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }
  const excluded = new Set(editor?.id ? [editor.id] : []);
  let changed = true;
  while (changed) {
    changed = false;
    for (const n of nodes)
      if (n.parent_id && excluded.has(n.parent_id) && !excluded.has(n.id)) {
        excluded.add(n.id);
        changed = true;
      }
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!editor) return;
    setBusy(true);
    try {
      await api(
        "/taxonomy" + (editor.id ? "/" + editor.id : ""),
        editor.id ? "PUT" : "POST",
        { ...editor, parent_id: editor.parent_id || null },
      );
      if (editor.parent_id)
        setExpanded((s) => new Set([...s, editor.parent_id]));
      setEditor(null);
      await onChange();
    } catch (e) {
      onError(e);
      await onChange();
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!editor?.id) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setBusy(true);
    try {
      await api(
        "/taxonomy/" +
          editor.id +
          "?expected_version=" +
          editor.expected_version,
        "DELETE",
      );
      if (selected[editor.dimension as "technology" | "problem"] === editor.id)
        onSelect(editor.dimension, "");
      setEditor(null);
      await onChange();
    } catch (e) {
      onError(e);
      await onChange();
    } finally {
      setBusy(false);
    }
  }
  function branch(
    dim: string,
    parent: string | null = null,
    depth = 0,
  ): React.ReactNode {
    return nodes
      .filter((n) => n.dimension === dim && n.parent_id === parent)
      .map((n) => {
        const children = nodes.some((c) => c.parent_id === n.id),
          open = expanded.has(n.id);
        return (
          <li key={n.id}>
            <div
              className={
                "category-row " +
                (selected[dim as "technology" | "problem"] === n.id
                  ? "chosen"
                  : "")
              }
              style={{ paddingLeft: 8 + depth * 17 }}
              draggable
              onDragStart={(e) => {
                setDrag(n.id);
                e.dataTransfer.setData("text/plain", n.id);
              }}
              onDragEnd={() => setDrag(null)}
              onDragOver={(e) => {
                if (drag && drag !== n.id) e.preventDefault();
              }}
              onDrop={(e) => {
                e.preventDefault();
                const moved = nodes.find((x) => x.id === drag);
                setDrag(null);
                if (moved && moved.dimension === dim) {
                  edit(moved);
                  setEditor({
                    ...moved,
                    parent_id: n.id,
                    expected_version: version,
                  });
                } else onError("不能跨技术域与问题域移动");
              }}
            >
              <button
                className="tree-toggle"
                aria-label={(open ? "收起" : "展开") + n.name}
                aria-expanded={children ? open : undefined}
                disabled={!children}
                onClick={() => toggle(n.id)}
              >
                {children ? (
                  open ? (
                    <ChevronDown size={14} />
                  ) : (
                    <ChevronRight size={14} />
                  )
                ) : (
                  <span className="tree-dot" />
                )}
              </button>
              <button
                className="category-name"
                title={nodePath(nodes, n.id)}
                onClick={() => onSelect(dim, n.id)}
              >
                {n.name}
              </button>
              <div className="category-tools">
                <button
                  aria-label={"新增" + n.name + "的子分类"}
                  title="新增子分类"
                  onClick={() => create(dim, n.id)}
                >
                  <Plus size={13} />
                </button>
                <button
                  aria-label={"编辑" + n.name}
                  title="编辑名称与层级"
                  onClick={() => edit(n)}
                >
                  <Pencil size={12} />
                </button>
              </div>
            </div>
            {children && open && <ul>{branch(dim, n.id, depth + 1)}</ul>}
          </li>
        );
      });
  }
  return (
    <aside className="knowledge-tree">
      <div className="tree-heading">
        <strong>知识分类</strong>
        <span>v{version}</span>
      </div>
      <p className="tree-help">点击分类筛选论文，拖动节点调整父级。</p>
      <div className="tree-global">
        <button onClick={() => setExpanded(new Set(nodes.map((n) => n.id)))}>
          全部展开
        </button>
        <button onClick={() => setExpanded(new Set())}>全部收起</button>
      </div>
      {["technology", "problem"].map((dim) => (
        <section key={dim}>
          <div className="dimension-head">
            <strong>{dim === "technology" ? "技术域" : "问题域"}</strong>
            <button
              className="icon-button"
              aria-label={
                "新增" + (dim === "technology" ? "技术域" : "问题域") + "根分类"
              }
              onClick={() => create(dim)}
            >
              <Plus size={15} />
            </button>
          </div>
          <button
            className={
              "all-category " +
              (!selected[dim as "technology" | "problem"] ? "selected" : "")
            }
            onClick={() => onSelect(dim, "")}
          >
            全部{dim === "technology" ? "技术方向" : "研究问题"}
          </button>
          <ul className="category-tree">{branch(dim)}</ul>
        </section>
      ))}
      {editor && (
        <div
          className="modal-backdrop editor-backdrop"
          onClick={() => !busy && setEditor(null)}
        >
          <form
            className="node-editor"
            onSubmit={save}
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label="编辑分类"
          >
            <div className="section-head">
              <h2>{editor.id ? "编辑分类与层级" : "新增分类"}</h2>
              <button
                type="button"
                className="icon-button"
                disabled={busy}
                onClick={() => setEditor(null)}
                aria-label="关闭分类编辑"
              >
                <X size={18} />
              </button>
            </div>
            <label className="field-label">
              分类名称
              <input
                autoFocus
                required
                maxLength={100}
                value={editor.name}
                onChange={(e) => setEditor({ ...editor, name: e.target.value })}
              />
            </label>
            <label className="field-label">
              所属父级
              <select
                value={editor.parent_id}
                onChange={(e) =>
                  setEditor({ ...editor, parent_id: e.target.value })
                }
              >
                <option value="">根层级</option>
                {nodes
                  .filter(
                    (n) =>
                      n.dimension === editor.dimension && !excluded.has(n.id),
                  )
                  .map((n) => (
                    <option key={n.id} value={n.id}>
                      {nodePath(nodes, n.id)}
                    </option>
                  ))}
              </select>
            </label>
            <p className="muted">
              移动分类会一并移动其全部子分类；已有论文关联保持不变。
            </p>
            {confirmDelete && (
              <div className="warning">
                确认删除此分类？仅允许删除没有子节点和引用的空分类。
              </div>
            )}
            <div className="form-footer">
              {editor.id ? (
                <button
                  type="button"
                  className="secondary danger"
                  disabled={busy}
                  onClick={remove}
                >
                  <Trash2 size={14} />
                  {confirmDelete ? "确认删除" : "删除空分类"}
                </button>
              ) : (
                <span />
              )}
              <button
                className="primary"
                disabled={busy || !editor.name.trim()}
              >
                保存分类
              </button>
            </div>
          </form>
        </div>
      )}
    </aside>
  );
}

export function PaperCategoryEditor({
  paper,
  nodes,
  version,
  api,
  onSaved,
  onError,
}: {
  paper: any;
  nodes: TaxNode[];
  version: number;
  api: Props["api"];
  onSaved: () => Promise<void>;
  onError: Props["onError"];
}) {
  const [editing, setEditing] = useState(false),
    [chosen, setChosen] = useState<string[]>([]),
    [busy, setBusy] = useState(false);
  async function save(manual = true) {
    setBusy(true);
    try {
      await api("/papers/" + paper.id + "/categories", "PUT", {
        node_ids: chosen,
        manual,
        expected_revision: paper.category_revision,
        expected_taxonomy_version: version,
      });
      setEditing(false);
      await onSaved();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="paper-category-editor">
      <div className="section-head">
        <h2>论文分类</h2>
        <button
          className="secondary"
          onClick={() => {
            setChosen(paper.effective_categories.map((c: any) => c.node_id));
            setEditing(!editing);
          }}
        >
          {editing ? "取消编辑" : "修改分类"}
        </button>
      </div>
      <div className="tags">
        {paper.effective_categories?.map((c: any) => (
          <span key={c.node_id}>{nodePath(nodes, c.node_id)}</span>
        ))}
        {!paper.effective_categories?.length && <span>未分类</span>}
      </div>
      <p className="muted">
        {paper.category_manual
          ? "采用手动分类，重新分析不会覆盖。"
          : "采用当前 AI 归档分类。"}
      </p>
      {editing && (
        <>
          <div className="category-checkboxes">
            {["technology", "problem"].map((dim) => (
              <div key={dim}>
                <h3>{dim === "technology" ? "技术域" : "问题域"}</h3>
                {nodes
                  .filter((n) => n.dimension === dim)
                  .map((n) => (
                    <label key={n.id}>
                      <input
                        type="checkbox"
                        checked={chosen.includes(n.id)}
                        onChange={(e) =>
                          setChosen(
                            e.target.checked
                              ? [...chosen, n.id]
                              : chosen.filter((id) => id !== n.id),
                          )
                        }
                      />
                      {nodePath(nodes, n.id)}
                    </label>
                  ))}
              </div>
            ))}
          </div>
          <div className="actions">
            <button className="primary" disabled={busy} onClick={() => save()}>
              保存论文分类
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => save(false)}
            >
              恢复 AI 分类
            </button>
          </div>
        </>
      )}
    </section>
  );
}
