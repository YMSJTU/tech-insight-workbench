# 技术洞察 · AI Agent 论文工作台

第一版已实现模块一（论文搜索）和模块二（技术知识库）。模块三只保留规划入口。

## 功能

- 搜索 Crossref、arXiv；查询规划、候选精确去重、年份筛选、可解释排序、入选论文自动归档。
- 上传 PDF，导入 DOI、arXiv / PDF 链接、摘要或正文；文档解析、ACP agent 分批阅读、技术提取和原文证据校验。
- 技术域、问题域、来源域交叉查询；论文详情包含技术机制、适用条件、局限与证据。
- 正式技术树和问题树；Agent 提出新节点建议，接受或拒绝不阻塞论文归档。
- 知识库内统一管理两棵分类树：逐级展开、新增、重命名、拖动调整父级；论文支持手动修改分类或恢复 AI 分类。
- 独立 Worker、持久化父子任务、进度、取消、重试、租约恢复、共享模型调用预算。
- 重复文献与已成功处理内容复用；补充全文或重新分析保留历史成功结果。

没有可用的 ACP agent 时，可检索、导入和保存文献，但不会生成虚假的技术分析。任务明确显示部分完成，可配置 agent 后重试。

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

目前 34 项后端测试、4 项分类树前端组件测试通过；测试数据库位于临时目录，不写入用户知识库。前端组件测试命令：在 `apps/web` 下执行 `npx vitest run`。真实来源检索和 PDF 解析已通过；ACP 真实归类验收结果见 [ACP 真实归类测试记录](docs/07-ACP真实归类测试记录.md)。浏览器访问权限检查未通过，因此没有完成实际页面视觉与点击验收。

## 当前限制

- 默认单机单用户、一个 Worker、一个 ACP agent 进程；不是已完成的多用户或分布式平台。
- 检索为文本与结构化分类筛选，暂未实现向量语义检索。
- 不支持扫描 PDF 的 OCR；获取失败会保留可读摘要或元数据，并说明原因。
- 最多 300 页 PDF、25 MB 文件；agent 读取最多前 32 个文本块，超过范围明确提示。分批技术条目尚未做跨批语义合并，参考文献页也可能被当作技术来源。
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
- [知识库与分类树统一交互](docs/06-知识库与分类树交互.md)
- [初始需求背景](docs/01-系统架构设计.md)
