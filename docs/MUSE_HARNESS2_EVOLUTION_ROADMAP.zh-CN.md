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
- [ ] 旧 SWE-bench task 经显式兼容映射继续运行；旧 manifest 的 digest 和 resume 语义不变。
- [ ] doctor 输出缺失依赖、容器架构、磁盘、执行模型和评审模型状态；Linux 环境与 Mac Docker Desktop 分别给出实测支持矩阵。

代码落点：在 `eval` 下增加通用协议与插件，不先大改 legacy runtime；通过桥接保留现有 `eval/v2`。benchmark 可选依赖使用独立环境，避免上游 Python/Harbor 版本拖动基础安装。

验收：同一契约测试覆盖非 Git 输入、只读输入、取消/超时/崩溃、清理、resume 幂等；旧 mini-SWE 路径无回归。

R0-A 第一批实现（2026-10-19）：`praxile/eval/contracts.py` 提供通用任务、环境描述、输入/输出与评分记录；执行视图使用白名单投影，旧 SWE-bench 任务可显式转换且原始序列化不变。`test_benchmark_contracts.py` 与原有 `test_eval_v2_schema.py` 合计 25 项通过。当前验证的是契约、私有字段不泄露、路径拒绝和序列化兼容；实际环境租约、文件只读挂载、运行恢复和真实 benchmark 验收仍未完成。

R0-A 第二批：`praxile/eval/lease.py` 增加本地工作区租约，支持执行、产物读取、取消、超时进程组回收与幂等释放；仅对显式传入的 release 回调委托环境销毁，默认不删除工作区。资源测试通过 `--forked` 隔离运行。该后端不是安全沙箱，目前仅支持 POSIX；reset 明确要求通过 preparer 创建新租约，尚未接入正式 runner。Harbor 管理容器、只读输入挂载、持久化恢复及两个 benchmark 的真实执行均仍未完成，因此环境协议总项不勾选。上游依赖需分别隔离：WorkBuddy 固定 Harbor 0.18.0，JobBench 固定 0.22.0。

### R0-B WorkBuddy Adapter

阶段进展（2026-10-19）：新增受摘要与资源限制保护的数据包解包、WorkBuddy 清单冻结及只修改新副本的官方准备流程。真实 Code 80 条、Office 50 条材料已冻结；Office 50 条已在副本绑定官方评分器和 dev 用户，19 项相关测试通过。原生执行器增加固定的非 root 初始化。仍缺真实任务执行、结果导入、Web/Security 环境与恢复验收，下列总项不勾选；具体摘要与命令见 `HARBOR_NATIVE_EXECUTOR.zh-CN.md`。

后续进展：新增固定 Harbor 0.18.0 的 job 生成，显式任务选择、单并发、零自动重试、结束清理；Security 在专用网络策略验收前拒绝执行。发现 task TOML 的 verifier import_path 被 Harbor 忽略，已改为 job 层显式绑定，工厂测试确认 CompositeVerifier。31 项相关测试通过，50 条真实 Office 材料通过 Harbor 配置解析；未开始真实模型任务评分，总项继续保持未完成。

异常路径增补：原生执行器记录独立 execution status，超时/取消/非零退出后限时回收已存在的结果，损坏下载不发布，保留主异常。尚未实现容器强杀后的完整 checkpoint 恢复，因此不据此勾选恢复验收。

阶段实现记录（2026-10-19）：新增 `praxile.adapters.harbor_native:PraxileAgent` 和 `praxile.eval.native_worker`，固定 wheel 双端 SHA-256 校验、容器离线安装、调用真实 AgentRuntime、原始轨迹下载和 token 统计回填已实现。Harbor 0.18.0 / 0.22.0 分别在独立环境通过真实 Docker + 模拟模型测试，各 2 项；基础回归 34 项通过。复现与边界见 `HARBOR_NATIVE_EXECUTOR.zh-CN.md`。这是执行通道验收，不是官方 benchmark 成绩；任务发现、正式评分、私有材料隔离、断点恢复和真实任务验收仍未完成，以下总项保持未勾选。

