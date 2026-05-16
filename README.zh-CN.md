# Praxile

<div align="center">

<!-- 可选：发布后替换为项目 Logo。 -->
<!-- <img src="assets/praxile-logo.png" alt="Praxile" width="64%" /> -->

<h3>面向 AI 编程的可治理经验 Harness</h3>

<p>
  <b>Spec 治理意图，Praxile 治理经验。</b>
</p>

<p>
  采集 Coding Agent 实际做过什么，将每次运行转化为<b>有证据支撑</b>、<b>可审查</b>的 proposal，<br />
  并只把经过审批的仓库本地知识写入 <code>.praxile/</code>。
</p>

<p>
  <b>简体中文</b>
  ·
  <a href="./README.md"><b>English</b></a>
</p>

<p>
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="MIT License">
  <img src="https://img.shields.io/badge/Status-Alpha-orange?style=for-the-badge" alt="Alpha">
  <img src="https://img.shields.io/badge/Local--First-Yes-6f42c1?style=for-the-badge" alt="Local First">
</p>

</div>

---

## Praxile 是什么？

**Praxile** 是一个面向 AI 编程的可治理经验 Harness。

它围绕 Coding Agent 的工作过程运行：记录环境交互，构建 trajectory，计算 reward 与风险信号，提取 evidence，生成可审查 proposal，并且只在人工审批后写入长期仓库知识。

Praxile 内置了一个**以聊天为先 (Chat-first) 的 Web Console**，不仅为你提供熟悉的 Agent 对话界面，还在侧边栏深度融合了经验治理、合规审计和 Spec 校验能力。

Praxile **不是**另一个通用 Coding Agent，**不是**隐藏式全局记忆系统，也**不是** Spec Kit 的替代品。

它面向希望长期使用 AI Coding Agent 的团队和开发者：让 Agent 工作流随着使用逐步积累可复用经验，同时不失去对“项目到底应该记住什么”的控制权。

> Spec-Driven Development 治理编码前 Agent 应该构建什么；Praxile 治理编码后项目应该学到什么。

---

## 为什么需要 Praxile？

大多数 Coding Agent 已经可以编辑文件、调用工具、运行测试。

更难的问题是：**一次任务结束后，什么应该成为长期项目知识？**

| 问题 | 常见 Coding Agent 工作流 | 使用 Praxile |
|---|---|---|
| 项目经验 | 每次运行后丢失 | 采集为有证据支撑的仓库经验 |
| 长期记忆 | 隐藏式或自动写入 | proposal 驱动，人工审批 |
| 重复失败 | 依赖人工重新发现 | 转化为有范围约束的 failure pattern |
| 项目规则 | 混在 prompt 里 | 沉淀为仓库本地治理资产 |
| Spec 对齐 | 依赖人工感觉 | Spec context 可影响 reward 与 proposal quality |
| 静默失败 | 难以及时发现 | 风险信号标记看似成功但验证薄弱的运行 |
| 经验衰减 | 很少被持续维护 | Reflect 识别重复、过期、有害和高价值经验 |
| 可解释性 | 难以追踪 | `praxile explain latest` 展示检索、reward 和 proposal |
| 治理 | 零散且手工 | 审计、回滚、生命周期状态与来源关系图 |

---

## Architecture at a glance

