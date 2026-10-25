# 技术洞察 · AI Agent 论文工作台

第一版已实现模块一（论文搜索）和模块二（技术知识库）。模块三只保留规划入口。

## 功能

- 两种搜索模式：按主题（Crossref / arXiv / OpenAlex，关键词召回 + ACP 语义重排）与按会议/期刊（OpenAlex 全量目录，按年份范围发现全部论文）。
- 能下载全文时保存 PDF；拿不到全文时归档摘要，连摘要也没有则保存元数据，并在论文条目中标注信息范围与原因。
- PDF 解析使用 PyMuPDF 保留正文、表格、嵌入图片和含图表页面的版面渲染，并生成 Markdown（正文 + GFM 表格 + 图片/版面链接）；论文详情可打开渲染后的“解析 Markdown”。
- 上传 PDF，导入 DOI、arXiv / PDF 链接、摘要或正文；ACP agent 按 Markdown 分块阅读、技术提取和原文证据校验。
- 技术域、问题域、来源域交叉查询；论文详情包含技术机制、适用条件、局限与证据。
- 正式技术树和问题树；Agent 提出新节点建议，接受或拒绝不阻塞论文归档。
- 知识库内统一管理两棵分类树：逐级展开、新增、重命名、拖动调整父级；论文支持手动修改分类或恢复 AI 分类。
- 独立 Worker、持久化父子任务、进度、取消、重试、租约恢复、共享 ACP prompt 预算。
- 重复文献与已成功处理内容复用；补充全文或重新分析保留历史成功结果。
- 对任意技术方向、上级分类或问题域生成技术总结/发展趋势：OpenCode ACP 分批归纳、逐条论文引用、历史版本、资料变更提示和 Markdown 导出。

没有可用的 ACP agent 时，可检索、导入和保存文献，但不会生成虚假的技术分析。任务明确显示部分完成，可配置 agent 后重试。

## 搜索方式

- **按主题**：输入研究主题与年份范围；Crossref、arXiv、OpenAlex 召回候选，先做关键词排序，再由 ACP agent 对前 30 个候选做完整相关性重排，并按可配置阈值筛选（默认 0.45）。未评分候选不会自动通过语义筛选；模型预算不足时明确退回关键词排序。
- **按会议/期刊**：输入会议或期刊名（如 `HPCA`）与年份；OpenAlex 解析来源并抓取该范围内的全部论文，自动过滤版权页、委员会名单等非论文条目，再按“最多自动归档”数量入库。
- **信息范围**：全文 PDF 下载并解析成功记为 `全文`；拿不到可下载全文但存在摘要记为 `摘要`；两者都没有记为 `元数据`。论文详情页显示 `coverage_note`，任务详情逐篇显示同样标记。
- **解析产物**：每篇 PDF 生成 Markdown，表格转成 GFM 表格，图片和版面渲染以 `assets/...` 链接保留；论文详情可打开 Markdown，后续 AI 分块直接读取它。
- **PDF 候选链**：按 OpenAlex 全部 locations、arXiv、Unpaywall（需配置 email）、OpenAlex DOI 回查依次尝试；下载器校验 content-type/PDF 文件头，区分 403/404/429/5xx/非 PDF，并按候选记录尝试结果。
- **覆盖率指标**：会议/期刊任务显示“发现 / 预期 / 覆盖率”，归档完成后显示全文、摘要、元数据、PDF 候选论文数、下载尝试与成功数。
- 批量入库可把模型调用上限设为 0：只抓取与归档文献，不调用 ACP agent；任务会标为“部分完成”，之后可对需要的论文重新分析。

## 当前本机运行

默认地址：<http://127.0.0.1:8765>。API 文档：<http://127.0.0.1:8765/docs>。

项目已创建 `.venv` 并安装依赖、构建前端。启动与停止：

```powershell
# 在项目根目录执行
.\scripts\start.ps1
.\scripts\stop.ps1
```

启动脚本在后台启动 API 和 Worker，不打开命令行窗口。日志位于 `data/api-error.log` 和 `data/worker-error.log`。不要同时运行多个 Worker。运行配置页显示 Worker 最近心跳。

## 配置 ACP Agent

模型调用不直连 HTTP API，而是由 Worker 启动本机 ACP agent（默认 `opencode acp`），每次提取在独立 session 中完成。模型与凭证由 agent 自己管理，项目只保存 agent 命令与可选模型 ID。

复制 `.env.example` 为 `.env`，按需配置：

```dotenv
ACP_COMMAND=opencode acp
ACP_MODEL=arkchat/doubao-seed-2.1-pro
ACP_HOME=
ACP_TIMEOUT=300
```

- `ACP_COMMAND`：agent 可执行命令；Windows 下若 `opencode` 不在 PATH，可写完整 `opencode.exe` 路径。
- `ACP_MODEL`：agent 暴露的模型 ID；留空使用 agent 默认模型。模型必须在当前 agent 配置中存在。
- `ACP_HOME`：可选的隔离 HOME 目录（相对项目根目录）。设置后 agent 的配置、认证、日志都写在该目录，适合受限环境；使用前需把 agent 的配置与认证复制到该目录。
- `ACP_TIMEOUT`：单次 prompt 的超时秒数。

修改配置后停止并重新启动服务。浏览器不会收到模型凭证。agent 读取的是论文文本，数据发送范围由 agent 的模型提供方决定。

