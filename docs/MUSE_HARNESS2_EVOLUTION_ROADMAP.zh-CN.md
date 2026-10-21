# Praxile: MUSE / Harness² 演进路线图

日期：2026-10-19。状态：研发提案；本文件完成不代表下列能力已实现。

## 1. 目标与产品边界

本轮目标是整理研发方向：使后续版本能真实运行 WorkBuddy-Bench 和 JobBench，并以可验证、可迁移、可回滚的经验演化保持 Praxile 的独立价值。

建议定位：**面向专业工作流的 Agent Experience / Eval / Evolution Control Plane，附带可独立运行的参考执行器。**

Praxile 负责决定经验何时适用、以什么形式执行、怎样证明效果、能否长期晋升。用户仍可使用 Praxile 自身运行任务；外部执行器是可替换的兼容目标。借鉴研究方法不等于复制完整框架，也不把项目降为 benchmark 结果导入器。

三条产品承诺：

- 每条生效经验可以追到来源、激活条件、执行事件及后续结果。
- 每次长期升级有独立验证、作用域和回滚版本；证据不足可保持原状。
- 成果是可打开、可检查的文件或可验证状态变化，并能说明经验对交付产生了什么影响。

本轮不开展模型参数训练、通用消费级应用生态或全量桌面操作系统重建。benchmark 兼容不以先建成这些能力为前提。

## 2. 三个参考对象，各取什么

### 学术 MUSE