```mermaid
flowchart LR
    classDef input fill:#EEF4FF,stroke:#5B8DEF,color:#16325C,stroke-width:1.5px;
    classDef interface fill:#F6F8FA,stroke:#7B8794,color:#1F2937,stroke-width:1.5px;
    classDef runtime fill:#F3F7FF,stroke:#4F7FD9,color:#102A56,stroke-width:1.5px;
    classDef engine fill:#F7F0FF,stroke:#8B5CF6,color:#352063,stroke-width:1.5px;
    classDef gov fill:#FFF7E8,stroke:#D8942A,color:#4A3200,stroke-width:1.5px;
    classDef asset fill:#EFFAF0,stroke:#45A66A,color:#12351F,stroke-width:1.5px;
    classDef audit fill:#F2F4F7,stroke:#667085,color:#182230,stroke-width:1.5px;
    classDef reflect fill:#ECFDF3,stroke:#12B76A,color:#054F31,stroke-width:1.5px;

    U["User Task<br/>反馈"]:::input
    S["Spec Context<br/>spec.md · plan.md · tasks.md · constitution.md"]:::input

    U --> I["Interfaces<br/>CLI · Terminal · Gateway"]:::interface
    S --> I

    I --> R["Runtime Harness<br/>Task Analyzer · Model Router · Tools · Tests · Safety · Workspace"]:::runtime
    R --> T["Trajectory Ledger<br/>Actions · Observations · Diffs · Commands · Artifacts"]:::runtime
    T --> E["Experience Engine<br/>Reward · Evidence · Episodes · Patterns"]:::engine
    E --> G["Governance Layer<br/>Silent-Failure Signals · Proposal Gate · Human Review"]:::gov
    G --> A["Repository Assets<br/>Memory · Skill · Rule · Eval · Pattern · Boundary"]:::asset
    A --> Q["Future Retrieval<br/>Explain · Attribution · Consolidation"]:::asset
    Q --> R

    A --> F["Praxile Reflect<br/>离线治理 · 清理 · 提升"]:::reflect
    T --> F
    G --> F
    F -->|可审查 proposal| G

    T --> O["Audit & Provenance<br/>Graph · Redaction · CI Gates · Rollback"]:::audit
    G --> O
    A --> O
    F --> O
```

Praxile 由多层组成：

1. **Spec 与任务输入层**：描述意图、约束和验收标准；
2. **Runtime Harness 层**：通过受控工具、测试、安全策略和可选 workspace isolation 执行任务；
3. **Trajectory Ledger 层**：记录实际发生了什么；
4. **Experience Engine 层**：将运行转化为 reward、evidence、episode 和 pattern；
5. **Governance Layer 层**：通过 silent-failure detection、proposal gate 和人工 review 过滤经验；
6. **Repository Assets 层**：只有通过审批的内容才成为长期资产；
7. **Praxile Reflect 层**：周期性分析已积累经验，并生成可审查的清理与优化 proposal；
8. **Audit & Provenance 层**：让经验链路可解释、可脱敏导出、可回滚。

---

## Core governed experience loop

```mermaid
flowchart LR
    classDef step fill:#F6F8FA,stroke:#7B8794,color:#1F2937,stroke-width:1.3px;
    classDef gate fill:#FFF7E8,stroke:#D8942A,color:#4A3200,stroke-width:1.3px;
    classDef asset fill:#EFFAF0,stroke:#45A66A,color:#12351F,stroke-width:1.3px;
    classDef weak fill:#FFF1F3,stroke:#E31B54,color:#7A271A,stroke-width:1.3px;
    classDef reflect fill:#ECFDF3,stroke:#12B76A,color:#054F31,stroke-width:1.3px;

    A["Run"]:::step --> B["Trajectory"]:::step --> C["Reward Report"]:::step --> D["Evidence"]:::step --> E["Episode"]:::step --> F["Pattern"]:::step --> G["Proposal"]:::step
    G --> H["Proposal Gate"]:::gate
    H -->|通过| I["Human Review"]:::gate
    H -->|抑制| W["Weak Candidate<br/>仅进入 run summary"]:::weak
    I -->|接受| J["Active Asset"]:::asset
    I -->|编辑 / 拒绝| K["Review Signal"]:::gate
    J --> L["Future Retrieval"]:::asset
    L --> A

    J --> R["Reflect<br/>重复 · 过期 · 有害 · 高价值"]:::reflect
    K --> R
    R -->|治理 proposal| H
```

核心规则很简单：

> 一次运行可以产生学习信号，但只有经过审批的 proposal 才能成为长期仓库知识。

---

## 功能亮点

- **以聊天为先的工作台 (Chat-First Web Console)**  
  零外部依赖的本地工作台（`praxile gateway serve`），提供经典的三栏式布局：聊天区、执行明细与右侧治理上下文面板。
- **仓库本地经验**  
  Memories、skills、rules、evals、failure patterns、project patterns、frozen boundaries、architecture gates 等都保存在 `.praxile/` 下。
