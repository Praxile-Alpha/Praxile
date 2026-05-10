# Praxile

**面向 AI 编程的可治理经验 Harness**

Praxile 会采集 AI Coding Agent 实际做过什么，将一次运行转化为有证据支撑的 proposal，并且只把人工批准过的仓库本地经验写入 `.praxile/`。

它不是通用 Coding Agent，不是隐藏的全局记忆系统，也不是 Spec Kit 替代品。Praxile 是围绕代码任务的治理层：环境交互、轨迹记录、reward、经验抽取、proposal 审核、审计、回滚和下一次检索复用。

[English README](README.md)

项目地址：[https://github.com/Praxile-Alpha/Praxile](https://github.com/Praxile-Alpha/Praxile)

## 为什么需要 Praxile？

很多 Coding Agent 都能改文件、跑测试。更难的问题是：一次任务结束后，项目到底应该记住什么？

Praxile 把这条记忆闭环显式化：

```text
用户任务
  -> 环境交互
  -> 运行轨迹
  -> reward report
  -> evidence / episode
  -> experience proposal
  -> 人工审核
  -> 批准后的 memory / skill / rule / eval / boundary
  -> 下一次任务更好地检索和使用
```

核心原则是：任何长期经验都必须有范围、有证据、可审核、可审计、可回滚。

## Praxile 提供什么

- **仓库本地经验**：memory、skill、rule、eval、failure pattern、project pattern、frozen boundary、architecture gate 都保存在 `.praxile/`。
- **proposal 驱动的进化**：长期经验先进入 pending proposal，不会静默写入 active memory。
- **Spec-aware 上下文**：可读取 `spec.md`、`.specify/`、plan、task、constitution，用于 reward 和 proposal gate。
- **结构化 reward report**：区分任务成功、回归稳定性、过程安全、成本、经验价值和人工反馈。
- **经验图谱**：解释某条经验为什么被加载、某个 proposal 来自哪次任务、某个 spec 影响了哪些 run。
- **工作区隔离**：支持原地运行，也支持每个任务独立 workspace 和 Git worktree 模式。
- **审计与 CI gate**：导出 run、proposal、asset、项目级治理 bundle，并默认脱敏。
- **安全控制**：敏感路径保护、危险命令拦截、diff review、备份、回滚、架构门禁和互操作护栏。
- **可选 terminal / gateway**：可使用普通 CLI、交互式 Praxile terminal 或本地 Web console。

## 安装

Praxile 需要 Python 3.11 或更新版本。

推荐从 GitHub 一行安装：

```bash
pipx install "git+https://github.com/Praxile-Alpha/Praxile.git"
```

也可以使用 `uv`：

```bash
uv tool install "git+https://github.com/Praxile-Alpha/Praxile.git"
```

开发者安装：

```bash
git clone https://github.com/Praxile-Alpha/Praxile.git
cd Praxile
python -m pip install -e ".[http]"
```

可选扩展：

```bash
python -m pip install -e ".[vector]"   # sentence-transformers 语义检索
python -m pip install -e ".[browser]"  # Playwright 浏览器证据
python -m playwright install chromium
```

也可以先审阅仓库中的安装脚本，再运行：

```bash
curl -fsSLO https://raw.githubusercontent.com/Praxile-Alpha/Praxile/main/install.sh
sh install.sh
```

## 不配置模型也能先体验

Demo 本地运行，不需要模型 endpoint：

```bash
praxile demo --fast --accept-first --show-files
```

它会创建一个小项目，记录 trajectory，生成 reward report，产生 proposal，在 demo 项目内接受一条低风险 memory，并展示下一次任务如何检索这条经验。

## 在代码仓库中快速开始

```bash
cd /path/to/your/code-project
praxile init
praxile setup
praxile doctor
praxile doctor --online
```

`praxile setup` 会一步步配置 provider 和 model role。Praxile 默认不内置云模型凭据，也不会保存原始 API key，只保存 `OPENAI_API_KEY`、`OLLAMA_API_KEY` 这类环境变量名。

执行任务：

```bash
praxile run "Fix the failing parser test" --test-command "python -m pytest"
```

审核 Praxile 提炼出的经验：

```bash
praxile review --interactive
praxile accept <PROPOSAL_ID>
praxile explain latest
```

## 模型配置

Praxile 初始是干净的：用户配置前，`model_providers` 为空。

最小可用角色：

- `coding_agent`：自主代码修改任务需要配置它。

推荐的自进化角色：

- `evidence_extraction`
- `experience_reflection`
- `proposal_composer`
- `review_recommendation`

可选语义裁判角色：

- `reward_judge`
- `feedback_classifier`
- `attribution_judge`
- `counterexample_checker`
- `pattern_mining`
- `project_pattern_composer`
- `deep_project_pattern_mining`

常见本地 Ollama 配置：

```bash
praxile setup \
  --provider ollama \
  --base-url http://localhost:11434/v1 \
  --model qwen2.5-coder:7b \
  --api-key-env OLLAMA_API_KEY \
  --channel none
```

常见 OpenAI-compatible 配置：

```bash
praxile setup \
  --provider openai-compatible \
  --base-url https://api.openai.com/v1 \
  --model <your-model> \
  --api-key-env OPENAI_API_KEY \
  --channel none
```

更多路由、fallback、本地优先策略、semantic judge、retrieval、reward 权重、gateway 和 channel 配置见 [praxile.config.example.json](praxile.config.example.json) 与 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)。

## Spec-aware 工作流

当任务有明确意图、非目标、验收标准或成功指标时，可以附加 spec：