官方 [WorkBuddy-Bench](https://github.com/Tencent/workbuddy-bench) 当前基于 Harbor/Docker，含 Code/Web/Office/Security 子集。实现时固定仓库提交、数据修订、镜像摘要和评分配置，不能把 Harness² 的论文子集当成官方全量任务集。

- [ ] 接入官方任务发现、执行和评分，先 Code，随后 Office/Web，最后 Security；完整兼容发布必须报告四类覆盖情况。
- [ ] 支持 Praxile Native 作为真实执行主体，规范化 Harbor trial identity、原始轨迹、文件产物和评分结果。
- [ ] 保留官方复合评分配置及各项结果；LLM/VLM 评分启用情况和模型身份进入报告。
- [ ] Security 使用独立受限网络策略与一次性沙箱，避免沿用日常项目权限。
- [ ] 公布每类至少一个真实任务的完整执行证据；小样本用于集成验收，不用于增益声明。

### R0-C JobBench Adapter

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

这阶段吸收 Meta Muse 的长期任务体验与 MUSE 的子任务组织方式；R0 使用的基础工具在 R0 一并实现，R1 负责系统化增强。

- [ ] Deliverable Contract：目标路径、格式、必要结构、允许的状态变化、证据位置；来自用户任务，不能从隐藏 rubric 推导。
- [ ] 按实际任务依赖建立版本化 capability profiles：表格读写/公式重算、文档/PDF抽取与渲染、网页浏览器操作、代码与脚本执行。
- [ ] 浏览器具备 click/fill/navigation/download、页面状态断言、截图和错误记录；截图存在不能充当视觉质量通过。
- [ ] 任务状态机：queued/running/waiting_approval/paused/completed/failed/cancelled；子任务边界 checkpoint，恢复前重新确认环境与产物状态。
- [ ] 本地自检检查“产物存在、能打开、结构符合请求”；与官方评分器物理分离。
- [ ] 模型凭据与 connector 凭据通过最小权限代理使用；工具副作用需要可校验的授权范围、有效期和撤销状态。
- [ ] Web Console 展示当前子任务、产物预览、实际操作、费用、权限请求、暂停/取消与恢复结果。

验收：长任务中断恢复不重复提交副作用；文件产物重新打开检查；前端任务同时保存功能检查与视觉证据；所有外部能力可被 doctor 明确识别为可用或不支持。

## 8. 阶段 R2：分层经验与证据驱动改进

### R2-A 子任务经验

- [ ] `SubtaskEpisode` 记录目标、状态前后摘要、关键 event IDs、产物、结果、失败原因与不确定性。
- [ ] 经验按战略/程序/工具使用分层；沿用既有资产存储，增加类型和索引，不复制第二套长期 memory。
- [ ] 子任务反思可建议重规划；本次任务内的临时经验带 TTL、来源和状态约束，不绕过安全或架构门禁。
- [ ] 激活条件包含任务语义、工具/应用版本、状态前置条件及 anti-scope；未知即 abstain。
- [ ] 用户偏好走独立 namespace 和用户确认，能力改善与个性化分别统计。

### R2-B 改进实验记录

- [ ] `ImprovementEpisode` 记录基线、候选、组件 diff、行为预测、probe、观察差异、混杂因素与后续决定。
- [ ] 每项修改 verdict 支持 helped/hurt/no_observed_effect/confounded；区分行为改善与最终任务成功。
- [ ] 不同轨迹按子任务/语义事件对齐，不能把不同执行的第 N 步当作同一状态。
- [ ] 被拒候选仍保留可用的局部证据；新经验必须引用具体差异，允许 NO_ENTRY。
- [ ] 不确定的机制归因触发单组件消融/重复试验，不能靠更高 LLM confidence 消除混杂。

### R2-C 自动候选与执行组件

- [ ] 在开发任务内实现 Propose -> Probe -> Compose -> Re-run；从两个有明确假设的候选起步，不强制每个普通任务都进行多次运行。
- [ ] 支持过程型与领域型候选；KEEP_BASE 是正常结果，组合版本重新验证，不能继承单个候选的通过状态。
- [ ] 在现有表示路由之后增加组件投影：上下文、skill、script、subagent、受限 hook；不支持的执行器能力明确拒绝。
- [ ] 可执行资产携带依赖、触发条件、输入输出、测试、权限、来源和版本；artifact bundle 完整性在运行前校验。
- [ ] hook 只能作用于允许的 runtime 扩展点，不能修改凭据代理、最终 evaluator 或审批服务。
- [ ] task-local adopted 与长期 promoted 分开；候选只在实验沙箱试用，生产更新沿用人工审批。

验收：从真实失败轨迹生成并运行候选、形成可核验的行为对比、组合再执行。无改进或无证据时可正常结束，不通过强制“生成经验”制造进化假象。

## 9. 阶段 R3：证明竞争力，而非只完成接口

- [ ] 冻结模型、executor、环境、数据修订、judge、预算、任务顺序和记忆快照；保存可公开复现的 manifest。
- [ ] 比较 frozen executor、等预算重复执行、仅经验、仅任务内改进、完整 Praxile 五组，必要时另做同条件 Harness² 参考组。
- [ ] 同时报告官方分数、交付率、回归率、完整 token/费用/延迟、经验库体积与审批负担。成本包括 proposer、probe、composer、反思和 judge。
- [ ] 以 task 为统计单位做 paired comparison；重复运行嵌套于 task，不能把同一任务的 repetitions 伪装成独立任务扩大显著性。
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
