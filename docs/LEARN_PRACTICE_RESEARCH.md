# 学 → 练 → 研究：Quantlerning × QuantLab × PaperPulse 贯穿链路方案

> 状态：方案稿（评审用）｜日期：2026-09-08｜范围：产品/技术架构方案，**不改代码**
> 本文档基于三个仓库实读（README/CODEBUDDY/路由/组件/CI），端点路径以各方 `/docs` OpenAPI 为准，落地前复核。

## 0. 三件产品，在链路里的角色

| 环节 | 产品（缩写） | 一句话定位 | 承担角色 | 现状缺口 |
|---|---|---|---|---|
| 学 | **Quantlerning**（ql） | 可视化量化教材：66 课 + 题库 + 代码沙箱 + 模拟器 | 概念学习 + 基础练习 | 终点是 LabView 模拟器，无真实挖掘/回测出口；进度只存 localStorage |
| 练/研 | **QuantLab**（qlb） | 真实 A 股上的因子研究平台：因子库/挖掘/评价/规则回测/组合/蒙特卡罗 | 真实数据上的"把假设做成因子并回测" | 无课程入口、无文献入口；结果"做完即止"，不回流入学习与选题 |
| 研 | **PaperPulse / paper_hot**（ph） | 经管论文智脑：1.5 万篇 + RAG + 选题验证/综述/期刊适配 | 研究上游：选题、文献、方法论、发表 | 纯文献工具，不含量化执行；"论文里的结论无法亲测" |

**技术底子（现状）**

| | Quantlerning | QuantLab | PaperPulse |
|---|---|---|---|
| 后端 | FastAPI + SQLAlchemy(async) | FastAPI + SQLAlchemy(async) | FastAPI + SQLAlchemy(SQLite, aiosqlite) |
| 前端 | Vue3 + TS + Vite + ECharts + KaTeX | Vue3 + Element Plus + Pinia | Next.js14 + React18 + Tailwind + D3 |
| 数据 | **只读复用 QuantLab 的 PostgreSQL**（quantlab 库） | qlib `.day.bin` + PostgreSQL（数据权威） | 独立 SQLite（`paperpulse.db`，约 509MB） |
| 端口 | FE 5173 / BE 8100 | FE 3001 / BE 8101 | FE 3000 / BE 8000 |
| 鉴权 | 无账号（本地） | AUTH_ENABLED 开关 + JWT | 轻量 `x-user-id` / `x-api-token`（可关） |
| 现有互通 | 复用同一 PG；仅有到 QuantLab 的外链 | — | CORS 允许任意 localhost 端口 |

**核心观察**：三者其实是"同一件事"的三段——数据同源（ql 读 qlb 的库），人同源（都是本机作者本人），但目前**人、结果、知识都不互通**。打通它们不需要新造一个平台，只需在边界上接上 5 条"主动脉"（见 §2）。

---

## 1. 贯穿主线：一条"研究驱动"的闭环

主线采用**选题驱动**（ph 在最上游），而非线性课程化——研究者先被问题吸引，再按需补知识，符合成人自学/研究者的真实路径：

```
  ┌───────────────────────────────────────────────────────────────┐
  │  PaperPulse（研·选题）                                          │
  │  读文献 → topic-validator/方法手册 → 产生"可检验的假设"            │
  └──────────────────────────────┬────────────────────────────────┘
                                  │ ① 假设里出现不懂的概念
                                  ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  Quantlerning（学 + 练基础）                                    │
  │  概念字典定位课程 → 读课/quiz 建立概念 → 应用题为可理解            │
  │  沙箱(CodeSandbox)用真实行情重现论文图示/小样本验证（练直觉）        │
  └──────────────────────────────┬────────────────────────────────┘
                                  │ ② 概念已过关 → 上真实数据
                                  ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  QuantLab（练真实 + 研）                                        │
  │  把假设翻译成因子表达式/规则策略 → FactorLibrary 评价(IC/分层/换手)  │
  │  → 规则回测/组合/蒙特卡罗 → 得到 IC/Sharpe/回撤/成交等可证伪证据      │
  └──────────────────────────────┬────────────────────────────────┘
                                  │ ③ 证据/结论回写
                                  ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  PaperPulse（研·复盘与再选题）                                    │
  │  结果记入选题库/笔记（"已验证/证伪"）→ 综述/期刊适配 → 下一轮选题      │
  └──────────────────────────────────────────────────────────────┘
```

