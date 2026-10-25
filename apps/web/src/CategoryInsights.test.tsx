// @vitest-environment jsdom
import React from "react";
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CategoryInsights from "./CategoryInsights";
afterEach(cleanup);
const nodes = [
  {
    id: "arch",
    name: "体系结构",
    dimension: "technology",
    parent_id: null,
    version: 1,
  },
];
const stats = {
  matched: 3,
  readable: 2,
  metadata_only: 1,
  used: 2,
  years: [2022, 2024],
};
function props() {
  return {
    nodes,
    nodeId: "arch",
    onNode: vi.fn(),
    api: vi.fn(async (path: string) =>
      path.includes("report-scope") ? { stats } : [],
    ),
    onJob: vi.fn().mockResolvedValue(undefined),
    onPaper: vi.fn(),
    onError: vi.fn(),
  };
}

test("scope reports submit through persistent report job", async () => {
  const p = props();
  p.api = vi.fn(async (path: string) =>
    path.includes("report-scope")
      ? { stats }
      : path === "/reports"
        ? { id: "job-1" }
        : [],
  ) as any;
  render(<CategoryInsights {...p} />);
  const user = userEvent.setup();
  await screen.findByText(/可读 2 篇/);
  await user.click(screen.getByRole("button", { name: "生成发展趋势" }));
  expect(p.api).toHaveBeenCalledWith(
    "/reports",
    "POST",
    expect.objectContaining({
      node_id: "arch",
      report_kind: "trends",
      max_calls: 12,
      max_papers: 60,
    }),
  );
  expect(p.onJob).toHaveBeenCalledWith({ id: "job-1" });
});

test("empty category cannot generate title-only research", async () => {
  const p = props();
  p.api = vi.fn(async (path: string) =>
    path.includes("report-scope") ? { stats: { ...stats, readable: 0 } } : [],
  ) as any;
  render(<CategoryInsights {...p} />);
  await screen.findByText(/此分类尚无可读论文/);
  expect(
    (screen.getByRole("button", { name: "生成技术总结" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});

test("saved report shows staleness and opens cited paper", async () => {
  const p = props();
  const report = {
    id: "r1",
    kind: "trends",
    created_at: 1,
    model: "acp-model",
    stale: true,
    warnings: [],
    snapshot: {
      node_name: "体系结构",
      stats,
      sources: [
        {
          id: "S001",
          paper_id: "p1",
          title: "A paper",
          year: 2022,
          venue: "CONF",
          material_scope: "原始摘要",
        },
      ],
    },
    content: {
      title: "方向趋势",
      sections: [
        {
          heading: "技术路线",
          points: [
            { text: "A finding", basis: "observed", citations: ["S001"] },
          ],
        },
      ],
      timeline: [],
      limitations: ["样本有限"],
    },
  };
  p.api = vi.fn(async (path: string) =>
    path === "/reports/r1"
      ? report
      : path.includes("report-scope")
        ? { stats }
        : [],
  ) as any;
  render(<CategoryInsights {...p} reportId="r1" />);
  const user = userEvent.setup();
  await screen.findByText(/分类或论文资料已变化/);
  await user.click(
    screen.getByRole("button", { name: "[S001]", exact: true } as any),
  );
  expect(p.onPaper).toHaveBeenCalledWith("p1");
  expect(
    screen.getByRole("link", { name: "导出 Markdown" }).getAttribute("href"),
  ).toBe("/api/reports/r1/export");
  await user.click(screen.getByRole("button", { name: "关闭报告" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
