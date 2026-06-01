# Praxile 下一阶段研发路线：工程健康化、Web Console 产品化与仓库上下文主动采集

> 面向研发人员。  
> 本文档整合三个方向：  
> 1. 工程结构健康化：`cli.py` / `store.py` 重构；  
> 2. Web Console 产品化：Chat-first、Repository Context 面板、Context Health 指标；  
> 3. OpenHuman 启发后的新增能力：`praxile sync`、ContextJuice、Memory Tree、Policy Layers、Background Governance Loop、Model Roles + Context Compression 联动。

---

## 0. 总体判断

当前 Praxile 已经具备较完整的 AI Coding Experience Governance 闭环：

```text
Chat / Run
  -> Runtime Harness
  -> Trajectory
  -> Reward
  -> Evidence
  -> Proposal Gate
  -> Human Review
  -> Asset
  -> Reflect
  -> Graph / Audit
```

下一阶段不能继续单纯堆功能，需要同时解决三个问题：

1. **工程可维护性问题**  
   当前 `cli.py` / `store.py` 已经承担过多职责，后续继续加 Web Console、Policy、Sync、ContextJuice 会快速失控。

2. **产品入口问题**  
   Praxile 不能只有 CLI，Web Console 必须成为 Chat-first 的 Agent Workspace，同时将 Praxile 的治理能力作为右侧增强层可视化。

3. **仓库上下文主动采集问题**  
   OpenHuman 的启发是“系统不应等用户手动喂上下文，而要主动同步、压缩、组织上下文”。Praxile 不应采集个人全域数据，而应主动了解代码仓库。

最终目标：

```text
从 CLI-first 的本地经验治理工具
升级为 Chat-first、Repo-aware、Policy-driven 的 AI Coding Governance Harness。
```

---

## 1. 优先级总览

### P0：必须先做，保证后续可持续开发

```text
P0-1 工程结构健康化：拆分 cli.py / store.py
P0-2 Web Console MVP：Chat-first + Model Roles + Run / Proposal 基础页面
P0-3 Repository Context 面板 MVP：展示当前仓库理解状态
```

### P1：产品差异化增强

```text
P1-1 Context Health 指标体系
P1-2 praxile sync MVP
P1-3 ContextJuice MVP
P1-4 Reflect / Graph / Audit 可视化
```

### P2：上下文主动采集与治理体系完善

```text
P2-1 Repository Memory Tree
P2-2 Policy Layers
P2-3 Model Roles + Context Compression 联动
P2-4 Background Governance Loop
```

### P3：团队化与平台化

```text
P3-1 CI / PR 集成
P3-2 多仓库治理
P3-3 Workflow Templates
P3-4 组织级 Policy 与 Experience Governance
```

---

# 2. P0：工程结构健康化

## 2.1 背景

当前核心文件过大：

```text
cli.py     4000+ 行
store.py   2900+ 行
```

这两个文件承担了过多职责：

```text
命令注册
命令执行
状态读写
proposal 管理
asset 管理
graph
audit
reflect
index
rollback
```

如果不先做结构健康化，后续新增 Web Console、API、Sync、ContextJuice、Policy Layers 会导致维护成本快速上升。

---

## 2.2 P0-1：拆分 cli.py

### 目标

将 `cli.py` 从“大文件命令集合”拆成模块化 CLI 命令系统。

### 建议目录结构

```text
praxile/cli/
  __init__.py
  main.py
  parser.py
  context.py
  commands/
    init.py
    setup.py
    run.py
    review.py
    proposal.py
    history.py
    explain.py
    feedback.py
    index.py
    consolidate.py
    models.py
    tools.py
    terminal.py
    channel.py
    rollback.py
    memory.py
    skill.py
    asset.py
    gateway.py
    doctor.py
    interop.py
    graph.py
    audit.py
    spec.py
    workspace.py
    reflect.py
    sync.py
    context.py
    policy.py
```

### 拆分原则

每个 command 文件负责：

```text
1. 注册 argparse 子命令
2. 参数校验
3. 调用 service / engine
4. 格式化输出
```