**闭环的 3 个判定信号**（给这个链路定义"真的转起来了"的验收标准）：
1. 一条研究至少经历一次 **ph → qlb 的数据回写**（论文假设在真实 A 股上得到可复现证据，结论落回 ph）。
2. **新手毕业标准** = 能在 QuantLab 独立提交并读懂一次完整回测（因子/区间/指标），而不必先"学完 66 课"。
3. PaperPulse 里论文的「已验证」标签**只允许由 QuantLab 回写产生**（杜绝主观勾选）。

---

## 2. 五条"主动脉"（打通手段，方案级描述）

### A. 统一导航与深链互跳（P0，改动最小、价值最直接）

三个产品各自保留主界面，只在关键位置互放"去另一个产品做下一步"的入口。最小三个深链：

1. **PaperPulse 论文详情页**
   - 「**在 QuantLab 验证**」→ 深链 QuantLab 回测页，预填：区间 = QuantLab 最新数据日期起近 N 年（调 qlb `/quant/data/status` 取 `latest_date`，禁止用"今天"当终点——当日数据未发布）；universe = csi300 默认；主题/策略名 = 论文标题前缀。首版"验证"落点在规则策略（策略库模板：动量/双均线/RSI 等），后续支持直接贴因子表达式。
   - 「**学相关概念**」→ 深链 Quantlerning 课程检索（按论文关键词/方法名定位，见 §D）。
2. **QuantLab 回测/因子结果页**
   - 「**记入研究笔记**」→ 调 PaperPulse `topic_projects`/笔记类 API，把 {因子/策略名, 表达式/规则, 区间, IC/Sharpe/回撤/换手, 结论草稿} 作为一条验证记录落库。
3. **Quantlerning 课程末/练习完成页**
   - 「**去 QuantLab 实战**」→ 按本课主题预填一个可跑通的因子表达式或规则策略模板 + 默认区间，进入 qlb 页面（做到"课上刚学的，三步内能在真实数据上跑起来"）。

原则：深链是**只读导航**，失败要静默降级（目标服务没起时给出提示而非报错）；首版不引 JS SDK，URL + query 即契约。

### B. 统一身份与进度档案

现状三套身份互不相通：ql 无账号（进度在 localStorage）、ph 用 `x-user-id` 头、qlb 用可选 JWT。打通档案的最小方案：

- **身份**：本机个人开发者场景下，先约定一个共享身份文件 `~/.quant/identity.json`（`{user_id, display_name, created_at}`），三端启动/写进度时读取；谁也不做账号体系。
- **档案 API（只读）**：每个产品各自在本地暴露 `GET /api/v1/profile`（ql 返回 {课程完成度, quiz 均分, exercise 均分, 沙箱成功率, 最近学习日}；qlb 返回 {因子数, 最近回测, 有效因子清单}；ph 返回 {论文收藏, 选题库, 验证记录}）。供其它产品在"入口推荐"和"毕业判定"时查询。
- **远期**：若要多机同步/多用户，再整体迁 OIDC + 统一用户表；首版不做。

### C. 共享数据底座与接口契约

- **数据权威 = QuantLab**（qlib bin + quantlab PG）。Quantlerning **维持只读复用同一 PG**（只读是硬约束）；PaperPulse **不直连 PG**，通过 REST 拉取结果、再回写自己的 SQLite。
- 跨产品只走已存在或被薄封装的 **REST/SSE + JSON**。下文 §5 给出契约基线（端口/端点/鉴权）。统一原则：*跨库表不建、跨产品不直接写别人 DB*；回写一律经对方 API 或本机轻量接口，避免三套 schema 紧耦合。

### D. 概念字典与知识检索（"同一个词，三种语境"）

量化/计量里大量术语在三端有不同呈现：IC、Sharpe、换手率、动量、双均线、事件研究、DID……建立一份**共享 concept registry（JSON，带 version）**，每条映射：

```json
{
  "id": "momentum",
  "name_zh": "动量",
  "ph":   { "search": ["动量", "momentum", "cross-sectional momentum"] },
  "ql":   { "lesson": "p3-l3", "concept": "动量因子与收益预测" },
  "qlb":  { "doc": "因子示例 Ref($close,20)/$close-1", "lib": "表达式" }
}
```