用户确认参考 [Towards Self-Evolving Agents: Enabling Autonomy through Interactive Experience Refinement](https://aclanthology.org/2026.findings-acl.1522.pdf)，实现见 [KnowledgeXLab/MUSE](https://github.com/KnowledgeXLab/MUSE)。它区分战略、程序和工具记忆，通过子任务执行后的反思支持长任务中的重新规划。

Praxile 的设计推导：引入子任务级 Episode、状态检查与分层经验消费。短期发现进入本次任务的临时层；持久化经验继续经过既有审批和验证。战略经验也按相关性和预算选择，不自动全量加入每次提示词。

### Harness²

[论文](https://arxiv.org/html/2610.14224v1)和[实现](https://github.com/google-research/harness2)强调任务内的 Propose–Probe–Compose，以及跨任务积累改进经验。

Praxile 的设计推导：在已有实验设施之上补齐候选生成、行为对比、组合再验证，并把“如何改进 Harness”的经验与“如何完成业务任务”的经验分开存储。当前任务采用与长期晋升是两个决策；选择了某个输出不构成因果归因。

### Meta Muse 产品

Meta [产品介绍](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/)与[安全设计说明](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse)描述了持续执行的个人工作环境、独立权限决策、凭据隔离与可接管的浏览器。这是厂商公开设计，不是本项目复现或独立审计的结果。

Praxile 的设计推导：长期任务要可见、可暂停、可恢复，执行策略不能自行提升权限；把用户偏好、项目知识和通用技能分开管理。优先实现必要的权限代理和操作审计，不承诺复制 Meta 的 VM、内核级数据流跟踪或整套连接器。

## 3. 当前代码证据与缺口

| 当前证据 | 可复用能力 | 下一步缺口 |
|---|---|---|
| `praxile/adapters/v2.py` | 版本化执行协议、事件与产物、能力声明、取消接口 | 容器内执行及专业文件工作流的能力协商 |
| `praxile/eval/v2/schema.py` 的 `EvalTask` | 冻结任务与执行视图分离 | 强制 `RepositorySpec` 和 `SWEbenchEvaluationSpec`，不能直接表达文件工作区任务 |
| `praxile/eval/v2/runner.py` | 隔离 checkout、产物归档、评估记录 | 主流程绑定仓库准备、patch prediction 和 diff scope |
| `praxile/control_plane/lab.py` | 固定双臂、重复实验、split、覆盖率 | 输入是预先定义的策略，尚需自动候选搜索与组合 |
| `praxile/eval/v2/representation.py`、P2-B 文档 | 激活之后选择经验表示并记录理由 | 还需决定执行组件及子任务状态适用性 |
| `praxile/reflect/engine.py` | 重复、过时、有害资产及拒绝主题治理 | 缺逐项修改的执行对比和混杂因素记录 |
| `praxile/browser.py` | 浏览器打开、截图、生命周期释放 | 当前该模块没有完整的交互、状态断言与接管协议 |
| P2-C / P2-D 文档与代码 | Judge 分离、晋升门禁和实验作用域 | 专业文件质量、部分得分及跨领域保留能力要接入 |

上述是本轮定向代码审计，不是全项目质量认证。现有机制的存在不证明有效增益；本轮没有运行 benchmark。

## 4. 冻结的架构边界

本轮后续实现属于架构变更，影响任务协议、环境所有权、评估结果和经验生命周期。

1. **环境与评分归属**：benchmark 环境插件负责 provision/reset/collect/destroy；官方评分器拥有评分规则。Agent 仅获得执行所需任务与材料。
2. **单一环境所有者**：Harbor 管理的任务不再由 Praxile 重复创建第二层工作区或容器。Praxile 管理实验身份与治理，环境插件返回 handle。
3. **权限归属**：Agent 和可演化 Harness 无权修改控制面审批、凭据代理、网络策略及最终评分器。
4. **经验证据**：retrieved、activated、injected、invoked、behavior_changed、verified_effect 分开记录。缺失观察记 unknown，不能补成成功。
5. **经验作用域**：task-local / project / domain / user / global 显式区分。跨项目迁移必须重新检查前置条件；用户习惯不能覆盖客观任务约束。
6. **实验隔离**：每 arm/repetition 独立可变状态；冻结并记录共同初始经验快照。held-out 结果不能反馈给同一轮候选生成或记忆更新。
7. **统计边界**：保留官方分数与分母；基础设施错误、未交付、评分不可用分开报告，禁止只统计成功完成的任务。
8. **长期晋升**：保留 component + executor profile + task family 作用域，并记录环境、工具与资产版本。研究中的临时采用不覆盖生产活动版本。

## 5. 目标调用链

```text
CLI / Web Console
  -> Experiment Service (任务版本、预算、split、经验快照)
  -> Benchmark Adapter (WorkBuddy / JobBench / SWE-bench)
  -> Environment Lease (Harbor/Docker 或 Git worktree)
  -> Execution Adapter (Praxile Native；可选外部执行器)
       -> 子任务计划与状态检查
       -> 激活门控 -> 表示/组件选择 -> 执行
       -> 临时 Episode -> 局部修复 / 继续
  -> Artifact Collector -> 官方 Evaluator
  -> Trace / Outcome / Cost Store
  -> Evidence Contrast -> Experience / Harness Candidate
  -> 冻结候选 -> 独立实验 -> 六道门禁 -> 人工批准 -> 版本发布
```

执行器不读取 Evaluator 的私有存储。public/proxy checks 可驱动任务内修复；最终 hidden evaluation 仅用于控制面决策。

## 6. 阶段 R0：真正跑通两个 benchmark

R0 是首个实现阶段，不沿用旧 P0/P1 编号，避免与已完成的 V2 里程碑混淆。

### R0-A 通用任务与环境协议

- [x] 增加版本化任务 envelope：instruction、输入材料 manifest、environment spec、输出 contract、private evaluator reference；不要求存在 Git。
- [ ] 增加环境租约协议：prepare/reset/execute/collect/cancel/cleanup，分别支持已有 worktree 与 Harbor 管理的容器。
- [ ] 任务输入和最终评分材料分别下载/存储/挂载；不把含 rubric 的原始数据行直接传给执行器。
- [x] 旧 SWE-bench task 经显式兼容映射继续运行；旧 manifest 的 digest 和 resume 语义不变。
  Eval Runner report 的 benchmark_contracts 使用现有 from_swebench 显式投影，版本标为输入任务集内容 digest；旧执行 backend/evaluator 不变，legacy manifest 不改写，resume 不重复运行已完成任务。25 项合同/runner/A-B 测试通过，包含 manifest 字节稳定、输入摘要稳定、投影一致与私有测试/答案不泄漏。这是兼容接入，不代表已用通用 Harbor 执行 SWE-bench，也不是新增模型性能验收。
- [ ] doctor 输出缺失依赖、容器架构、磁盘、执行模型和评审模型状态；Linux 环境与 Mac Docker Desktop 分别给出实测支持矩阵。
  自检增补：在线查询 daemon OS/架构，主机磁盘容量只读观测；离线未查询 daemon 时不宣称 infrastructure_available。10 项测试通过，本机离线查询已验证；Docker VM 磁盘、模型/judge 连通性和 Linux/Mac 完整实测矩阵仍未完成。

代码落点：在 `eval` 下增加通用协议与插件，不先大改 legacy runtime；通过桥接保留现有 `eval/v2`。benchmark 可选依赖使用独立环境，避免上游 Python/Harbor 版本拖动基础安装。

验收：同一契约测试覆盖非 Git 输入、只读输入、取消/超时/崩溃、清理、resume 幂等；旧 mini-SWE 路径无回归。

R0-A 第一批实现（2026-10-19）：`praxile/eval/contracts.py` 提供通用任务、环境描述、输入/输出与评分记录；执行视图使用白名单投影，旧 SWE-bench 任务可显式转换且原始序列化不变。`test_benchmark_contracts.py` 与原有 `test_eval_v2_schema.py` 合计 25 项通过。当前验证的是契约、私有字段不泄露、路径拒绝和序列化兼容；实际环境租约、文件只读挂载、运行恢复和真实 benchmark 验收仍未完成。

R0-A 第二批：`praxile/eval/lease.py` 增加本地工作区租约，支持执行、产物读取、取消、超时进程组回收与幂等释放；仅对显式传入的 release 回调委托环境销毁，默认不删除工作区。资源测试通过 `--forked` 隔离运行。该后端不是安全沙箱，目前仅支持 POSIX；reset 明确要求通过 preparer 创建新租约，尚未接入正式 runner。Harbor 管理容器、只读输入挂载、持久化恢复及两个 benchmark 的真实执行均仍未完成，因此环境协议总项不勾选。上游依赖需分别隔离：WorkBuddy 固定 Harbor 0.18.0，JobBench 固定 0.22.0。

### R0-B WorkBuddy Adapter

公开约束 + v3 设计验收：`bca6326` 一次 low 请求 15.618 秒返回 NO_ENTRY，模型
仍自报缺公开内容约束（实际已传入）和动作参数。新增确定性输入清单，将输入
存在性与模型的充分性判断分开。未生成候选，不继续对此稀疏来源重复付费请求。
见 `results/office-flash-public-context-design-20261019.json`；下一步仍需新的可验证
开发 Episode 或更细动作语义，不据此宣称有效进化。

文档证据 v3：读取参数投影补入显式 limit/char_offset，修复字符分页摘要遗漏；
同任务/trace/路径的连续读取关联前次事件和内容哈希，证据选择保留依赖闭包。
冻结 Office 轨迹离线重放得到 7 条证据，两次输入读取同字节，输出读取无前次
版本保持 unknown；未推断修改因果、未调用模型。原始命令和正文仍不导出，
完整动作语义与状态对齐仍缺，不据此勾选 R2 总项。

low 强度实测：`b3fed9d` 同证据同预算一次请求 14.587 秒返回完整 NO_ENTRY，
2105 input / 1243 output，模型自报缺少动作参数、前后状态和公开内容约束。
这是结构化弃权而非有效候选或性能提升。新增 opt-in、摘要绑定的公开任务上下文
入口，保留调用方声明而非认证的边界；未加入私有 rubric，尚无新上下文质量验收。
结果见 `results/office-flash-low-design-20261019.json`，完整经验闭环仍不勾选。

120 秒设计请求复验：`ed38302` 的 M3.1 Flash 在 87.560 秒完整返回，但 8000 输出
token 全部为 reasoning，最终正文为空、length 截断；无候选/无晋升。官方文档
确认默认 effort=max，下一步应冻结较低强度的新配置，不继续盲目增加预算。
本轮只补齐 effort 枚举与请求正文测试，没有自动更改用户配置或追加模型请求。
结果见 `results/office-flash-durable-design-120s-20261019.json`；有效经验闭环仍未完成。

最新原生设计入口验收（2026-10-19）：`eval design-native-context` 已与旧设计入口共享
请求意图、冻结预算、轨迹和拒绝未知请求重放的恢复逻辑。实际通过其服务入口向
MiniMax M3.1 Flash 发送 7 条公开投影证据，零重试、无 fallback；一次请求 60 秒后
超时，无候选，费用未知，未晋升。监督器确认进程回收，不能据此声称远端停止计费。
随后增加显式 `--request-timeout` 预算配置，但没有追加付费验收。详见
`results/office-flash-durable-design-20261019.json` 和 `IMPROVEMENT_EPISODES.zh-CN.md`。
原生设计运行边界已加强，但有效候选、probe/compose、held-out 增益仍未证明，
本节及 R2 完整闭环继续不勾选。

原生经验入口进展：新增 `native_development_evidence`，从带 SHA256 的原生执行结果按显式开发任务名单生成白名单证据，可显式转交既有 CandidateDesigner；CLI 不调用模型、不自动写入长期经验。真实 Office 轨迹投影出 3 条工具状态证据，未据此生成候选或宣称收益。开发归属仍为调用方声明，缺少独立来源认证和更丰富的公开语义证据，因此不勾选完整经验闭环。后续真实调用按用户要求使用 `MiniMax-M3.1-Flash`，既有 M3 结果不改写或混成同模型 A/B。

真实 Office Trial 阶段进展（2026-10-19）：固定 amd64 镜像通过 Harbor 0.18 完整运行 `drug-inventory-split-L2-006`，MiniMax M3 新鲜基线无经验注入；官方 CompositeVerifier 13/15、reward 0.8667（partial_pass），48 个原生事件绑定成功，输入快照一致、凭据撤销和容器清理已验证。并发现原 Job 未收集 output 产物，故本次不能算完整交付验收；已修复生成器声明 artifacts，4 个 fixture Trial 验证成功/失败产物在清理后均保留。这不是 A/B 收益证明，也不满足全部 WorkBuddy 类别覆盖；总项保持未完成。详见 `HARBOR_NATIVE_EXECUTOR.zh-CN.md` 与 `results/workbuddy-inventory-baseline-20261019.json`。

R0-A worktree 后端增补：新增 WorktreeLease，复用既有 preparer，生成独立租约身份并固定初次解析的 commit；支持真实 reset、执行取消、创建取消后的回收及幂等 cleanup。重置失败后禁止继续执行。11 项 Git/租约/旧 runner 资源测试通过；旧 runner 未迁移，Harbor 生命周期统一接入、只读挂载和跨进程恢复仍缺，完整租约项保持未勾选。见 `WORKTREE_LEASE.zh-CN.md`。

阶段进展（2026-10-19）：新增受摘要与资源限制保护的数据包解包、WorkBuddy 清单冻结及只修改新副本的官方准备流程。真实 Code 80 条、Office 50 条材料已冻结；Office 50 条已在副本绑定官方评分器和 dev 用户，19 项相关测试通过。原生执行器增加固定的非 root 初始化。仍缺真实任务执行、结果导入、Web/Security 环境与恢复验收，下列总项不勾选；具体摘要与命令见 `HARBOR_NATIVE_EXECUTOR.zh-CN.md`。

后续进展：新增固定 Harbor 0.18.0 的 job 生成，显式任务选择、单并发、零自动重试、结束清理；Security 在专用网络策略验收前拒绝执行。发现 task TOML 的 verifier import_path 被 Harbor 忽略，已改为 job 层显式绑定，工厂测试确认 CompositeVerifier。31 项相关测试通过，50 条真实 Office 材料通过 Harbor 配置解析；未开始真实模型任务评分，总项继续保持未完成。

异常路径增补：原生执行器记录独立 execution status，超时/取消/非零退出后限时回收已存在的结果，损坏下载不发布，保留主异常。尚未实现容器强杀后的完整 checkpoint 恢复，因此不据此勾选恢复验收。

容器内期限增补：native worker 由 POSIX supervisor 执行并管理进程组，超时/终止回收，宿主记录白名单监督状态。4 项真实本地进程测试及 Harbor 0.18/0.22 各 9 项适配器测试通过。宿主取消即时传递、容器级强杀恢复和新版 wheel 的真实 Docker 验收仍缺，未勾选完整生命周期能力。

容器复验更新：新版 wheel 已在 Mac Docker Desktop Linux ARM64 上，通过 Harbor 0.18 与 0.22 各 2 项真实容器测试，覆盖普通/dev 用户、fixture 模型运行及进程组超时回收。镜像与 wheel 摘要见执行器文档；此次补齐新版 wheel Docker 验证，不包含官方任务/真实模型分数。即时取消与强杀恢复仍未完成，因此总项不勾选。

取消握手增补：run 取消通过专属标记请求容器内 supervisor 停止，再限时确认终态；请求成功与停止确认独立记录，失败保留原取消异常。6 项本地进程测试、两个 Harbor 版本各 11 项契约/各 2 项 Docker 测试通过。真实 Docker 验证 helper 到进程组回收；Harbor 顶层取消、daemon 失联、强杀恢复和完整租约统一接入仍未验收，总项保持未完成。

Agent 入口取消复验：两个 Harbor 版本各 4 项真实容器测试通过。等待 worker 已向阻塞 fixture 模型发起请求后取消 Agent.run，验证取消传播、进程组回收、终态确认及结果缺失的正确记录；普通/dev 用户均覆盖。完整 Harbor Trial 生命周期、daemon 失联和强杀恢复仍缺，未把 Agent 入口验收扩大为完整租约验收。

结果导入增补：新增官方 score/reward/trial 的一致性校验与规范化，保留逐项判断，区分有效零分和 judge/build 错误，缺 usage 不补零，执行与评分状态分开。16 项导入测试及其余相关测试合计 47 项通过（含上游 ArtifactWriter 兼容）；真实任务评分与可信隔离尚未完成。

自检增补：`doctor --benchmark workbuddy|jobbench [--online]` 不初始化项目，检查固定 Harbor 版本/插件/Docker；online 仅检查 daemon，不调用模型。基础设施可用与真实执行验证分开，模型/judge/镜像架构未知时明确显示。5 项测试及真实 WorkBuddy 基础设施查询通过；完整能力矩阵与各任务族验收仍未完成。

真实镜像尝试：Office `api-usage-explain-cli-l3-001` 的原始环境显式 amd64 构建，在基础镜像 metadata 阶段因本机 Docker 镜像源 `registry.docker-cn.com` TLS 超时失败；未进入 Agent/评分，不计零分。另确认 Harbor 默认按 daemon 架构构建，ARM Mac 与任务固定 x64 Node 的兼容路径待验证。详见执行器文档，不勾选真实任务验收。

阶段实现记录（2026-10-19）：新增 `praxile.adapters.harbor_native:PraxileAgent` 和 `praxile.eval.native_worker`，固定 wheel 双端 SHA-256 校验、容器离线安装、调用真实 AgentRuntime、原始轨迹下载和 token 统计回填已实现。Harbor 0.18.0 / 0.22.0 分别在独立环境通过真实 Docker + 模拟模型测试，各 2 项；基础回归 34 项通过。复现与边界见 `HARBOR_NATIVE_EXECUTOR.zh-CN.md`。这是执行通道验收，不是官方 benchmark 成绩；任务发现、正式评分、私有材料隔离、断点恢复和真实任务验收仍未完成，以下总项保持未勾选。

官方 [WorkBuddy-Bench](https://github.com/Tencent/workbuddy-bench) 当前基于 Harbor/Docker，含 Code/Web/Office/Security 子集。实现时固定仓库提交、数据修订、镜像摘要和评分配置，不能把 Harness² 的论文子集当成官方全量任务集。

- [ ] 接入官方任务发现、执行和评分，先 Code，随后 Office/Web，最后 Security；完整兼容发布必须报告四类覆盖情况。
- [ ] 支持 Praxile Native 作为真实执行主体，规范化 Harbor trial identity、原始轨迹、文件产物和评分结果。
- [ ] 保留官方复合评分配置及各项结果；LLM/VLM 评分启用情况和模型身份进入报告。
- [ ] Security 使用独立受限网络策略与一次性沙箱，避免沿用日常项目权限。
- [ ] 公布每类至少一个真实任务的完整执行证据；小样本用于集成验收，不用于增益声明。

### R0-C JobBench Adapter

工具 profile v2 真实设计返回完整文本但 JSON 解析失败（未进入动作校验），无候选。
新增可选函数提交传输，严格检查唯一同名函数与 JSON arguments，不执行工具，
不静默降级。候选 CLI 已通过 `--response-mode function` 显式接入，manifest
冻结完整合同，历史默认文本模式保持兼容。一次 MiniMax M3.1 Flash 实测通过
指定函数提交及严格 JSON 解析，但首个 read_document 示例存在 4 项 schema
错误，未生成可执行候选。后续补充参数范围与脱敏字段诊断，仅经离线测试，
不回填旧结果或自动重试。详见 IMPROVEMENT_EPISODES 与
`results/docx-repair-function-design-20261019.json`，不勾选完整生成/验证闭环。

后续同合同复验在证据引用处失败，没有候选。新增默认关闭的
`--repair-attempts 1`：仅对本地校验失败做一次有轨迹、有预算的模型修订，
不重试网络错误、不放宽证据/动作校验、不自动执行或晋升。每次完成后保存
轨迹，manifest 冻结预算。一次真实修订已执行：首次候选数量错误，修订后
动作参数校验仍失败，无合法候选。具体诊断反馈增强仅离线验证；有效候选、
独立 probe 及组合验证仍待完成，不能把限次机制验收当成质量收益。

具体反馈真实复验仍在引用/动作校验失败，已停止同配置重复 DOCX 设计。新增
独立合成提交协议探针，检查精确结构/引用/整数，默认不运行、不改变能力声明；
已完成离线验收，真实模型协议与有效候选仍待验证。该探针不是候选任务 probe。

协议探针真实验收更新：basic 合成复制通过，candidate 合同下精确复制失败但
具体差异类别未知；不据此宣称复杂 schema 不受支持。新增分项检查仅离线验证，
详见 `results/minimax-submission-probes-20261019.json`。有效候选闭环仍未完成。

后续分项实测定位到动作差异，新增 opt-in function_json_actions 版本化编码，
严格解码后仍按原动作 schema 验证，禁止数值强制转换。初版合成复制通过，
开发设计仍失败；补充 type 判别字段与完整动作 schema 说明仅离线验证。
旧协议不改，详见 `results/minimax-json-action-submission-20261019.json`。

typed-actions-001 的最终 v2 说明真实验收通过：首次生成两候选及组合，无修订，
完整校验与离线作用域激活通过。公开脱敏证据/候选包支持无 API 重放，位于
`results/docx-typed-actions-20261019/`。候选固定条目数及占位示例有内容保持风险；
尚未执行或晋升，完整 R2 闭环仍不勾选。

独立执行前置修复：Harbor adapter/worker/凭据代理现已显式传递并固定
model_request_options（如 reasoning_effort=low），防止设计器和执行器采用
不同默认推理设置。代理拒绝 worker 覆盖参数；返回记录不一致则拒收。
通过单元及 Harbor adapter 测试，真实 M3.1 候选任务 A/B 尚未开始。

修订来源设计复验：`5602042` 的 M3.1 Flash low 返回完整 JSON，但首个候选动作
示例校验失败，未发布候选或执行 probe。原记录不能定位具体字段，新增有界脱敏
校验诊断；另提供 opt-in 工具 profile v2 数值/SHA 约束，保留 v1 摘要兼容性。
这些后续改动尚无真实模型复验，不能把格式拒绝当成经验质量或性能改善。见
`results/docx-repair-flash-design-20261019.json`。

文档修订来源审计：投影 v4 增加经 DOCX schema 校验的替换结构统计。已有 seeded
JobBench compact repair 离线回放四次修订，字符数 4783→3954→3232→2494，表格
始终 15×6，布局记录仍为两页。可用于审计动作与结果，但内容保持/因果仍未知。
未调用模型，未生成候选，未将 seeded repair 混入原始 benchmark 或 held-out。

最新真实复验（0ba84dd）：同一 easy technical-writers 任务完成两份可解析
DOCX，14 次模型调用，1 次 length 截断被拒绝后恢复。独立原版官方 verifier
得到 73/82、reward=0.8902（9 项通过 8 项）；摘要和冻结权重校验一致。
用户另行授权仅 judge 向 MiniMax 发送评审材料，执行器不接触 rubric。
本地渲染发现 checklist 实际 5 页而非要求的 1 页；原始产物未修改，不将
模型 completed 或结构检查通过当成全部要求通过。完整证据摘要见
`results/jobbench-inventory-smoke-20261019.json`。这是单任务 standalone verifier
链路，不是完整 Harbor Trial、held-out 提升或全量验收。judge token/费用账本、
正式 Trial 导入、恢复与其他任务覆盖仍缺，R0 总项保持未完成。

协议版本真实复验仍未完成交付：两份 .docx 实为 0/4 字节文本，官方离线
extract-only 虽返回 complete，但正文为两个格式读取错误；未调用付费评分。
据此禁止 edit_file 写入/覆盖二进制文档，明确工具边界；表格 schema 错误
增加具体位置。结果见 jobbench-protocol-smoke-20261019.json，不勾选真实成功。

90 秒请求上限复验：10 次 MiniMax 响应后因连续非法动作停止，仍无交付物。
新证据定位到 DOCX 嵌套契约描述不足及前置 think 文本干扰，已补完整结构提示
和保守的 reasoning/final 边界解析（非法文档字段仍拒绝）。536 项快速测试通过，
该协议修复尚无真实交付验收；不能用本次较少 tokens 声称性能提升。

独立官方 judge 镜像已按原始 Dockerfile 构建，无网络检查确认固定计算环境
标识和评分入口；未改 rubric、依赖或 generation。该记录当时尚无非空交付物的
官方评分；后续单任务评分见本节最新复验。镜像不可提供给执行器，正式
任务验收总项保持未完成。

正式文档工具真实复验：6050fbe 已能在冻结任务镜像中多次调用 read_document，
但最后模型请求超时，输出仍为空，未调用官方 judge。记录到的成功响应输入
558,536 tokens，失败请求用量未知。补充显式单请求超时配置及准确的中途失败
提示，不自动增加预算/重试；详情见 jobbench-documents-smoke-20261019.json。

真实新入口复验已完成：同一 easy technical-writers 任务在 c82541c 下越过误报
门禁，产生 30 次 MiniMax-M3 调用（336,906 input / 3,564 output tokens），
到达步数上限，未生成交付物，官方评分未运行。容器已清理。结果暴露 R1 正式
文档读取/生成工具缺口和 JSON action 协议失配；不增加步数掩盖问题，不把动作
success 当作任务成功。详见 results/jobbench-intent-smoke-20261019.json。

任务意图边界修复：新增显式 TaskInput V1，正文与完整环境说明分别记录；Harbor
适配器可通过公开正文相对路径和冻结 SHA-256 传递该输入。worker 校验路径、
大小和摘要；正文用于意图分析，完整说明继续进入模型，隐私信号不丢失。
真实架构正文及 frozen boundary 的拦截保留。该入口仅供可信控制面配置，
不自动识别/忽略条件句，不开放模型 gate bypass；新的真实任务复验仍待执行。

隔离构建与真实执行入口复验：用户批准仅在副本把 Debian HTTP 改 HTTPS，
Linux ARM64 任务镜像构建及无网络依赖检查通过，原冻结 manifest 未变。
随后用 Harbor 0.22 原生适配器运行原始公开 instruction（30 步/900 秒上限），
发现 TaskAnalyzer 将通用说明中的 `database or workbook` 判为架构变更，
在第一次模型请求前返回 needs_human。模型调用和输出文件均为 0，容器已清理；
不是官方完整 Trial，也未调用 judge，不生成零分。该结果暴露 Coding 任务意图与
环境说明混合的入口问题，必须补齐中立任务分析边界，不能靠关闭架构门禁解决。
详细摘要及原始证据 hash 见 HARBOR_NATIVE_EXECUTOR.zh-CN.md；真实评分总项不勾选。

真实环境进展：easy technical_writers/task1 原始基础镜像经官方 registry 同摘要映射
成功获取（未改任务文件或全局镜像源），后续 APT 下载因 deb.debian.org:80 连接失败
返回 100，最终镜像未生成。未调用执行模型或 judge，不计任务零分。官方 judge 的
max_completion_tokens=200000 已进入代理保真/预算兼容测试；499 项快速单元通过，
不等于真实评分验收。详见 HARBOR_NATIVE_EXECUTOR.zh-CN.md。

阶段实现记录（2026-10-19）：`praxile.eval.jobbench` 已支持读取、完整校验上游不可变 generation，按 main/easy 与精确 task ID 选择，并生成通过 Harbor 0.22.0 schema 的原生执行 job。公开输入与 tests 引用分开投影；摘要不匹配、额外文件、符号链接、共享 verifier 和完整 rubric 复制到环境会被拒绝。7 项单元测试及上游真实 renderer 协议测试通过；这是 fixture 材料的接入验收，尚未下载/执行真实任务或调用官方 judge，因此下列正式能力总项不勾选。

后续进展：已下载并通过官方 renderer 准备 main/easy 各一条真实 technical-writers 材料，冻结数据 revision 与 manifest 摘要见执行器文档。`jobbench_results` 可校验并导入官方逐项分数，验证冻结 task checksum 和 rubric 权重，区分有效零分与评分错误，保留私有原始证据。45 项相关单元测试、2 项上游集成测试通过；真实 main rubric 的官方无产物分支得到 `0/66`，未调用模型。非空产物 LLM 评审、真实 Agent 执行、受限参考材料搜索、恢复与全量覆盖尚未完成。

官方 [JobBench runner](https://github.com/Job-Bench/job-bench-eval) 提供普通 CLI 与 Harbor 路径。选择 Harbor 作为首条共同基础设施，评分逻辑仍调用其官方实现。数据版本与 split 以 [数据集 manifest](https://huggingface.co/datasets/JobBench/job-bench) 实际内容为准。

- [ ] 支持 main/easy、任务筛选和正式 split manifest；easy 是独立难度数据集，不能默认当作 main 的训练集。
- [ ] 构造只包含授权参考文件的工作区；实现需要搜索材料的任务的受限检索接口。
- [ ] 收集多文件输出并使用官方抽取/评分路径，保留评分错误与抽取局限；生成模型和 judge 独立配置。
- [ ] 对齐官方 rubric 权重与汇总口径，同时保存逐项结果，禁止简单套用 SWE-bench resolved 指标。
- [ ] main/easy 均产生真实执行与评分记录；正式支持报告枚举全量任务，不能通过跳过不支持格式宣称全量兼容。

R0 出口：两个 benchmark 都能执行、评分、恢复、追溯和清理。只有完成正式 manifest 的全量运行并公开失败/unsupported 才能称“全量评测完成”；分数不设虚构提升目标。

## 7. 阶段 R1：专业任务交付能力

页数检查增补：`check_document_layout` 正式接入工具/schema/路径权限，用户
显式配置可信 LibreOffice 后，以快照和限时进程组获取真实页数，超页失败；
不继承模型密钥、不覆盖原稿。真实两页与一页修订 fixture 通过，旧 checklist
离线复查为 5 页且失败。页数来源不冒充可信用户契约，视觉验收仍为 false。
后续补齐 Harbor renderer 配置透传及 caller-bound DOCX max_pages：完成前
校验不通过会向模型反馈并在剩余步数内重试，最终再检查；缺依赖/摘要变化均
不通过。JSON fixture 修复闭环、真实 DOCX 超页拦截已验证。benchmark 公共
契约输入通道已补：Harbor 构造时冻结对象，上传后由 worker 在模型调用前检查
文件摘要、大小及严格 JSON 并绑定 Runtime；未提供保持原行为。HTTP fixture
和真实 Docker 普通/dev 用户验证了门禁，不读取隐藏 rubric。同路径 revise_docx
已补：期望摘要、生成后重新比对、二进制备份、原子替换及摘要保护回滚；
真实 LibreOffice + 模型 fixture 验证两页拒绝/修订为一页/完成/回滚。此锁只
协调遵守协议的进程，不是完整 OS CAS 或断电事务。真实模型修订验收仍缺，
完整自检/修订项不勾选。具体边界见 `DOCUMENT_TOOLS.zh-CN.md`。

产物回收边界增补：原生 worker/Harbor 可显式配置输出目录，执行后生成独立
artifact_inventory，区分不存在、空、格式问题、不支持和未检查完；来源是
observed_outputs，不冒充用户契约。模型 completed 不覆盖机器检查 issues。
旧真实无效 DOCX 两文件离线复查均失败；完整任务要求满足和官方评分仍未完成。
详见 DOCUMENT_TOOLS.zh-CN.md。

受控生成增补：create_docx 接入声明式标题/段落/列表/表格/分页符，禁用脚本和
模板执行；仅新建、不覆盖、同目录 staging 原子发布。权限、dry-run、快照和
轨迹已接入，回滚仅删除摘要匹配的新文件，保留后续人工修改。真实 DOCX 回读和
失败路径测试通过；这是基础结构化写出，不是版式验收，XLSX/PDF 生成、渲染、
重算与新的真实 benchmark 仍未完成。详见 DOCUMENT_TOOLS.zh-CN.md。

文档读取通路增补：新增 read_document 动作，PDF 页/XLSX 行/DOCX 正文块分页、
长文本续读、文件快照摘要、受限解析子进程和错误返回已接入工具及 Action Schema。
read_file 对这三类二进制格式提示改用正式工具。Harbor 可显式继承冻结任务镜像
依赖，默认关闭并记录选择。当前只完成读取，产物生成、渲染、重算与真实模型复验
仍缺，专业交付总项不勾选。边界见 DOCUMENT_TOOLS.zh-CN.md。

增量进展（2026-10-19）：新增默认关闭的任务内浏览器会话，navigate/click/fill/精确文本断言/当前状态截图已接入 runtime action 工具链；请求白名单、Socket/Service Worker 限制、错误脱敏与生命周期清理已覆盖。8 项会话单元测试、1 项真实 Chromium 集成测试通过。下载、接管、完整状态断言与恢复未完成，浏览器总项仍不勾选；详见 `BROWSER_SESSIONS.zh-CN.md`。

交付检查增补：新增独立版本化 public deliverable contract 和 `check_deliverables` runtime action，支持 JSON 字段、CSV 结构、UTF-8 文本、文件缺失/大小/路径检查及摘要；不改变 BenchmarkTask V1。12 项测试通过。结果明确不是官方评分或语义质量判断，二进制 Office/PDF opener、状态变化契约和可信来源确认尚缺，总项不勾选；详见 `DELIVERABLE_CHECKS.zh-CN.md`。

格式检查后续：Office/PDF opener 已以可选 documents extra 接入限时子进程，包含 ZIP/XML 预检和结构统计；9 项资源测试覆盖四种真实格式、损坏/超时/宏/实体声明。公式重算、渲染、二进制格式的用户结构要求、可信确认及状态变化契约仍缺，R1 总项继续未完成。

用户结构检查后续：已支持格式专属的 structure 约束（CSV 行数、XLSX 工作表名、DOCX 正文标题/表格数、PPTX 幻灯片数、PDF 页数），可区分“能打开”和“符合所声明结构”。19 项单元及9项资源测试通过。任意结构/视觉质量、可信确认、公式重算、状态变化仍未完成，不勾选完整交付总项。

终态契约增补：Deliverable Contract V2 支持 state_equals，可与文件要求共同检查或表达纯状态交付；ToolRegistry 使用当前浏览器观测，不采用模型自报状态，未知/类型不符均不通过。33 项交付测试和 455 项快速单元测试通过。V1 不变；前后允许变化范围、用户可信确认与持久化观测来源尚缺，明确不宣称状态转换验证，完整项不勾选。

调用方契约绑定增补：run --deliverable-contract 及 Runtime 参数可冻结公开契约副本/摘要，经轨迹和 checkpoint 保存；模型不能弱化、resume 不能换契约或任务，结束时强制复验，不满足则 completed 降为 needs_human。原地和隔离 CLI 路径均传递绑定。458 项快速单元测试通过。该机制是明确调用方输入，不等于签名人工审批；Web 输入控件、前后允许变化及完整来源证据链仍缺，总项继续未勾选。

Web 契约通路增补：同步 run、异步 jobs、同步/异步聊天均接受受限内联契约并共用 RunService 校验；入队前拒绝非法契约，不读取服务器路径。聊天重试保留原契约且拒绝替换/删除，run 详情暴露冻结摘要和复验结果。16 项 Gateway 契约/Console 回归通过。前端契约编辑器、job 队列跨进程恢复和完整状态转换证据仍缺，总项不勾选。

React 契约界面增补：独立 React Console 支持可选 JSON 契约输入、错误提示、提交防重复、job 完成刷新及冻结要求/文件与状态检查展示。Vite 生产构建和桌面/手机 Playwright 两项测试通过，已检查截图与页面溢出；API 为 fixture，不代表真实模型 UI 验收。内置 Python Console、完整状态转换、任务接管与跨进程恢复仍缺，完整 R1 界面项保持未完成。

观测证据增补：V2 交付报告保存契约引用字段的受限实际值、未知/省略原因、来源、采集时间及可复算摘要，沿既有轨迹持久化；不会保存整个浏览器状态。37 项交付单元测试、19 项绑定/Gateway 资源回归通过，含存储往返。摘要不作为签名，provenance_attested=false；前后允许变化范围与独立见证仍缺，完整交付项不勾选。

回归审计：新增浏览器修改动作后发现 dry-run 原先仅阻止 edit_file/run_command，已将 click/fill 纳入共用修改动作集合。8 项实际 action-loop 单元测试覆盖正常与 dry-run 分发差异；不启用模型、浏览器或 SQLite。导航仍是允许的网络读取，dry-run 不等于完全无副作用沙箱。

下载增补：browser_download 为显式 opt-in 的公开 HTTP URL GET，逐跳白名单、流式大小限制、临时文件清理、项目内随机产物路径和 SHA-256 证据已接入 runtime；dry-run 阻止分发。6 项真实本机 HTTP 测试通过。无 cookie/登录态/点击下载，Playwright 隐式下载仍关闭；DNS/IP 隔离、认证导出、接管与完整浏览器工作流未完成，总项不勾选。见 BROWSER_SESSIONS.zh-CN.md。

浏览器安全回归：一次性 open/screenshot 改用与交互共享的受限 context。真实测试发现 route.continue_ 的重定向可先触达非白名单目标，现改为禁止 fetch 自动跳转并拦截全部重定向响应；目标测试服务器已验证无越界请求。21 项单元与3项真实 Chromium 测试通过。允许域内自动重定向也暂时拒绝，流式页面/完整登录流程不在兼容承诺内；完整浏览器项不勾选。

这阶段吸收 Meta Muse 的长期任务体验与 MUSE 的子任务组织方式；R0 使用的基础工具在 R0 一并实现，R1 负责系统化增强。

- [ ] Deliverable Contract：目标路径、格式、必要结构、允许的状态变化、证据位置；来自用户任务，不能从隐藏 rubric 推导。
  V3 增补调用方冻结的 unchanged_files 路径/SHA-256 约束，最终字节变化、缺失、
  符号链接或超限均失败；与输出/终态要求联合检查，沿用 runtime 完成门禁。
  629 项快速单元测试通过，仍不证明运行途中无修改、目录无增删或外发，完整
  状态变化项不勾选。协议与限制见 `DELIVERABLE_CHECKS.zh-CN.md`。
  React V3 通路增补：修复前端仅允许 V1/V2 的版本限制，展示文件保留的期望/
  实际摘要、变化与未验证状态；缺证据不隐藏冻结路径。3 项 Node 测试、10 项
  桌面/手机 fixture 浏览器用例和构建通过，截图已检查；非真实模型 Web 验收。
- [ ] 按实际任务依赖建立版本化 capability profiles：表格读写/公式重算、文档/PDF抽取与渲染、网页浏览器操作、代码与脚本执行。
  worker 依赖锁定增补：支持显式 wheel 路径/摘要清单、上传前及容器内校验、
  离线安装和 pip check；清单进入环境摘要、执行记录与版本标识，额外依赖环境
  不视为原环境严格 A/B。Harbor 0.18/0.22 各 30 passed、8 skipped；新增清单的
  后续真实 Docker 离线安装在两版 Harbor 各 1 passed，确认依赖仅存在于 worker
  venv，系统 Python 未受影响，0 模型调用。见 worker-dependency-docker-20261019.json；
  不代表完整文档渲染或任务验收，完整项不勾选。
  文档过程证据增补：成功修订的 before/after 摘要进入开发证据；布局检查按
  相同路径和唯一先前版本摘要引用修订事件，歧义/不匹配保持 unknown，不按
  时间邻近猜测因果。真实 compact repair 轨迹离线回放 4/4 可关联，不包含
  正文/评分材料，不表示候选收益已验证。
  前后布局比较增补：按修订 before/after 摘要匹配唯一先前检查，核对相同页数
  上限和 renderer 路径，输出 page_delta 或 unknown。冻结 compact repair
  回放为 null/0/0/0，未见后三次页数改善；不证明内容质量或环境二进制等价。
  证据选择增补：失败优先采样同时保留修订/前置检查依赖闭包，预算不足省略
  整组结论；禁止跨任务或未来引用，报告实际遗漏数量，不提高条数预算。
  关联证据真实生成复验仍弃权：8 条证据、2359/297 token、5.031 秒、无候选，
  不宣称收益。随后新增生成器 v2 有界弃权诊断（模型自述、未验证），保存缺失
  证据类别但不自动补采/重试；新提示词尚未真实验收，完整项继续不勾选。
  后续诊断真实验收未通过：响应 stop/JSON parsed，但 schema 校验失败，0 接受
  候选；2443/1565 token、19.236 秒。旧日志无法区分失败分支，已补固定阶段和
  候选位置记录，不保存响应正文、不放宽门禁；完整能力仍未验收。
  阶段诊断版单次请求成功返回有界弃权：2443/506 token、7.35 秒，0 候选。
  模型要求前后观察及反例，但输入已含三组零页数变化；该诊断不当客观缺失证明。
  不再对同源轨迹重复调用；需公开开发对照和原生候选执行，完整项仍不勾选。
  原生候选执行增补：native_worker 增加显式 development-only 候选文件/摘要及
  repository 输入，经已有适用性 gate 后作为不可信建议注入 Runtime；默认
  baseline 不变，不写资产、不授予权限。结果区分 activation 与实际 injected。
  16 项本机真实子进程/HTTP fixture 通过，涵盖弃权和交付物门禁；Harbor 转发、
  规范化事件及完整双 arm 实验仍缺，完整项不勾选。
  Harbor 转发增补：构造时冻结并校验候选文件，传输摘要随 worker 参数传递；
  执行记录保留源文件/传输/规范化候选身份与 repository，默认无候选路径不变。
  两版 Harbor 各 31 passed、9 skipped；真实 Docker 候选注入、规范化事件及
  完整双 arm 仍待验证，完整项不勾选。
  真实 Docker 候选通道验收：两版 Harbor 各 4 passed，普通/dev 用户下分别确认
  激活文本实际进入 fixture 模型请求、弃权文本未进入。修复宿主结果回收仍只接受
  baseline 的遗漏，新增身份与模式核验。无付费调用、不代表任务收益；规范化事件
  与完整双 arm 仍待完成，见 native-candidate-docker-20261019.json。
  规范化事件增补：Harbor 回收时生成与结果文件摘要绑定的 praxile-events.json，
  分离 CONTEXT_ACTIVATION / CONTEXT_INJECT，并验证实际注入标记及内容摘要；
  不改变旧 baseline 的转换。当前为回收时投影，非实时流，EventStore 导入与
  双 arm 编排未完成，完整项保持未勾选。
  EventStore 导入增补：eval import-native 经服务校验源文件摘要和重算事件一致性，
  再写入统一日志/SQLite，支持幂等导入、冲突拒绝和收据失败恢复。7 项隔离测试
  通过；真实冻结 compact repair 离线导入 41/重复0/重建41，0 模型调用。
  不认证来源、不导入官方评分或产物实体；完整双 arm 仍未完成。
  官方结果绑定增补：适配器把 native result 原始摘要写入 Harbor metadata；
  JobBench/WorkBuddy 导入器核对摘要、轨迹身份和重算事件，输出可关联 EventStore
  收据的 native_trace_binding。旧 Trial 明确 unbound，评分不可用不补零。
  本地一致性不是来源认证，真实完整 Trial 与双 arm 编排仍未完成，总项不勾选。
  双臂准备增补：native_pair 从一份基础 Harbor job 生成 baseline/candidate 配置及
  私有候选快照，固定单并发/单次尝试/零自动重试，记录配置摘要，拒绝覆盖已有
  实验。8 项测试通过，两个 Harbor 版本均通过配置解析；不启动模型，不声称
  外部环境已冻结。真实执行/恢复/官方评分编排仍未完成，总项保持未勾选。
  串行入口增补：native_pair_run --execute 校验准备文件后独占认领运行，使用
  官方 Job.create/run 顺序执行两臂；逐臂检查输入变化，异常/取消不启动下一臂，
  已有运行不自动恢复或重试。9 项 fixture 测试通过；真实双臂、资源强杀恢复与
  评分汇总仍缺，jobs_returned 不等于通过，总项保持未勾选。
  完整 Job Docker 复验：Harbor 0.22 下 active/abstain 两组共四个 Trial，2 项测试
  通过；原生完成、请求注入差异、代理撤销、fixture 评分及 trace 绑定均校验。
  首次 fixture bytes 错误导致 502 已记录并修复。零付费调用，不是官方 benchmark
  成绩；真实任务、0.18 完整 Job 与恢复仍缺，完整项不勾选。
  0.18 完整 Job 增补：上游 WorkBuddy Docker 插件 + WorkBuddyPraxileAgent 的
  active/abstain 双臂四 Trial 均完成，2 项通过；额外实测 setup 后非 root UID
  和 dev 默认身份。仍为 fixture 模型/评分，真实任务与官方 CompositeVerifier、
  image mount 和恢复未验收，不据此勾选完整 benchmark 支持。
  本地输入冻结增补：native_pair --freeze-local-inputs 生成 v2 plan，记录任务树
  内容/权限与 worker/dependency wheels，启动和每臂前复核，变更后停止后续臂。
  v1 保持兼容并标未冻结；清单不含文件正文/凭据。此为启动时检测，不是不可变
  挂载，远端模型/judge/镜像与运行时并发修改仍未解决，完整冻结项不勾选。
  运行后校验增补：每臂前后写入 input_checks；最后一臂返回后也复核任务树、
  wheels 和计划文件，变化或不可读取标记 confounded，保留 job_returned 事实。
  12 项执行入口测试覆盖末臂修改和计划删除。瞬时修改后恢复仍无法由摘要证明
  未发生，不视为不可变环境或因果实验验收。
  官方双臂汇总增补：native_pair_results 校验 v2 计划/执行记录/本地输入，按
  计划任务调用 JobBench/WorkBuddy 官方导入器，核对候选 digest 与 native 模式。
  缺 Trial 保留为未完成，未绑定评分保留展示但不进入均值；污染执行拒绝比较，
  不导出 rubric 正文、不授予晋升。当前为双官方格式 fixture 验证，真实官方任务
  全链路、多次重复实验统一汇总和来源认证仍待完成，完整项不勾选。
  共享评分依赖补漏：上游 CompositeVerifier 会从任务外加载 dataset.toml 和
  shared/verifier。启用该官方评分器的本地快照现覆盖 dataset.toml + 完整 shared/，
  缺失/符号链接拒绝；旧未覆盖计划必须重新准备。真实 Office 材料两次只读快照
  一致（任务19项、共享13项），无模型调用。宿主插件依赖/外部引用仍未完全冻结。
  官方评分器到汇总集成：未修改的 WorkBuddy CompositeVerifier + Office shared
  插件执行自建内容 checker，正确/错误交付两组双臂四 Trial 分别得到 1/0，
  官方导入、native 绑定和配对汇总通过（2 项 Docker 测试）。任务/模型仍为 fixture，
  不是官方数据集成绩或性能增益，LLM judge 禁用，真实任务和完整隔离恢复仍未完成。
  渲染环境增补：Harbor 配置 renderer 时，在 worker 解释器中限时预检 DOCX/XML/PDF
  依赖及可执行路径，缺依赖在模型调用前失败，不自动修改冻结镜像。0.18/0.22
  各 18 项适配器测试通过，588 项快速单元测试通过。这不是完整 capability profile
  或实际渲染验收，完整项仍未完成。
  真实 MiniMax 复验：额外 worker pypdf 依赖经用户授权并记录摘要后，两份 DOCX
  生成成功；公开一页契约实际渲染 checklist 为 5 页，拒绝 completed，最终
  needs_human（30 步上限）。后续修订输出解析失败，没有成功 revise_docx，不能
  勾选自主修复闭环。无官方复评分、无严格 A/B 或性能提升声明；证据摘要见
  `results/jobbench-public-contract-smoke-20261019.json`。
  修订失败可观测性增补：动作解码保留完整响应摘要/长度、reasoning 边界原因及
  JSON 解析错误偏移，修复提示明确“未执行，不应假设产物已改变”。不放宽解析
  或安全规则；旧失败只有截断预览，精确原因仍未知，自主修订总项继续未完成。
  单独产物修订实验（不是原始 benchmark）已用 MiniMax 成功执行三次摘要校验
  修订，5 页变为 4/3/2 页，memo 未变，但预算耗尽仍不满足一页。随后新增
  compact 固定版式；同内容离线渲染为一页并检查截图，606 项快速测试及
  8 项修订/版式资源测试通过。模型自主选择版式、内容保持及完整原任务验收仍缺，
  不把离线排版修正记为自主修复成功。见 `results/jobbench-seeded-repair-20261019.json`。
  compact 真实复验已确认模型自主选择版式并成功修订四次，但页数一直为 2，最终
  needs_human；无解析失败也没有满足一页要求，不能把接口接通当作任务成功。
  见 `results/jobbench-compact-repair-20261019.json`。另外宿主凭据代理新增不含正文
  的用量账本与 Harbor 落盘，缺失 token 和金额保持 unknown，不追溯补造旧实验用量。
- [ ] 浏览器具备 click/fill/navigation/download、页面状态断言、截图和错误记录；截图存在不能充当视觉质量通过。
- [ ] 任务状态机：queued/running/waiting_approval/paused/completed/failed/cancelled；子任务边界 checkpoint，恢复前重新确认环境与产物状态。
- [ ] 本地自检检查“产物存在、能打开、结构符合请求”；与官方评分器物理分离。
- [ ] 模型凭据与 connector 凭据通过最小权限代理使用；工具副作用需要可校验的授权范围、有效期和撤销状态。
  模型代理增量：已实现宿主 loopback HTTP 服务、固定模型/HTTPS 上游、随机临时 capability、TTL/撤销和调用/输出预留额度，真实 Key 不作为客户端参数。16 项单元测试、本机 HTTP/Docker 可达性测试通过；Harbor 已接入临时凭据文件传递、worker 读取后删除和 finally 撤销，0.22 普通/dev 容器均完成 fixture 调用。connector、工具副作用授权、真实 benchmark 和 Linux 原生宿主仍未验证，完整项不勾选。见 `MODEL_CREDENTIAL_BROKER.zh-CN.md`。
- [ ] Web Console 展示当前子任务、产物预览、实际操作、费用、权限请求、暂停/取消与恢复结果。

验收：长任务中断恢复不重复提交副作用；文件产物重新打开检查；前端任务同时保存功能检查与视觉证据；所有外部能力可被 doctor 明确识别为可用或不支持。

## 8. 阶段 R2：分层经验与证据驱动改进

### R2-A 子任务经验

增量进展：新增 SubtaskEpisode V1 与 subtask_begin/end runtime actions，刷新前后项目快照、稳定动作引用、产物引用、观察状态计数、模型声明及不确定性分离，沿用原轨迹/checkpoint。3 项专门测试通过（含实际 action loop）。未结束子任务标 interrupted。尚无完整失败原因归因、临时经验 TTL、分层消费与重规划，总项不勾选；详见 `SUBTASK_EPISODES.zh-CN.md`。

分层消费增补：原资产侧车元数据支持显式 experience_layer，现有正常运行的激活/表示路径增加 strategic/procedural/tool 独立预算及实际提示词/轨迹标签，强制控制规则不削弱，未标记旧资产兼容。SQL 分层过滤、TTL/状态/工具版本的后续实现与限制见下方各项；尚无自动分层提案，不勾选完整子任务经验能力。

- [x] `SubtaskEpisode` 记录目标、状态前后摘要、关键 event IDs、产物、结果、失败原因与不确定性。
  已记录目标/前后项目快照、稳定 action refs 与 V2 event 映射、产物、观察状态和模型结果声明；新增失败观察摘要/退出码及独立归因状态，证据不足 cause=null、abstained。字段沿 checkpoint/trace 往返保留。30 项子任务/反思/兼容测试通过，含非字典观察数据保护；根因自动发现和完整外部状态建模不属于已验证能力。
- [x] 经验按战略/程序/工具使用分层；沿用既有资产存储，增加类型和索引，不复制第二套长期 memory。
  显式 experience_layer 已进入 assets 可空列与 SQL 索引，四条检索路径支持 layers 过滤；原 runtime 分层预算/语义门控保留，不新建 memory。旧库原地迁移保留使用计数，显式 index 填充缓存；元数据更新校验层名。16 项分层索引/运行时测试及 7 项既有检索回归通过。此项完成不代表模型可自动准确分类经验，自动分层提案另待实现。
- [x] 子任务反思可建议重规划；本次任务内的临时经验带 TTL、来源和状态约束，不绕过安全或架构门禁。
  subtask_note/subtask_reflect 已接入 action loop：显式调用 experience_reflection 角色，引用当前任务已关闭子任务的动作证据，支持 abstain、1..20 动作 TTL、状态约束和成本记录；账本沿用 trajectory/checkpoint，只按请求注入，不直接写长期资产。14 项专门测试通过，含策略拒绝时不创建笔记/不调用反思模型、过期、来源校验与序列化恢复；23 项跨模块资源回归通过。
  勾选范围是原文要求的“建议”能力，不包含自动改写 plan 或直接执行重规划；没有声称真实模型建议质量或性能提升已验收，后者由真实任务实验另行证明。
- [ ] 激活条件包含任务语义、工具/应用版本、状态前置条件及 anti-scope；未知即 abstain。
  初次激活已增加基于控制面观测的 activation_requirements（精确工具版本与状态点路径），未知/不符/非法时 abstain，保留原语义与 anti-scope 门控及强制规则。条件随选中经验冻结到 checkpoint，恢复前重新观测，失效时拒绝恢复。运行中每次模型请求前也重新观测，失效则 checkpoint + needs_human，避免继续使用旧消息中的经验。10 项路由资源测试、13 项纯条件测试及 2 项中途失效测试通过。外部应用状态、浏览器及跨进程版本观测仍未覆盖；此机制也不是原子权限检查，完整项保持未勾选。
- [ ] 用户偏好走独立 namespace 和用户确认，能力改善与个性化分别统计。

浏览器观测增补：runtime 统一观测入口可读取已有会话存活、去敏 origin 和 Chromium 版本，不自动启动浏览器。22 项针对性测试通过；真实 Chromium 断言已添加，本轮因本地端口沙箱权限及审批服务容量失败未执行，不算验收完成。尚不覆盖 DOM/业务状态、跨进程观测和子任务中途重新选择。

后续 DOM 观测增补：控制面可配置 origin 范围内的 count/visible 探针，返回类型化状态
及定义摘要，配置对象运行中变更不替换已冻结定义。跨域、歧义、异常及期间导航保留
unknown，已有条件门控在下次模型请求前停止失效上下文。13 项纯探针测试、运行时
条件失效/未知回归及 3 项真实 Chromium 测试通过；不宣称 DOM 原子性、跨进程状态
或视觉质量判断，完整激活项保持未勾选。详见 BROWSER_SESSIONS.zh-CN.md。

### R2-B 改进实验记录

受控 Context A/B 已增加逐任务 ImprovementEpisode V1，保留冻结预测、候选 digest、配对 trace、观察差异和混杂因素；verdict 限定单对任务结果，不能推断因果，默认 NO_ENTRY、不授予 promotion。缺失数据保留未知。已接实际 A/B analyze/report 持久化，修复分类 progress 误用全任务汇总。可执行 diff、独立行为 verifier、语义对齐及中性 benchmark 接入尚未完成，总项不勾选。详见 `IMPROVEMENT_EPISODES.zh-CN.md`。

- [ ] `ImprovementEpisode` 记录基线、候选、组件 diff、行为预测、probe、观察差异、混杂因素与后续决定。
  组件证据增补：Context A/B 从冻结 manifest 生成精确 /context JSON Patch 及
  前后内容摘要，随 episode 持久化并支持重新分析；不导出其他 settings。
  这是配置差异，不是可执行脚本/hook diff，也不证明实际表示或行为收益；
  独立 probe 生命周期和跨组件支持未完成，完整项仍不勾选。
- [ ] 每项修改 verdict 支持 helped/hurt/no_observed_effect/confounded；区分行为改善与最终任务成功。
  增补冻结的重复操作测量：从 control-plane 操作事件引用复算 search/read_file/
  run_command 同参数重复次数，生成独立 behavior_probe 与行为方向 verdict。
  任务结果 verdict 不随成本/次数改善改变，缺覆盖/暴露/不变量证据则弃权或
  confounded；638 项快速单元测试通过。没有机制因果、真实模型质量或晋升声明，
  其他行为预测与中性 benchmark 支持仍缺。详见 `IMPROVEMENT_EPISODES.zh-CN.md`。
- [ ] 不同轨迹按子任务/语义事件对齐，不能把不同执行的第 N 步当作同一状态。
  已接入保守操作身份对齐：唯一且参数完整的 read_file/search/run_command 按摘要建立事件引用，乱序不影响配对，重复/截断保留未对齐；不使用 step 序号。状态等价性仍 unknown，不做因果判断。4 项专门测试通过；完整子任务语义状态对齐未完成。
  原生 SubtaskEpisode 现可通过 V1/AgentEvent 转换保留：追加回顾性 SUBTASK_EPISODE，不改写已有事件 ID；保存动作到事件映射与 unresolved refs，反向投影恢复引用。19 项 trace/兼容性测试通过。跨执行主体的子任务语义等价判定仍未完成。
  子任务对齐增补：A/B 比较与 ImprovementEpisode 增加唯一、已关闭、精确 goal
  摘要配对，保存 episode/event/action refs 和前后状态摘要；悬空动作引用拒绝，
  重复目标不按步骤强配。相同快照仅等于记录字段相同，不证明环境/语义等价。
  6 项专门测试和旧 A/B 回归通过；完整语义对齐仍未完成。
  原生文档读取对齐增补：行为比较入口按请求参数、源内容版本和返回结构摘要
  唯一匹配成功读取，不按步骤序号配对；重复身份、不同版本与选择保持未对齐。
  三对真实开发运行离线复算为 0/1/1 对读取，保留事件引用和原文件摘要。
  这不证明完整环境/抽取器/语义等价，也不验证后续机制收益；见
  [状态对齐证据](results/docx-read-alignment-20261021.json)。完整语义对齐仍未完成。
- [ ] 被拒候选仍保留可用的局部证据；新经验必须引用具体差异，允许 NO_ENTRY。
- [ ] 不确定的机制归因触发单组件消融/重复试验，不能靠更高 LLM confidence 消除混杂。

### R2-C 自动候选与执行组件

- [ ] 在开发任务内实现 Propose -> Probe -> Compose -> Re-run；从两个有明确假设的候选起步，不强制每个普通任务都进行多次运行。
  新增 DevelopmentContextCycle Python API：显式开发/held-out 名单、两个有假设的
  候选及预先提供的组合候选被冻结，三个版本分别执行独立隔离 A/B；可断点恢复，
  变更计划拒绝恢复。开发轨道允许本任务来源证据，clean 轨道继续禁止重叠。
  CLI `eval ab` 已增加 second-candidate/composed-candidate 和显式开发/held-out
  名单参数，要求 development-only，拒绝混用 held-out capability/proxy 协议；
  fixture 验证真实 CLI 调度、三组独立 A/B 与报告落盘。
  草案生成增补：proposal_composer 单次有界调用可从显式公开开发观察生成两个
  候选及组合草案，引用/范围校验、弃权、截断拒绝及用量记录已接入，fixture
  串联生成到隔离实验。新增 development 实验的 EventStore 证据投影：校验
  manifest/report/任务归属，仅提取 search/read_file/run_command 的结构化
  状态、参数摘要和事件引用，不发送原始输出或 evaluator 内容；可直接送入
  designer，无可用事件则不调用模型。fixture 覆盖正常来源与拒绝错配来源。
  CLI design-context 已接此证据入口：冻结输入/范围/去敏角色配置，保存生成
  轨迹与候选文件，不自动执行；恢复重建缺失候选而不重复已保存的模型生成。
  fixture 验证 CLI 生成、文件恢复及变更输入拒绝，真实模型质量仍未验收。
  真实生成验收（2026-10-19）：MiniMax-M3 从四条公开页数检查生成草案，首次
  RobustJSONError；补共享 reasoning/JSON 解码与摘要诊断后复验生成两个候选
  和组合。合计两次请求，1680 input / 3742 output tokens，无 rubric/正文发送。
  审查发现任意页边距、未实现估页和无证据达标保证等能力错配，未执行/未晋升。
  下一步需执行器 capability 约束与独立内容保持验证，不能据此勾选质量验收。
  证据见 results/jobbench-candidate-design-20261019.json。
  能力约束增补：design-context 可传原生 executor action profile，提示词含实际
  动作字段和固定 DOCX 约束，三个草案均须给出通过注册表校验的动作样例；
  候选携带 profile 摘要，A/B 拒绝执行器不匹配并复核。未声称正文语义与样例
  一致或工具已可用。后组合增补：同摘要父 profile 可组合，重新验证组合样例；
  混合/错配/伪造 profile 拒绝，cycle 也复核自定义回调，不能丢约束或取并集。
  单元与默认隔离链路回归不等于原生能力组合实际执行验收，真实复验未完成。
  profile 真实复验（2026-10-19）：MiniMax 单次 1297 input / 4000 output，
  52.284 秒，仅推理块、无最终 JSON，length 截断被拒绝，未生成/执行候选。
  新增角色级受限 request_options 透传与校验，默认不启用、不覆盖预算；
  首次仅配置/传输测试通过，证据见 results/jobbench-candidate-profile-20261019.json。
  后续同预算单次显式 thinking disabled 请求正常返回 NO_ENTRY：1284 input /
  12 output，1.591 秒。没有候选、执行或晋升，不宣称质量或性能提升；证据见
  results/jobbench-candidate-direct-20261019.json。有效草案与实际收益仍未验收，
  完整项继续不勾选，不通过强制产出绕过弃权。
  文档证据接入增补：开发轨迹采集器支持 check_document_layout，校验调用/结果
  路径与上限一致、页数及状态自洽，投影结构化事实而非文档正文/评分材料。
  无上限保留 unknown，视觉/语义质量不推断。正式 collector 测试通过不等于
  原生文档 A/B 或真实收益验证，完整项仍不勾选。
  两阶段组合增补：Python API 支持两组开发 probe 后调用组合器，模型输入只
  含父候选与白名单任务 verdict；组合/弃权先冻结，恢复不重复生成，组合独立
  重跑。fixture 验证执行、恢复和弃权；CLI 已接 compose-after-probes 与
  proposal_composer 配置，记录独立用量、冻结去敏配置/范围摘要并关闭 transport。
  语义失败归因、所有
  适配器 payload、独立来源认证和真实模型验收未完成，完整项不勾选。
- [ ] 支持过程型与领域型候选；KEEP_BASE 是正常结果，组合版本重新验证，不能继承单个候选的通过状态。
  工作流已按组合自己的客观任务提升、无回归/无混杂证据给出任务内试用建议，
  否则 KEEP_BASE；不因单项通过继承成绩，不自动 adopted/promoted，不改生产资产。
- [ ] 在现有表示路由之后增加组件投影：上下文、skill、script、subagent、受限 hook；不支持的执行器能力明确拒绝。
- [ ] 可执行资产携带依赖、触发条件、输入输出、测试、权限、来源和版本；artifact bundle 完整性在运行前校验。
- [ ] hook 只能作用于允许的 runtime 扩展点，不能修改凭据代理、最终 evaluator 或审批服务。
- [ ] task-local adopted 与长期 promoted 分开；候选只在实验沙箱试用，生产更新沿用人工审批。

验收：从真实失败轨迹生成并运行候选、形成可核验的行为对比、组合再执行。无改进或无证据时可正常结束，不通过强制“生成经验”制造进化假象。

### R2 真实开发执行证据（2026-10-20）

MiniMax-M3.1-Flash / reasoning_effort=low 已完成三个冻结候选各自的新 baseline
与 candidate，共六次隔离执行，实际激活/注入记录与模型用量已核对。仅一个
seeded DOCX 开发修订任务，不是六个独立任务或官方 JobBench/held-out 成绩。
候选均从 5 页压到 1 页；baseline 页数为 1/1/5，其中一组因动作 schema 错误
停止。memo 校验均未改变，容器清理已观察确认。正文保持仍未独立验证，事后
检查出现潜在合规项遗漏，不以省 token 或页数达标晋升。完整验收项继续不勾选。
记录与限制见 [六次执行证据](results/docx-six-arm-20261020.md)。

2026-10-21 增补显式内容保持评审 API：独立角色调用、逐项原文/产物引文校验、
完整要求覆盖检查、未知/非法响应弃权、用量记录。引文存在不等于语义正确，
LLM 判断与环境验证继续分开，不能自动晋升。测试仅覆盖接口行为；真实文档
评审和校准仍待验收，详见 [内容保持评审](CONTENT_PRESERVATION_REVIEW.zh-CN.md)。
同日真实复验：六份产物哈希已绑定，固定十六项源文要求后独立调用 MiniMax；
两份逐项评审通过本地引用校验、四份请求阶段弃权。无完整配对，不能排名；
已知用量不冒充完整成本。临时脚本在保存全部记录后清理报错，已修正但未重跑。
增补安全错误分类与响应诊断，不追溯猜测旧失败原因。见
[事后评审证据](results/docx-content-review-20261021.md)，完整项仍不勾选。
诊断增补：受限异常链投影保留最近传输调用的 HTTP 状态和尝试次数，不记录
敏感正文，也不声称覆盖所有 fallback。独立单次探测收到 stop 响应但本地校验
失败，未替代旧结果；旧四次请求失败仍不能归因。真实评审质量验收继续未完成。
评审协议增补：显式 function submission 复用既有 provider 通道，默认文本模式
兼容；schema 不代替本地引文和覆盖校验。真实单次试验收到 tool_calls，但条目
校验失败，保留弃权；错误码随后细分，未伪称旧响应具体原因已知。参见
[协议试验记录](results/docx-review-function-20261021.json)，未晋升。
检查点增补：显式私有响应回调支持不重复调用模型的离线诊断；保存失败则弃权。
真实单次留档发现仅返回 6/16 项且最后一项缺 reason，输出恰为 3000 token，
但结束标记仍为 tool_calls。已增加覆盖计数、预算触顶信号和回归测试，不推断
旧响应原因。下一步需固定总预算的分组评审，未以扩大预算或补写条目绕过校验。
分组 API 已实现：预校验全部要求、确定性分组与额度、失败即停、不转移余额、
完整覆盖才汇总。真实验收另冻结 8000 总输出额度（与旧预算不作效率对比）：
前两组 8 项有效，第三组引文拼接/不精确而弃权，第四组未调用；保留未知项和
全部用量。见 [分组证据](results/docx-review-batch-20261021.json)。完整内容质量
尚未验收，下一步需来源绑定的多片段引用，不能靠模糊匹配放宽事实约束。
多片段引用已增补：显式 units 模式由控制面无损分段、绑定快照/偏移并还原
引用，拒绝未知/重复/跨快照 ID，旧 quote 模式保留。真实 4 组验收前三组
12 项引用校验通过，第四组 provider 响应处理失败，整体弃权且完整用量未知。
见 [片段引用记录](results/docx-review-units-20261021.json)；引用真实性不等于
语义判断正确，完整可靠性与校准仍未完成。
无效 submission 用量增补：provider 新增 ModelError 兼容错误类型，保留固定
错误码、结束标记与安全 token 字段；reviewer 按失败调用记账，非法别名保持未知。
真实单次同输入探测明确收到 length + arguments_invalid，输出触顶 2000；不拼接
回旧批次，不追溯推断旧失败。见 [响应诊断](results/docx-review-envelope-20261021.json)。
显式预算计划增补：每组额度预校验、复制冻结、总和严格等于总预算；批次用量
账本排除历史调用，缺用量失败保持 unknown。开发调优冻结 [1800,1400,1800,3000]
后真实四组 16 项全部完成引用/覆盖校验，LLM 判 12 保留/3 缺失/1 不确定。
这仅验证完整评审流程，不等于产物通过、判断已校准或 held-out 收益；无晋升。
见 [完整开发评审记录](results/docx-review-allocation-20261021.json)，完整项仍不勾选。
内容 judge 校准增补：独立调用方参考标签仅在控制面比较，不发送标签或 case ID；
统计弃权、覆盖率、全体/条件准确率和错误保留率，无概率不生成 ECE。四个合成
开发冒烟样例（释义、遗漏、冲突、嵌入指令）真实 MiniMax 结果 4/4 与参考匹配、
0 弃权。它不是人工认证/held-out 校准集，不证明复合文档或广泛抗注入能力，
也不构成晋升依据；见 [开发校准记录](results/content-calibration-smoke-20261021.json)。
同条件比较器已增补：校验计划摘要、双方条件/声明 judge 身份、组与合并结果
一致性；缺一侧即 inconclusive，不按部分结果判胜。真实新配对两侧未完成：
baseline 第四组超时，candidate 第二组 length/参数无效，未重试或替换旧成绩。
见 [配对记录](results/docx-content-pair-20261021.json)。说明单次完成不等于可靠性
已验收；R2/R3 内容质量、稳定完整对照及泛化验证继续未完成。
评审产品化增补：新增 `python -m praxile.eval.content_review_run`，显式 judge 角色、
输入与预算快照、0700 运行目录、轨迹/报告持久化及重复 ID 拒绝。私有模式限定
loopback，云端公开材料需 --public；完整响应默认不存。fixture 验证持久化、
失败记账、隐私拒绝和不自动重放，CLI help 已核验。本轮未新增真实模型实验，
不以入口可用代替内容评审稳定性验收。
离线比较入口增补：`python -m praxile.eval.content_review_files` 从新持久化目录
绑定 manifest/输入/计划/报告/轨迹与引用正文，无模型调用；不一致或不完整拒绝
完整比较。fixture 覆盖改写文件、伪造片段、符号链接和 CLI 读回。不把本地
自洽当独立来源认证，不迁移或改写旧真实实验。真实完整对照稳定性仍待验证。

行为对比增补：`python -m praxile.eval.native_behavior` 支持单轨迹和双轨迹离线
投影，保留所有可识别事件顺序、操作状态计数、内容版本链接及覆盖缺口；不再
用候选生成的有界样本代表完整行为。六份旧真实执行逐一重算并绑定原 SHA256，
可观察到表格/bullet 选择、修改和检查次数差异，不按第 N 步配对、不按少调用
判胜。见[行为对比证据](results/docx-behavior-20261021.md)。未知事件、完整语义
对齐、内容质量和机制因果验证仍未解决，R2 总项继续不勾选。

## 9. 阶段 R3：证明竞争力，而非只完成接口

- [ ] 冻结模型、executor、环境、数据修订、judge、预算、任务顺序和记忆快照；保存可公开复现的 manifest。
- [ ] 比较 frozen executor、等预算重复执行、仅经验、仅任务内改进、完整 Praxile 五组，必要时另做同条件 Harness² 参考组。
- [ ] 同时报告官方分数、交付率、回归率、完整 token/费用/延迟、经验库体积与审批负担。成本包括 proposer、probe、composer、反思和 judge。
  Eval 上游完整性增补：规范化 MODEL_CALL 缺 TokenUsage 时保留已知小计，但
  tokens_total=null，并传播 tokens_complete/覆盖计数到聚合与 A/B；不再把
  缺调用用量的 trace 当完整零成本证据。旧适配器已填零字段和未记录请求仍
  无法还原，独立 judge 与全 cycle 成本账本仍未收口，总项继续不勾选。
  cycle 局部账本增补：报告现在集中列出执行 arms 与组合器已知用量、小计及
  覆盖情况，缺价格不补零；全实验总额继续 unknown，上游设计和独立 judge
  仍未合并。fixture 验证预先组合六个 arm、弃权四个 arm 加组合器的记录范围。
  轨迹用量完整性增补：`cost.usage_evidence` 区分已知 token 小计与完整总量，
  按 purpose 记录覆盖率；字段缺失、类型非法、别名冲突和旧轨迹缺失调用均不补零。
  旧 `prompt_tokens/completion_tokens` 字段仅保留已知小计兼容语义。该证据仅覆盖
  TrajectoryLogger 记录的调用，未记录的失败请求、独立 judge 和离线演化不能据此
  声称已纳入总成本；金额保持 null，完整账本总项仍不勾选。
  Harbor 导出已复用该完整性计算并保留 metadata：调用数与记录不一致时拒绝
  输出完整 token 总量，旧轨迹注明 recorded_calls_only，不信任缓存摘要。
  A/B 汇总修复：缺失/非法 token、工具次数、延迟、费用及进展计数不再补零；
  缺一侧则差值 unknown，缺一任务则总量 unknown，并报告完整配对覆盖率。
  效率证据不足时不宣称节省，客观任务结果单独保留。完整全角色成本及中性
  benchmark 汇总仍未完成，本项不勾选。
  费用完整性增补：trace/aggregate 区分已报告小计和完整总费用，缺价格保留 null；Lab 与普通 A/B 拒绝比较未完整定价的 arm，ignore_errors 不能当零成本。未进入执行 trace 的官方 judge/离线演化调用仍需统一账本接入，本项不勾选。
- [ ] 以 task 为统计单位做 paired comparison；重复运行嵌套于 task，不能把同一任务的 repetitions 伪装成独立任务扩大显著性。
  Harness Lab 已修复 repetition 展平问题：先任务内均值再任务级配对，独立样本数为完整任务数，缺失配对整任务标不完整；使用保守 Hoeffding 区间并声明独立任务假设。旧统计报告不能通过质量晋升门禁。9 项测试通过。中性 benchmark 连续评分、任务级成本区间和相关任务簇设计尚未完成，完整项未勾选，详见 `TASK_CLUSTER_STATISTICS.zh-CN.md`。
  连续评分增补：`python -m praxile.eval.compare_imports` 可按显式 repetition 计划比较官方 JobBench/WorkBuddy 导入，按任务归一化且保留分母、checksum、输入摘要及缺失评分，不混合 benchmark，不自动晋升。6 项测试通过。尚不能核验完整实验执行配置，也未完成真实多任务验收，详见 `IMPORTED_BENCHMARK_COMPARISON.zh-CN.md`。
- [ ] held-out 下使用冻结经验；另设明确标识的 online-stream 实验，记录顺序，禁止把两种协议合并报分。
- [ ] 做跨任务/跨领域/跨执行模型迁移、反例与旧能力保留测试；按 executor + task family 分别晋升。
- [ ] 公开失败、abstain、无输出、评分失败和负迁移案例；CI 检查、fixture 与真实任务证据分别标注。
- [ ] 在实验收益稳定后才引入自适应探测预算，依据边际信息/成本决定是否继续，保留固定预算对照。

晋升阈值须在看 held-out 结果前确定。成功标准是完整协议和诚实结论；负结果也可完成研究验收，但不得发布为性能改善。

## 10. 建议顺序、迁移与回滚

关键路径：R0-A -> R0-B Code + R0-C -> R0-B Office/Web/Security；R1 的必要交付工具跟随对应子集实现；随后 R2，最后 R3 完整实验。

首个 PR 建议只做通用 task/environment/result 契约、兼容桥接和契约测试。第二个 PR 做 Harbor 生命周期与 WorkBuddy Code；第三个做 JobBench 及多文件产物。每个 PR 都必须有运行证据，不能连续只增加 schema 占位。

迁移采用新增版本与显式转换，保留旧 manifest 原文/digest；新 capability 使用显式配置开启，原有 mini-SWE 实验作为回归路径。回滚通过恢复活动 policy/asset pointer 与执行配置完成，历史 trace 和官方结果不覆盖。文件工作区不伪装成空 Git 仓库。

仍需实施时核实：各 benchmark 固定版本的 API 与镜像架构、可获得的 judge 服务、各模型对工具/图像的实际支持、数据更新后的任务数量与 split。任何变化均更新 manifest 与支持矩阵，不悄悄降低评测范围。

## 11. 本轮交付核对

- [x] 区分学术 MUSE、Meta Muse 与 Harness²，并列明来源。
- [x] 对照当前 task、runner、adapter、lab、reflect、browser 代码确定缺口。
- [x] 覆盖 WorkBuddy 全部当前子集及 JobBench main/easy 的接入路径。
- [x] 给出阶段依赖、接口边界、验收证据、迁移/回滚与实验协议。
- [x] 将 Praxile 的竞争力落实为证据链、适用性控制、独立验证和受控晋升。

以上勾选仅表示本路线图已完成的研究与设计；R0-R3 的实现 checklist 保持未勾选。