不要在 command 层实现复杂业务逻辑。

### 建议新增 CommandContext

```python
class CommandContext:
    project_root: Path
    config: Config
    store: ExperienceStore
```

让各命令共享上下文加载逻辑，避免重复初始化。

### 验收标准

```text
cli.py 不再超过 300 行
所有原有命令行为不变
现有测试全部通过
新增命令可以单独维护
CLI command 注册逻辑清晰
```

---

## 2.3 P0-2：拆分 store.py

### 目标

将 `store.py` 从单体存储实现拆成多个 repository / service 模块。

### 建议目录结构

```text
praxile/store/
  __init__.py
  base.py
  schema.py
  migrations.py
  tasks.py
  trajectories.py
  proposals.py
  assets.py
  retrieval.py
  graph.py
  audit.py
  reflect.py
  index.py
  feedback.py
  rollback.py
```

### 模块职责

#### base.py

```text
SQLite connection
transaction
file lock
common helpers
```

#### schema.py / migrations.py

```text
表结构定义
schema 版本
迁移逻辑
```

#### tasks.py

```text
tasks table
run history
task status
reward score
```

#### trajectories.py

```text
trajectory read/write
external compatible sidecar
checkpoint
```

#### proposals.py

```text
pending / accepted / rejected proposals
write proposal
accept proposal
reject proposal
proposal rollback metadata
```

#### assets.py

```text
asset metadata
asset lifecycle
active / deprecated / superseded / archived
asset usage
```

#### retrieval.py

```text
FTS
vector retrieval
ranking
matched terms
loaded assets
```

#### graph.py

```text
experience_nodes
experience_edges
graph rebuild
graph explain
graph trace
graph impact
```

#### audit.py

```text
audit chain
audit bundle
redaction helpers
```

#### reflect.py

```text
reflect reports
reflect findings
reflect generated proposal metadata
```

### 验收标准

```text
store.py 不再超过 500 行
原 ExperienceStore 对外 API 尽量兼容
内部委托到各 repository/service
现有测试全部通过
新增 store 模块有单元测试
```

---

## 2.4 P0-3：抽象 Service Layer

拆分 CLI 和 Store 后，应抽象 service 层，供 CLI / Web API 共用。

建议目录：

```text
praxile/services/
  run_service.py
  proposal_service.py
  asset_service.py
  graph_service.py
  audit_service.py
  reflect_service.py
  spec_service.py
  model_service.py
  sync_service.py
  context_service.py
```

### 价值

```text
CLI 调 service
Web API 调 service
未来 CI / MCP 也调 service
避免重复业务逻辑
```

### 验收标准

```text
CLI 不直接操作底层 store 细节
Web API 后续可复用同一 service
run / proposal / asset / reflect / graph 具备明确 service 边界
```

---

# 3. P0：Web Console 产品化 MVP

## 3.1 产品定位

Web Console 不应是普通治理后台，而应是：

```text
Chat-first Agent Workspace + Governance Console
```

核心逻辑：

```text
Chat 是入口
Run 是过程
Governance 是右侧增强层
Experience 是长期资产层
```

用户应能像使用 OpenClaw / Hermes / Claude Code 一样，通过聊天框发起任务；Praxile 在右侧展示独有的治理信息。

---

## 3.2 P0 页面范围

P0 必做页面：

```text
1. Chat Workspace
2. Model Roles Configuration
3. Run Detail
4. Proposal Inbox / Detail
5. Asset Detail Lite
6. Repository Context Panel MVP
```

---

## 3.3 Chat Workspace

### 页面布局

```text
左栏：Project / Sessions / Runs
中栏：Chat + Agent Execution
右栏：Governance Context
```

### 左栏

```text
当前项目路径
会话列表
最近 runs
pending proposals
recent reflect findings
context health 简要状态
```

### 中栏

```text
聊天消息
任务输入框
Agent 执行过程
工具调用卡片
命令输出
文件 diff
测试结果
Stop / Continue / Retry
```

### 右栏