搜索任务的 max_calls 是父任务及所有子任务共同的 ACP prompt 次数上限，重试也计数；不是精确货币预算。agent 返回 usage 时记录 token，未返回时不伪造估计。

PDF 抓取相关配置：

```dotenv
UNPAYWALL_EMAIL=
PDF_MAX_CANDIDATES=3
PDF_RENDER_DPI=110
```

- `UNPAYWALL_EMAIL`：填写后启用 Unpaywall 开放获取 PDF 查询；留空则只使用 OpenAlex/arXiv 候选。
- `PDF_MAX_CANDIDATES`：每篇论文最多尝试的 PDF 候选数（1–5）。
- `PDF_RENDER_DPI`：含图片/表格/矢量图页面的版面渲染 DPI（72–160）。

## 新环境安装

需要 Python 3.12+、Node.js 20+，以及可用的 ACP agent（如 opencode）。当前机器默认 `python` 为旧版本，不应拿它创建本项目环境；请明确选择 Python 3.12+。本机验证使用 opencode 2.1.2；agent 的模型提供方与授权需在 agent 侧单独完成。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
npm ci --prefix apps/web
npm run build --prefix apps/web
.\scripts\start.ps1
```

如需前端热更新，运行 `npm run dev --prefix apps/web`，后端 API 仍在 8765；Vite 将 `/api` 转发给 API。

手动启动进程（两个终端）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
.\.venv\Scripts\python.exe -m backend.worker
```

## 数据与存储

默认 `data/workbench.db`（SQLite WAL）与 `data/files/`。备份时停止服务后复制整个 `data` 目录。`data/agent-home` 保存 ACP agent 的配置、认证与日志，备份或共享 `data` 时应按敏感数据处理。

可通过 `DATABASE_URL=postgresql+psycopg://user:password@localhost/workbench` 使用 PostgreSQL；需要提前创建数据库。本次验证覆盖 SQLite，未连接 PostgreSQL 做集成验收。schema 当前为 v1，不应在没有迁移脚本时直接替换旧表结构。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check backend tests scripts --select F
npm run build --prefix apps/web
# 只读真实来源连通性探测
.\.venv\Scripts\python.exe -m scripts.probe_sources
# 临时数据库中验证真实搜索、PDF 下载及解析；禁用付费模型调用
.\.venv\Scripts\python.exe -m scripts.verify_real_pipeline
```

目前 66 项后端测试、7 项前端组件测试通过；测试数据库位于临时目录，不写入用户知识库。前端组件测试命令：在 `apps/web` 下执行 `npx vitest run`。真实来源检索、PDF 解析、ACP 归类与双模式搜索结果见历史验证记录；新增分类报告的真实 ACP 验证见 [分类报告与检索优化验证](docs/10-分类报告与检索优化验证.md)；P0 搜索/下载与图表保留验证见 [P0 搜索与下载优化](docs/12-P0搜索与下载优化.md)。没有完成实际页面视觉与点击验收。

## 当前限制

- 默认单机单用户、一个 Worker、一个 ACP agent 进程；不是已完成的多用户或分布式平台。
- 检索未实现向量语义检索；主题模式的关键词召回可能带入同名不同领域论文，目前依赖 ACP 语义重排纠偏。
- 不支持扫描 PDF 的 OCR；解析器保留文本、表格、嵌入图片和含图表页面的版面渲染，但无线表格可能漏检；获取失败会保留可读摘要或元数据，并说明原因。
- PDF 版面渲染会占用额外磁盘空间；含图表页面按 `PDF_RENDER_DPI` 生成 JPEG，可按需调低或清理。
- Unpaywall 查询需要配置 `UNPAYWALL_EMAIL`；未配置时仅使用 OpenAlex/arXiv 候选，全文产出率会相应降低。
- 最多 300 页 PDF、25 MB 文件；agent 读取最多前 32 个正文块，超过范围明确提示。独立参考文献节会被过滤；跨批技术先按规范化名称合并，再在预算允许时通过 ACP 归并，保留原始证据。
- 分类报告基于知识库样本与有界内容摘录，默认最多 60 篇（可设为 1–200），并非全领域穷尽综述；年份或资料不足时会明确限制趋势判断。
- ACP agent 必须在本机可用；单个 prompt 串行执行，长文批次耗时取决于 agent 与模型。
- 同一论文的复杂出版版本归并、跨论文技术实体合并、独立实验结果结构化和全库重分类尚未实现。
- 通用出版方 HTML 页面不保证可抽取正文，优先使用 DOI、arXiv、公开 PDF 或上传文件。
- 模块三的扩展探索与技术迁移验证未实现。

## 文档

- [4+1 系统架构](docs/02-系统架构4+1视图.md)
- [模块二详细设计](docs/03-模块二详细设计.md)
- [模块一详细设计](docs/04-模块一详细设计.md)
- [实现映射、验证记录与差异](docs/05-实现与验证记录.md)
- [ACP 真实归类测试记录](docs/07-ACP真实归类测试记录.md)
- [双模式搜索测试记录](docs/08-双模式搜索测试记录.md)
- [分类总结与趋势设计](docs/09-分类总结与趋势设计.md)
- [分类报告与检索优化验证](docs/10-分类报告与检索优化验证.md)
- [P0 搜索与下载优化](docs/12-P0搜索与下载优化.md)
- [知识库与分类树统一交互](docs/06-知识库与分类树交互.md)
- [初始需求背景](docs/01-系统架构设计.md)