- **Spec-aware execution**  
  可选读取 spec、plan、task、constitution 上下文，并影响 reward、silent-failure signals 与 proposal gate。
- **Evidence-backed proposals**  
  长期变更以 proposal 形式生成，包含 source run、evidence summary、confidence、applicability scope、anti-scope 和 rollback path。
- **Proposal gate 与人工 review**  
  低证据、低置信、缺少范围约束或存在风险的学习候选可以在进入 review 前被抑制。
- **Silent-failure detection**  
  识别看似成功但验证薄弱、范围过大、缺少 spec 或缺少归因的运行风险。
- **Praxile Reflect**  
  离线、proposal-governed 的经验治理能力：识别重复经验、过期资产、有害资产、重复静默失败、被拒绝 proposal 模式和高价值 pattern。
- **Reward and attribution**  
  区分任务成功、回归安全、过程安全、成本、经验价值、用户反馈和 asset attribution。
- **Experience graph and audit chain**  
  从 spec、run、proposal、asset、feedback、reflect report 和 future retrieval 中构建可重建的本地来源关系图。
- **Safety and rollback**  
  内置敏感路径保护、危险命令阻断、项目本地 `.praxile/rules/safety-policy.json`、备份、architecture gate、workspace isolation 和 proposal rollback。

---

## Praxile Reflect

Praxile Reflect 是对已积累仓库经验进行离线治理的能力。

它参考了“定期回顾长期 Agent 记忆”的思路，但不会自动改写 memory。

```text
Past Runs + Assets + Feedback + Silent-Failure Signals + Provenance Graph
  -> Reflect
  -> Findings
  -> Reviewable Governance Proposals
  -> Human Review
  -> Asset Merge / Deprecate / Rewrite / Promote
```

Reflect 可以发现：

- 重复或重叠的经验资产；
- 过期或长期未使用的 memory、skill、rule 和 pattern；
- 带有负向 outcome 或 harmful feedback 的资产；
- 重复出现的 silent-failure signals；
- 反复出现的 rejected-proposal themes；
- 值得提升为 skill、rule 或 checklist 的高价值 pattern。

常用命令：

```bash
praxile reflect --summary
praxile reflect --since 7d
praxile reflect --duplicates --stale --silent-failures
praxile reflect --harmful --rejected-proposals --high-value-patterns
praxile reflect --asset .praxile/memory/project.md
praxile reflect --report markdown --output reflect.md
praxile reflect --write-proposals
praxile reflect --ci
```

边界：

> Reflect 不会直接改写记忆，而是提出可治理的经验更新 proposal。

---

## Experience graph

Praxile 不只是一组 Markdown 文件。它会构建本地 provenance graph，用于解释经验从哪里来，以及后来如何被使用。

```mermaid
flowchart TB
    classDef spec fill:#EEF4FF,stroke:#5B8DEF,color:#16325C,stroke-width:1.3px;
    classDef run fill:#F6F8FA,stroke:#7B8794,color:#1F2937,stroke-width:1.3px;
    classDef proposal fill:#FFF7E8,stroke:#D8942A,color:#4A3200,stroke-width:1.3px;
    classDef asset fill:#EFFAF0,stroke:#45A66A,color:#12351F,stroke-width:1.3px;
    classDef feedback fill:#F7F0FF,stroke:#8B5CF6,color:#352063,stroke-width:1.3px;
    classDef reflect fill:#ECFDF3,stroke:#12B76A,color:#054F31,stroke-width:1.3px;

    S["Spec / Constitution"]:::spec -->|derived_from_spec| R1["Run"]:::run
    R1 -->|produced| E["Evidence / Episode"]:::run
    E -->|supports_proposal| P["Proposal"]:::proposal
    R1 -->|generated_from_run| P
    P -->|approved_by| A["Asset"]:::asset
    A -->|retrieved_in_run| R2["Future Run"]:::run
    A -->|helped_run| R2
    A -->|misled_run| R3["Failed / Risky Run"]:::run
    F["Feedback"]:::feedback -->|adjusts_confidence| A
    A -->|supersedes| A2["Older Asset"]:::asset
    P -->|rejected_as| X["Rejected Signal"]:::proposal
    RF["Reflect Finding"]:::reflect -->|recommends| P2["Governance Proposal"]:::proposal
    RF -->|analyzes| A
    RF -->|analyzes| R1
```