三端各做交叉引用入口：ph 论文里命中关键词可跳 ql 课程/qb 因子示例；ql 学完概念可跳 qlb 对应真实因子；qlb 因子详情可反查"这篇论文/这节课讲过它"。**registry 的 version 建议纳入三方 CI 做一致性与链接有效性校验**（类比 Quantlerning 已有的 `scripts/check_content.py`）。

### E. 反馈回路（内容回流，让链路"自我造血"）

1. **qlb → ql**：在 QuantLab 上验证有效的因子/策略，沉淀为 Quantlerning 新课程案例或变式应用题（走 ql 现有 md 内容管线 + `check_content.py` CI，保证格式合法）。
2. **ph → qlb**：论文摘要/方法描述作为因子挖掘的语料——QuantLab 的 LLM 因子挖掘（`llm_factor`）与文本因子（`text_factor`）可把"论文提出的异象"作为生成先验；PaperPulse 的 4.4k 金融经济 + 190 计量论文天然是语料池。
3. **ql → qlb/ph**：学习档案（掌握度/最近在学主题）驱动入口推荐：掌握度达标的用户，在 qlb 与 ph 首页看到"进阶研究任务/相关论文"。

---

## 3. 用户旅程

### 3.1 新手旅程（约 1 天，走完一次全链路）

1. **ph**：首页看到推荐论文《隔夜收益率异象》，点详情，AI 摘要给出"核心变量：隔夜收益 / 次日收益"。
2. **ph → ql**：点「学相关概念」→ 跳到 Quantlerning 动量/收益因子课程段，读 1 课 + 3 道 quiz（80 分以上）。
3. **ql**：应用题的 AI 批改通过 → 用 CodeSandbox 读真实行情 `get_daily("sh600000")` 画出"隔夜收益排名前 10% 股票次日表现"的简易分层图（练直觉，只读数据，可复现论文图）。
4. **ql → qlb**：课程末「去 QuantLab 实战」→ 带入规则策略模板「动量(20 日)」，默认区间为 qlb 最新数据日前 2 年；用户改参数提交回测。
5. **qlb**：得到 Sharpe/回撤/换手 → 点「记入研究笔记」。
6. **ph**：回到该论文页，看到自己的验证记录标签「已初步验证（QuantLab 回测 2026-…）」→ 收藏该选题进 workbench。
7. **闭环信号达成**：ph→qlb 完成一次数据回写。

### 3.2 进阶研究者旅程（选题立项 → 证据 → 发表）

1. **ph**：用 topic-validator 从 3 篇论文中立项一个选题（含数据来源 CSMAR/Wind、方法 DID/事件研究）。
2. **ph → qlb**：把选题的量化部分翻译为因子假设 → 在 QuantLab 用 LLM 挖掘/文本因子跑一版。
3. **qlb**：因子入库 → 深度分析（IC 时序/分层/衰减）→ 规则回测/组合 → 蒙特卡罗看稳定性。
4. **qlb → ph**：结果回写 workbench（指标快照 + 结论）→ 调 `producer` 生成综述初稿与期刊适配 → 导出 GB/T 7714 / BibTeX 引用。
5. **qlb → ql**：把这次跑通的策略（含参数）作为课程案例草稿提交 ql 内容仓库（可选）。

---

## 4. 分阶段路线图

| 阶段 | 内容 | 改动落点与量级 | 可验收信号 | 回滚点 |
|---|---|---|---|---|
| **P0 深链互跳** | §A 三个最小深链按钮 + qlb 只读日期端点复用 | 三端前端各 1 个按钮/链接 + 各自 `<1k` 行前端改动；qlb 若有需要补 1 个只读端点 | 从 ph 论文 3 次点击内到达 qlb 预填回测页；从 ql 课程到达 qlb 模板 | 仅删按钮/链接，无后端迁移 |
| **P1 身份 + 回写** | §B 身份文件 + 档案 API；§A2 回写走 PaperPulse topic_projects | ph 增加"验证记录"落点（复用 topic_projects 或轻量表）；ql 进度从 localStorage 上浮到档案 API | 一条 ph→qlb 回写真实落库并可读回 | 保留旧 localStorage 读路径，双写一段 |
| **P2 智能串联** | §D concept registry + 交叉引用；§E 论文→挖掘语料 | 新建 registry（JSON + CI 校验）；qlb `llm_factor` 增加论文语料注入开关 | qlb 挖掘输入可选择"基于某篇论文摘要"；三方概念跳转闭环可用 | registry 独立文件，可整体下线 |
| **P3 内容联动 + 个性化** | §E 课程案例回流（md + check_content CI）；学习档案驱动推荐 | ql 内容库 + 两端首页推荐位 | 有新案例课入库且 CI 通过；首页推荐随掌握度变化 | 推荐位可关，内容为纯增量 |