```text
Run Summary
Spec Context
Loaded Assets
Reward
Evidence
Silent Failure
Generated Proposals
Proposal Gate
Audit Status
Experience Graph Links
```

### 输入框参数

```text
task
test_command
spec_path
workspace_mode
dry_run
model_role_override
allow_shell
run_tests
max_steps
```

### 验收标准

```text
默认打开 Web Console 进入 Chat Workspace
可以新建 session
可以输入任务并启动 run
可以看到 run 执行阶段
可以看到 tool calls / diff / test output
run 完成后右侧展示 governance summary
```

---

## 3.4 Model Roles Configuration

### 背景

Praxile 支持多个模型角色，因此 Web Console 必须把 Model Roles 作为一级配置能力，而不是简单模型选择框。

### 推荐展示的 14 个角色

```text
1. coding_agent
2. evidence_extraction
3. experience_reflection
4. proposal_composer
5. review_recommendation
6. reward_judge
7. feedback_classifier
8. attribution_judge
9. counterexample_checker
10. pattern_mining
11. project_pattern_composer
12. deep_project_pattern_mining
13. cheap_reasoner
14. embedding
```

### 分组

```text
Core execution roles
Experience extraction roles
Review and reward roles
Semantic judge roles
Utility roles
```

### 表格字段

```text
Role
Category
Purpose
Provider
Model
Mode
Fallback
Status
Latency
Actions
```

### Mode

```text
required
recommended
optional
disabled
```

### Status

```text
connected
missing_key
unreachable
disabled
not_configured
```

### Provider 管理

支持：

```text
Ollama
OpenAI-compatible
Anthropic
Local hash embedding
Custom provider
```

### Presets

```text
Minimal setup
Local-first
Cloud coding + local judges
All OpenAI-compatible
```

### 验收标准

```text
可以查看所有 model roles
可以配置 role -> provider/model
可以测试 selected role
可以 test all routes
不显示 raw API key
能显示 provider health
能显示模型调用统计
```

---

## 3.5 Proposal Inbox / Detail

### 列表字段

```text
Proposal ID
Type
Title
Risk
Confidence
Gate status
Recommended action
Source run
Generated by: run / reflect
Created at
```

### Detail 必须回答三个问题

```text
Why generated?
Why should / should not accept?
What will change if accepted?
```

### Detail 展示

```text
Reason
Evidence summary
Risk
Confidence
Applicability scope
Anti-scope
Proposal gate result
Silent failure influence
Source run
Affected assets
Target files
Diff
Rollback path
Review history
```

### 操作

```text
Accept
Reject
Edit
View source run
View affected asset
View graph
```

### 验收标准

```text
可以查看 pending proposals
可以按 risk / type / generated_by 过滤
可以 accept / reject
reject 必须提供原因
高风险操作二次确认
```

---

## 3.6 Repository Context Panel MVP

### 背景

参考 OpenHuman 的核心启发：系统要主动理解用户/环境，而不是完全依赖用户手工喂上下文。

Praxile 不应采集个人全域数据，而应主动了解代码仓库：

```text
OpenHuman 主动了解人
Praxile 主动了解仓库
```

### 面板展示内容

```text
Last sync time
Detected stacks
Detected test commands
Spec files
Docs files
Recent commits
Recent failures
Active assets
Pending proposals
Reflect findings
Context freshness
```

### P0 数据来源

先不做复杂 sync，先复用当前已有数据：

```text
project inspection
git status
detected test commands
existing specs
existing docs
.praxile assets
recent trajectories
reflect reports
```

### 验收标准

```text
Chat Workspace 右侧或左侧能展示 Repository Context
能看到项目被 Praxile 理解到什么程度
能看到 last sync / detected tests / spec coverage / active assets
```

---

# 4. P1：Web Console 差异化增强

## 4.1 Context Health 指标体系

### 背景

OpenHuman 的产品价值之一是让用户知道“它已经了解你”。Praxile 应该让用户知道：

```text
Praxile 已经了解这个仓库到什么程度。
```

### 指标建议