它帮助回答：

```text
这条 asset 来自哪里？
哪个 run 生成了这个 proposal？
哪些 evidence 支撑了它？
它是被接受、拒绝、废弃还是替换？
它后来在哪些 run 中被检索？
它是帮助了任务，还是误导了任务？
Reflect 是否建议清理、合并或提升它？
```

---

## 安装

Praxile 需要 **Python 3.11+**。

### 从 GitHub 安装

```bash
pipx install "git+https://github.com/Praxile-Alpha/Praxile.git"
```

或使用 `uv`：

```bash
uv tool install "git+https://github.com/Praxile-Alpha/Praxile.git"
```

### 开发环境安装

```bash
git clone https://github.com/Praxile-Alpha/Praxile.git
cd Praxile
python -m pip install -e ".[http]"
```

可选扩展：

```bash
python -m pip install -e ".[vector]"   # 语义检索
python -m pip install -e ".[browser]"  # 浏览器证据采集
python -m playwright install chromium
```

---

## 不配置模型也可以试用

运行本地 demo：

```bash
praxile demo --fast --accept-first --show-files
```

该 demo 不需要模型端点。它会创建一个小型项目，记录 trajectory，生成 reward report 和 proposals，在 demo 项目内接受一个低风险 memory，并展示后续运行如何检索它。

---

## 快速开始

### 1. 初始化代码仓库

```bash
cd /path/to/your/code-project
praxile init
praxile setup
praxile doctor
praxile doctor --online
```

`praxile setup` 用于配置 provider 和 model roles。Praxile 只保存环境变量名称，例如 `OPENAI_API_KEY` 或 `OLLAMA_API_KEY`，不会保存原始 API key。

### 2. 执行任务

```bash
praxile run "Fix the failing parser test" --test-command "python -m pytest"
```

### 3. 附加 spec context 执行

```bash
praxile run "Implement search API"   --spec docs/specs/search.md   --test-command "python -m pytest"
```

### 4. 使用 Web Console (强烈推荐)

启动内置的可视化、Chat-first 控制台，更直观地与 Agent 对话并进行经验审核：

```bash
praxile gateway serve --host 127.0.0.1 --port 8765
```
然后在浏览器中打开 `http://127.0.0.1:8765/`。

如果要使用独立 React/Vite Console：

```bash
cd web
npm install
npm run dev
```

Web Console 现在可以生成本地 CI report、在显式确认后通过 `GITHUB_TOKEN` 发布 GitHub PR comment、导入 GitHub Actions artifacts 到 `.praxile/`、可视化 experience graph，并通过结构化字段编辑 pending proposal。

### 5. 审查与解释

```bash
praxile review --interactive
praxile explain latest
praxile spec verify latest
```

### 5. 优化已积累经验

```bash
praxile reflect --summary
praxile reflect --since 7d --duplicates --stale --silent-failures
```

### 6. 接受或拒绝 proposal

```bash
praxile accept <PROPOSAL_ID>
praxile reject <PROPOSAL_ID> --reason "too broad"
```

---

## 经验模型

| 层级 | 作用 |
|---|---|
| Markdown / JSON | 人类可读的长期资产和结构化运行记录 |
| SQLite | 资产元数据、生命周期状态、使用记录和来源关系 |
| FTS | 关键词检索 |
| Vector index | 可选语义检索 |
| Experience graph | 可重建的来源关系与影响关系 |
| Proposal history | 审查、接受、拒绝、回滚记录 |
| Reflect reports | 离线经验清理与优化 findings |
| Audit chain | 带脱敏模式的可导出治理证据 |

已批准资产默认处于 active 状态。Deprecated、superseded、archived 资产仍然可审计，但默认不参与正常检索。

---

## 常用命令