```bash
praxile run "Implement search API" \
  --spec docs/specs/search.md \
  --test-command "python -m pytest"

praxile spec verify latest
```

Spec compliance 会影响 reward 和 proposal 质量。一个任务即使测试通过，如果违反范围、漏掉验收项，或绕过 architecture gate 修改架构，也可能产生弱 proposal 或被 proposal gate 拦截。

## 经验资产形态

Praxile 的经验既不只是 Markdown，也不只是 graph。

- 可读的长期资产是 `.praxile/` 下的 Markdown 或 JSON。
- SQLite 索引用于检索、搜索、使用记录、生命周期状态和图查询。
- 经验图谱是解释层，可以从 trajectory、proposal、spec 和 asset 重建。
- 已批准资产默认 active。deprecated、superseded、archived 的资产仍可审计，但默认不会进入检索。

常用命令：

```bash
praxile memory list --include-inactive
praxile skill list
praxile asset status .praxile/memory/project.md
praxile graph status --rebuild
praxile graph explain .praxile/memory/project.md
praxile graph trace <PROPOSAL_ID>
```

## 审计与治理

Audit 命令是只读导出：

```bash
praxile audit run latest --json
praxile audit proposal <PROPOSAL_ID> --json
praxile audit asset .praxile/memory/project.md --json
praxile audit bundle --redaction strict --output praxile-governance-bundle.json
praxile audit check --strict --rebuild-graph --redaction strict
```

脱敏模式：

- `standard`：默认模式，遮蔽疑似密钥，同时保留来源链路。
- `strict`：额外移除原始内容、observation、output 和 diff 摘录。
- `none`：只建议本地调试使用。

`audit check` 适合集成 CI。当经验宪法不完整、有高风险 pending proposal、strict 模式下缺少 graph 证据，或配置要求最新 run 必须成功但实际失败时，它会返回失败。

## Terminal、Gateway 与 Channel

交互式 terminal：

```bash
praxile terminal
```

本地 Web console：

```bash
praxile gateway serve --host 127.0.0.1 --port 8765
```

Channel 配置：

```bash
praxile channel bind telegram -1001234567890 --name team-alerts --token-env TELEGRAM_BOT_TOKEN
praxile channel bind discord 123456789012345678 --name dev-room --token-env DISCORD_BOT_TOKEN
praxile channel list
```

当前边界：Praxile 管理本地 channel binding 和 gateway route metadata。生产级 Telegram / Discord bot listener 是独立 listener 层，位于这套配置之上。

## 常用命令

```text
praxile init                 在当前仓库初始化 .praxile
praxile setup                配置 provider、model role 和可选 channel
praxile demo --fast          运行本地治理经验 demo
praxile run "..."            执行 agent 任务
praxile run "..." --dry-run  只分析和记录，不修改文件
praxile run "..." --workspace-mode copy
                             在隔离 workspace 中运行
praxile review --interactive 交互式审核 pending proposal
praxile accept <PROPOSAL_ID> 接受一个 proposal
praxile reject <PROPOSAL_ID> 拒绝一个 proposal
praxile history              查看 trajectory 历史
praxile explain latest       解释检索、reward 和 proposal
praxile spec check           检查 spec 质量信号
praxile spec verify latest   用 spec 上下文验证已完成 run
praxile constitution check   检查经验治理原则
praxile graph status         查看经验图谱状态
praxile audit check          运行 CI 友好的治理 gate
praxile consolidate --all    为过期或重叠资产生成清理 proposal
praxile models --stats       查看模型路由和历史表现
praxile tools                查看支持的工具动作
praxile rollback             回滚任务修改或已接受 proposal
praxile terminal             启动 Praxile terminal
praxile gateway serve        启动本地 Web console/API
praxile doctor --online      验证配置、模型路由和本地状态
```

## 本地状态

Praxile 在仓库内写入 `.praxile/`：

```text
.praxile/
  config.json
  constitution.md
  memory/
  skills/
  evals/
  rules/
  experience/
  backups/
  db/
  logs/
```

不要把原始密钥写进 `.praxile/config.json`。请通过 `api_key_env` 和 channel 的 `token_env` 使用环境变量。

## 与 Hermes / OpenClaw 的边界

Praxile 可以检测可选 Hermes / OpenClaw 能力，也能使用 OpenAI-compatible endpoint，但它不是 Hermes 或 OpenClaw 插件。

- `.praxile/memory` 不会自动写入外部全局 memory。
- `.praxile/skills` 不会自动安装到外部 skill store。
- Praxile trajectory 是审计源事实；外部兼容 sidecar 只是导出。
- 未来任何外部 sync 都应该通过显式 adapter 命令和可审计 proposal。

## 文档

- [快速入门](docs/GETTING_STARTED.md)
- [配置说明](docs/CONFIGURATION.md)
- [架构说明](docs/ARCHITECTURE.md)
- [经验模型](docs/EXPERIENCE_MODEL.md)
- [审计治理](docs/audit-governance.md)
- [为什么是 Praxile](docs/WHY_PRAXILE.md)
- [安装与互操作](docs/INSTALL_AND_INTEROP.md)
- [测试指南](docs/contributing-testing.md)
- [安全策略](SECURITY.md)

## 当前状态

Praxile 仍处于 Alpha 阶段。当前已经实现本地核心闭环：init、setup、run、trajectory、reward、proposal generation、review、accept/reject、retrieval、graph、audit、rollback、terminal、gateway 和 channel 配置。

第一版不包含：

- 自动训练模型参数；
- marketplace 分发；
- 静默全局 memory sync；
- 自动生产级 Telegram / Discord listener；
- 无限制 shell 执行；
- 自动接受长期经验。

## 开源协议

MIT