```text
Context freshness
Spec coverage
Asset freshness
Reflect health
Compression ratio
Unreviewed proposals
Silent failure trend
Model route health
```

### 指标说明

#### Context freshness

```text
最近一次 repo context sync 距现在多久
最近 commits / docs / specs 是否已索引
```

#### Spec coverage

```text
多少模块有 spec
多少最近任务附带 spec
多少 architecture-sensitive run 缺少 spec
```

#### Asset freshness

```text
active assets 中多久未使用
stale / deprecated / superseded 比例
```

#### Reflect health

```text
最近一次 reflect 时间
reflect findings 数量
unreviewed reflect proposals 数量
```

#### Compression ratio

```text
ContextJuice 压缩前后 token / chars 比例
```

#### Silent failure trend

```text
最近 7 天 silent failure 数量
重复 signal 类型
```

### 验收标准

```text
Dashboard / Repository Context Panel 展示 Context Health
指标有颜色状态：healthy / warning / critical
点击指标可跳转到详情
```

---

## 4.2 Reflect Dashboard

### 页面内容

```text
Reflect Summary
Findings
Recommended Proposals
Asset Health
Silent Failure Patterns
Rejected Proposal Themes
High-value Patterns
```

### 操作

```text
Run Reflect
Run Reflect since 7d
Write Proposals
Open Proposal
Open Asset
Open Graph
```

### 验收标准

```text
可以运行 reflect summary
可以查看 reflect findings
可以从 finding 生成 proposal
可以查看 generated_by=reflect 的 proposals
```

---

## 4.3 Graph Explorer

### 节点

```text
Spec
Run
Evidence
Episode
Pattern
Proposal
Asset
Feedback
Reflect Finding
Audit Report
```

### 边

```text
derived_from_spec
generated_from_run
supports_proposal
approved_by
retrieved_in_run
helped_run
misled_run
supersedes
contradicts_asset
adjusts_confidence
reflect_recommends
```

### MVP 策略

不要展示全图，默认展示 ego graph：

```text
中心节点 + 一跳 / 两跳关系
```

### 验收标准

```text
可以 graph explain asset
可以 graph trace proposal
可以 graph impact spec
节点点击可跳转对应详情页
```

---

## 4.4 Audit Dashboard

### 页面内容

```text
Audit status
Failed checks
Warnings
Redaction mode
Latest run audit
Proposal audit
Asset audit
Bundle export
CI gate result
```

### 验收标准

```text
可以运行 audit check
可以导出 audit bundle
支持 standard / strict / none redaction
none 模式必须警告
```

---

# 5. P1：OpenHuman 启发后的新增模块

## 5.1 praxile sync

### 目标

让 Praxile 在 run 前主动采集仓库上下文，而不是完全依赖用户手工提供上下文。

### 命令设计

```bash
praxile sync
praxile sync --watch
praxile sync --since 7d
praxile sync --github
praxile sync --docs
praxile sync --specs
praxile sync --ci
```

### 采集内容

```text
git commits
git status
recent diffs
GitHub PRs
GitHub issues
CI logs
docs
specs
test commands
package metadata
accepted assets
reflect findings
```

### 产物

```text
.praxile/context/
  repo_snapshot.json
  docs_index.json
  specs_index.json
  commits/
  issues/
  ci/
  summaries/
```

### P1 验收标准

```text
praxile sync 可以扫描本地 repo
能生成 repo_snapshot.json
能识别 docs / specs / test commands
能在 Web Console Repository Context Panel 展示 sync 结果
```

---

## 5.2 ContextJuice

### 目标

借鉴 OpenHuman 的 TokenJuice，将 Praxile 的长上下文压缩为更适合不同 model roles 使用的结构化上下文，同时保留治理证据。

### 压缩对象

```text
tool outputs
shell outputs
test logs
CI logs
diffs
docs
HTML / Markdown
GitHub issue / PR comments
trajectory history
reflect reports
audit reports
```

### 压缩策略