```text
praxile init                    初始化当前仓库的 .praxile
praxile setup                   配置 providers 和 model roles
praxile demo --fast             运行本地 governed-experience demo
praxile run "..."               执行 agent 任务
praxile run "..." --dry-run     仅分析并记录，不编辑文件
praxile run "..." --spec ...    附加 spec context 执行任务
praxile review --interactive    审查 pending proposals
praxile explain latest          解释检索、reward 和 proposals
praxile spec check              检查可选 spec 质量信号
praxile spec verify latest      基于 spec context 验证运行结果
praxile reflect --summary       分析已积累经验
praxile reflect --write-proposals
                                  生成可审查治理 proposal
praxile graph explain <ASSET>   解释 asset 来源与使用关系
praxile audit check             运行治理门禁
praxile consolidate --all       对重复或过期资产生成治理 proposal
praxile rollback <ID>           回滚任务编辑或已接受 proposal
praxile doctor --online         校验配置、路由和本地状态
```

完整 CLI 参考请见 [Getting Started](docs/GETTING_STARTED.md)。

---

## 本地状态

Praxile 会在 `.praxile/` 下写入仓库本地状态：

```text
.praxile/
  config.json
  constitution.md
  memory/
  skills/
  evals/
  rules/
  experience/
    trajectories/
    evidence/
    episodes/
    patterns/
    proposals/
    feedback/
    reflect/
  backups/
  db/
  logs/
```

不要把原始密钥写入 `.praxile/config.json`。请通过 `api_key_env` 和 channel 的 `token_env` 设置引用环境变量。

---

## Interop boundary

Praxile 可以检测可选的外部 Agent 能力，也可以使用 OpenAI-compatible endpoints，但它不是 Hermes、OpenClaw 或 Memori 插件。

- `.praxile/memory` 不会写入外部全局记忆；
- `.praxile/skills` 不会安装到外部 skill store；
- Praxile trajectories 是事实来源；
- external-compatible sidecars 只是导出物；
- 未来的外部同步必须通过显式 adapter 命令和可审计 proposal 完成。

---

## 当前状态

Praxile 当前处于 **Alpha** 阶段。

已实现核心能力：

- init / setup / doctor；
- 本地 demo；
- run / trajectory logging；
- reward report；
- evidence 和 proposal generation；
- proposal gate；
- `propose`、`search`、index maintenance 与应用前 snapshot；
- generic JSONL 外部轨迹导入与 JSON eval suite runner；
- review / accept / reject；
- chat-first 本地 Web Console，包含 Reflect、Graph、Audit、Spec 与 Safety 面板；
- repository-local assets；
- retrieval 和 explain；
- spec-aware context；
- silent-failure signals；
- experience graph 和 audit exports；
- Praxile Reflect；
- rollback。

演进中能力：

- isolated workspaces；
- terminal 与 local gateway；
- channel configuration；
- semantic judges；
- CI governance gates；
- advanced consolidation and reflect policies。

首个版本不包含：

- 自动模型权重训练；
- marketplace 分发；
- 静默全局记忆同步；
- 自动生产级 Telegram / Discord listeners；
- 不受限制的 shell 执行；
- 长期经验的自动接受。

---

## 文档

- [Getting Started](docs/GETTING_STARTED.md)
- [Configuration](docs/CONFIGURATION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Core Layers](docs/CORE_LAYERS.md)
- [Experience Model](docs/EXPERIENCE_MODEL.md)
- [Evals And Adapters](docs/EVALS_AND_ADAPTERS.md)
- [Web Console](docs/WEB_CONSOLE.md)
- [Praxile Reflect](docs/REFLECT.md)
- [Why Praxile](docs/WHY_PRAXILE.md)
- [Governance](docs/GOVERNANCE.md)
- [Install And Interop](docs/INSTALL_AND_INTEROP.md)
- [Testing Guide](docs/contributing-testing.md)
- [Security Policy](docs/SECURITY_MODEL.md)

---

## 参与贡献

欢迎贡献。

适合作为起点的方向包括：

- proposal quality 与 deduplication；
- spec-aware experience；
- silent-failure detection；
- Praxile Reflect analyzers；
- retrieval quality；
- semantic judge evaluation；
- explainability；
- audit 与 governance UX。

提交前请阅读 `CONTRIBUTING.md` 与 `SECURITY.md`。

---

## License

MIT License. See [LICENSE](LICENSE).