每阶段独立可交付、可单独回滚，不要求一次性全上。

---

## 5. 技术集成契约（附录，落地前以各方 `/docs` 为准复核）

### 5.1 端口与鉴权（本机并跑）

| | FE | BE | 鉴权方式 | 跨机时 |
|---|---|---|---|---|
| QuantLab | 3001 | 8101 | `AUTH_ENABLED`（JWT，可关） | 配反向代理/HTTPS |
| Quantlerning | 5173 | 8100 | 无（本地） | 同上 |
| PaperPulse | 3000 | 8000 | `x-user-id` / 可选 `x-api-token` | 同上 |

> 同一机器并跑时注意端口占用（3000/3001/5173/8000/8100/8101 已被三个项目占用），需要时按各自 `.env` 调整并同步深链 URL。

### 5.2 关键端点（现状，供集成基线）

| 能力 | QuantLab（qlb） | Quantlerning（ql） | PaperPulse（ph） |
|---|---|---|---|
| 状态/日期 | `/api/v1/quant/data/status` | `/api/v1/courses*` | `/api/papers*`、`/api/search/suggest` |
| 数据只读 | `/factors*`、回测结果、`/market/*` | `/api/v1/data/*`（daily/index/valuation…）、`/exec/run` 沙箱 | `/api/papers/{id}/analyze[?stream]`、RAG |
| 写/评价 | 因子 CRUD、挖掘、回测 | `/api/v1/chat/judge`、`/exec/run`（评测） | `/api/topic-projects/*`、`/api/topic-ideas/*` |
| AI 技能 | 挖掘/解释 | 判分/变式/复习 | topic-validator、debate、defense、producer（综述/期刊/引用） |
| 导出 | CSV/JSON（因子导出） | 数据集 `sql.gz` | `producer/citations`（gbt7714/bibtex）、JSON export、docx |

### 5.3 统一约定（建议写进各自 CODEBUDDY/docs）
- 深链 URL 只含可公开参数（区间/主题/表达式），不夹 token；鉴权在目标产品自己的会话里完成。
- 时间参数一律 `YYYY-MM-DD`；"最新数据日"统一以 qlb `/quant/data/status` 的 `latest_date` 为准（**不把"今天"当回测终点**）。
- 跨产品回写失败必须**不影响主流程**（包装在 try/catch，记 warning）。
- 概念 registry 每次改动 bump version，并在三方 CI 里做一致性/link 校验。

---

## 6. 风险与未决问题

1. **PaperPulse 基础设施**：SQLite + 个人伪鉴权。若回写与检索量上来，需评估 PG 化与真实身份；P1 前决定"轻量表 vs 复用 topic_projects"。
2. **ql 进度在 localStorage**：换浏览器/清缓存即失，是"毕业判定"的前置障碍——P1 必须先把进度上浮为档案 API。
3. **三仓独立演进、契约漂移**：concept registry 与深链 URL 需 version + CI 校验，否则一段时间后链接失效、指标口径对不上。
4. **口径一致性**：同一指标（IC/Sharpe）在 ql 课程、qlb 计算、ph 论文引用里必须是同一个定义；建议在 registry 里固化每个指标的计算口径与出处链接。
5. **端口占用**：三产品并跑需 6 个端口，超出部分要按 .env 微调并维护"深链目标地址表"。

### 待评审人拍板的决策点
- [ ] **主线方向**：采用"选题驱动（ph 上游）"而非线性课程化（先学完再研）？文档默认前者。
- [ ] **身份最小方案**：首版采用本机 `~/.quant/identity.json`，不引入账号体系，可否？
- [ ] **回写落点**：验证记录先落 PaperPulse 的 `topic_projects`，不新建跨库表，可否？

---

*文档来源：QuantLab `CODEBUDDY.md` 与 `docs/`；Quantlerning `README/CODEBUDDY/backend/content`、`QuizBlock/ExerciseBlock/CodeSandbox/progress`、`chat/judge`、`exec/run`、`scripts/check_content.py`；PaperPulse `PRODUCT_PLAN.md`、`routers/{papers,topic,producer,workbench}.py`、`skills/`。*
