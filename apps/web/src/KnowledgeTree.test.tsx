// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import KnowledgeTree, { PaperCategoryEditor, TaxNode } from "./KnowledgeTree";

const nodes: TaxNode[] = [
  {
    id: "arch",
    name: "体系结构",
    dimension: "technology",
    parent_id: null,
    version: 1,
  },
  {
    id: "cache",
    name: "Cache 系统",
    dimension: "technology",
    parent_id: "arch",
    version: 1,
  },
  {
    id: "prefetch",
    name: "Cache 预取",
    dimension: "technology",
    parent_id: "cache",
    version: 1,
  },
  {
    id: "deep",
    name: "深层预取",
    dimension: "technology",
    parent_id: "prefetch",
    version: 1,
  },
  {
    id: "compiler",
    name: "编译器",
    dimension: "technology",
    parent_id: null,
    version: 1,
  },
  {
    id: "performance",
    name: "性能优化",
    dimension: "problem",
    parent_id: null,
    version: 1,
  },
];
const props = () => ({
  nodes,
  version: 1,
  selected: { technology: "", problem: "" },
  onSelect: vi.fn(),
  api: vi.fn().mockResolvedValue({}),
  onChange: vi.fn().mockResolvedValue(undefined),
  onError: vi.fn(),
});
beforeEach(() => localStorage.clear());
afterEach(cleanup);

test("recursive expand, selection and expansion persistence", async () => {
  const p = props();
  const user = userEvent.setup();
  const view = render(<KnowledgeTree {...p} />);
  expect(
    screen.queryByRole("button", { name: "深层预取" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "展开Cache 预取" }));
  await user.click(
    screen.getByRole("button", { name: "深层预取" }),
  );
  expect(p.onSelect).toHaveBeenCalledWith("technology", "deep");
  view.unmount();
  render(<KnowledgeTree {...p} />);
  expect(
    screen.getByRole("button", { name: "深层预取" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "全部收起" }));
  expect(
    screen.queryByRole("button", { name: "Cache 预取" }),
  ).toBeNull();
});

test("edit excludes own subtree as parent and submits chosen hierarchy", async () => {
  const p = props();
  const user = userEvent.setup();
  render(<KnowledgeTree {...p} />);
  await user.click(screen.getByRole("button", { name: "编辑Cache 系统" }));
  const dialog = screen.getByRole("dialog");
  const parent = within(dialog).getByLabelText("所属父级");
  expect(
    within(parent).queryByRole("option", { name: "体系结构 / Cache 系统" }),
  ).toBeNull();
  await user.selectOptions(parent, "compiler");
  await user.clear(within(dialog).getByLabelText("分类名称"));
  await user.type(within(dialog).getByLabelText("分类名称"), "缓存技术");
  await user.click(within(dialog).getByRole("button", { name: "保存分类" }));
  await waitFor(() =>
    expect(p.api).toHaveBeenCalledWith(
      "/taxonomy/cache",
      "PUT",
      expect.objectContaining({
        name: "缓存技术",
        parent_id: "compiler",
        expected_version: 1,
      }),
    ),
  );
});

test("new child uses selected parent", async () => {
  const p = props();
  const user = userEvent.setup();
  render(<KnowledgeTree {...p} />);
  await user.click(
    screen.getByRole("button", { name: "新增Cache 系统的子分类" }),
  );
  await user.type(screen.getByLabelText("分类名称"), "Cache 压缩");
  await user.click(screen.getByRole("button", { name: "保存分类" }));
  await waitFor(() =>
    expect(p.api).toHaveBeenCalledWith(
      "/taxonomy",
      "POST",
      expect.objectContaining({ name: "Cache 压缩", parent_id: "cache" }),
    ),
  );
});

test("manual paper classification saves and can restore AI mode", async () => {
  const p = props();
  const user = userEvent.setup();
  const paper = {
    id: "paper1",
    category_revision: 0,
    category_manual: false,
    effective_categories: [],
  };
  render(
    <PaperCategoryEditor
      paper={paper}
      nodes={nodes}
      version={1}
      api={p.api}
      onSaved={p.onChange}
      onError={p.onError}
    />,
  );
  await user.click(screen.getByRole("button", { name: "修改分类" }));
  await user.click(screen.getByRole("checkbox", { name: "编译器" }));
  await user.click(screen.getByRole("button", { name: "保存论文分类" }));
  await waitFor(() =>
    expect(p.api).toHaveBeenCalledWith(
      "/papers/paper1/categories",
      "PUT",
      expect.objectContaining({
        node_ids: ["compiler"],
        manual: true,
        expected_revision: 0,
      }),
    ),
  );
  await user.click(screen.getByRole("button", { name: "修改分类" }));
  await user.click(screen.getByRole("button", { name: "恢复 AI 分类" }));
  await waitFor(() =>
    expect(p.api).toHaveBeenLastCalledWith(
      "/papers/paper1/categories",
      "PUT",
      expect.objectContaining({ manual: false }),
    ),
  );
});