```text
HTML -> Markdown
长 URL 缩短
重复 stack trace 去重
测试日志只保留 failing cases
diff 只保留 changed hunks + context
长文档 heading-aware chunk
按模块 / spec / file path 聚合
保留 error signature
保留 command / exit code / file path
```

### 关键原则

不能压掉治理关键证据：

```text
failure signature
file path
command
diff hunk
spec constraint
proposal evidence
source run id
```

### 命令设计

```bash
praxile context compress
praxile context compress --run latest
praxile context compress --source ci-log.txt
praxile context status
```

### 输出

```text
compressed markdown chunks
preserved evidence metadata
token estimate before / after
compression ratio
```

### 验收标准

```text
可以压缩 test log / diff / tool output
输出 compression ratio
输出 preserved evidence metadata
Web Console 展示 ContextJuice 压缩率
```

---

## 5.3 Repository Memory Tree

### 目标

将仓库上下文和已批准经验组织成人类可读的树状视图。

注意：Memory Tree 不替代 Experience Graph，而是 Graph 的人类可读视图。

### 建议结构

```text
.praxile/context/tree/
  modules/
    parser.md
    auth.md
    api.md
  timelines/
    2026-05.md
  specs/
    search-api.md
  failures/
    parser-fixture.md
  decisions/
    architecture-boundaries.md
```

### 组织维度

```text
按模块：auth / parser / api / frontend
按时间：最近 7 天 / 最近 30 天
按对象：spec / PR / run / asset
按风险：silent failure / high-risk proposal
按经验：failure pattern / skill / rule / memory
```

### 命令设计

```bash
praxile context tree
praxile context tree --module auth
praxile context tree --recent 7d
```

### 验收标准

```text
能根据已有 assets / specs / runs 生成 tree markdown
Web Console 能展示 Repository Memory Tree
点击 tree 节点可跳转 asset / run / spec / graph
```

---

## 5.4 Policy Layers

### 目标

借鉴 OpenHuman 的三层规则叠加，形成 Praxile 自己的 policy-as-code 体系。

### 建议规则层

```text
default policy
user policy
project policy
organization policy（后续）
```

### 配置目录

```text
.praxile/policies/
  default.json
  user.json
  project.json
  reflect.json
  context.json
  proposal_gate.json
  tool_policy.json
  model_roles.json
```

### 应用范围

```text
tool execution
shell command
workspace mode
spec gate
proposal gate
reflect proposal
audit check
model role routing
context compression
```

### 命令设计

```bash
praxile policy list
praxile policy check
praxile policy explain proposal_gate
```

### 验收标准

```text
能加载 default / user / project policy
能解释某条 policy 为什么生效
proposal gate / tool policy 至少支持外置 JSON 配置
Web Console 能展示 policy 状态
```

---

## 5.5 Background Governance Loop

### 目标

借鉴 OpenHuman 的“潜意识循环”，但严格保持 Praxile 的治理边界。

Praxile 后台循环只做：

```text
sync
compress
reflect
audit
graph rebuild
report
proposal generation
```

不做：

```text
自动改代码
自动 accept proposal
自动 rewrite memory
自动执行危险 shell
```

### 命令设计

```bash
praxile daemon start
praxile daemon status
praxile daemon stop
```

或：

```bash
praxile watch
```

### 周期任务

```text
sync repo context
compress new logs
rebuild graph
run reflect summary
run audit check
generate governance report
```

### 验收标准

```text
可以启动后台 watch
可以定期 sync / reflect / audit
不会自动 accept / rewrite / edit code
生成 reports / proposals 需人工 review
```

---

## 5.6 Model Roles + Context Compression 联动

### 目标

不同 model roles 需要不同上下文。ContextJuice 不应只有一种压缩策略，而应按 role 生成不同上下文视图。

### 示例策略

```text
coding_agent:
  保留代码片段、diff、错误签名、文件路径、测试命令

evidence_extraction:
  保留 tool output、test result、diff summary、failure excerpts

proposal_composer:
  保留 evidence summary、scope、anti-scope、source run、target files

reward_judge:
  保留 reward signals、tests、blocked actions、scope control、silent risks

attribution_judge:
  保留 loaded assets、action history、referenced paths、outcome

pattern_mining:
  保留 failure signatures、affected files、verification commands、fix actions
```

### 配置路径

```text
.praxile/policies/context.json
```

示例：

```json
{
  "compression_by_role": {
    "coding_agent": {
      "preserve": ["diff_hunks", "file_paths", "error_signatures", "test_commands"],
      "max_chars": 60000
    },
    "proposal_composer": {
      "preserve": ["evidence_summary", "scope", "anti_scope", "source_run"],
      "max_chars": 30000
    }
  }
}
```

### 验收标准

```text
ContextJuice 可按 role 输出不同上下文
run 中记录使用了哪种 compression profile
Web Console Model Roles 页面展示 compression profile
Context Health 展示各 role 压缩率
```

---

# 6. P2：团队化与平台化

## 6.1 CI / PR 集成

目标：

```text
让 Praxile 从本地工具进入团队工程流程。
```

能力：

```text
GitHub Actions
PR comment report
CI summary
audit check
spec verify
reflect --ci
pending high-risk proposal gate
```

典型流程：

```text
PR opened
  -> praxile spec verify
  -> praxile audit check
  -> praxile reflect --ci
  -> generate PR comment
  -> block if high-risk governance issue exists
```

---

## 6.2 Workflow Templates

将不同任务类型模板化：

```text
test-failure-repair
spec-driven-feature
architecture-change
security-fix
migration
```

每类 workflow 定义：

```text
需要哪些 spec
允许哪些工具
必须跑哪些 tests
proposal gate 标准
silent failure 规则
audit 输出要求
review 策略
```

---

## 6.3 Multi-repo View

后续支持多仓库治理：

```text
repo health
context freshness
pending high-risk proposals
reflect findings
audit status
policy compliance
```

---

# 7. 推荐实施顺序

## 第 1 阶段：工程健康化

```text
拆 cli.py
拆 store.py
抽 service layer
确保测试全绿
```

原因：后续 Web API、sync、policy 都依赖清晰 service 边界。

---

## 第 2 阶段：Web Console P0

```text
Chat Workspace
Model Roles
Runs
Proposals
Asset Lite
Repository Context Panel MVP
```

原因：先补齐用户入口和基础交互。

---

## 第 3 阶段：Context Sync + Context Health

```text
praxile sync MVP
Repository Context Panel 增强
Context Health 指标
```

原因：让 Praxile 从“执行后学习”扩展为“运行前主动了解仓库”。

---

## 第 4 阶段：ContextJuice + Memory Tree

```text
ContextJuice
role-specific compression
Repository Memory Tree
```

原因：解决上下文臃肿和手工维护知识库问题。

---

## 第 5 阶段：Policy Layers + Background Loop

```text
Policy Layers
Background Governance Loop
Reflect / Audit / Sync 周期化
```

原因：让 Praxile 的治理能力从手动命令升级为持续治理系统。

---

## 第 6 阶段：CI / PR / Team-ready

```text
GitHub Actions
PR comments
CI summary
workflow templates
multi-repo dashboard
```

原因：进入团队工程流程。

---

# 8. 最终研发任务摘要

本阶段总目标：

```text
让 Praxile 从 CLI-first 的本地经验治理工具，
升级为 Chat-first、Repo-aware、Policy-driven 的 AI Coding Governance Harness。
```

核心交付顺序：

```text
P0:
- cli.py / store.py 重构
- Chat Workspace
- Model Roles Configuration
- Run Detail
- Proposal Review
- Repository Context Panel MVP

P1:
- Context Health
- praxile sync
- ContextJuice
- Reflect Dashboard
- Graph Explorer
- Audit Dashboard

P2:
- Repository Memory Tree
- Policy Layers
- Background Governance Loop
- Model Roles + Context Compression 联动
- CI / PR 集成
```

最终产品逻辑：

```text
Chat 是入口，
Context 是基础，
Governance 是增强，
Experience 是资产，
Reflect 是长期维护，
Audit 是信任保障。
```

