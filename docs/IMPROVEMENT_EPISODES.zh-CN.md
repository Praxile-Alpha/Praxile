# 配对实验解释记录

## 开发任务组合实验

`praxile.eval.v2.development_cycle.DevelopmentContextCycle(state_root, event_store)`
提供显式 opt-in Python API。`run(task_set, ...)` 接收 `candidates=[first, second]`、
`composed_candidate`、`development_task_ids`、`heldout_task_ids`、`cycle_id`，以及
原 A/B 的 adapter/evaluator/baseline_policy/model/source_overrides 参数。
三条候选均须有 `expected_effect.hypothesis`；组合候选必须使用独立 ID。

预先组合模式下所有候选在开始执行前冻结，不根据 held-out 反馈组合。工作流执行三组独立 A/B，
也就是六个 arm，而不是复用上一组 baseline；调用方必须按该成本配置每个 arm 的
预算。产物位于 `eval/v2/development_cycles/<cycle_id>/`，包含不可变 manifest、
progress 和 report；三个实际实验仍存入现有 experiments/runs/trace 目录。
`resume=True` 恢复已存在子实验，计划变化拒绝恢复；失败不跳过组合验证。

开发任务名单必须与 task set 精确一致且与 held-out 名单无交集；候选来源不能
包含声明的 held-out 任务。开发轨道允许引用本任务证据，clean 轨道限制不变。
这些名单由调用方声明，不能替代可信 benchmark split 与物理信息隔离。

只有组合本身的逐任务结果存在客观提升、无回归和混杂、执行不变量通过时，
返回 `TASK_LOCAL_TRIAL_RECOMMENDED`；其他情况返回 `KEEP_BASE`。单项结果再好
也不会替组合授予资格。始终 `adopted=false`、`promotion_eligible=false`、
`experience_entry=NO_ENTRY`，不改生产策略或长期资产。真实模型质量、
可迁移收益和完整 R2-C 验收均未完成。

### 模型草案生成

`candidate_designer.design_development_candidates(...)` 使用现有 router 的
`proposal_composer` 角色，接收显式 public 开发观察（每条仅 task_id/event_id/
observation）、开发/held-out ID 名单、控制面给定的 repositories/task_signals/
does_not_apply_when 范围，以及 TrajectoryLogger。单次有界请求生成两个干预候选
和一个组合草案，或正常返回 NO_ENTRY。输入摘要、状态和模型用量记入传入轨迹；
输出可经 ContextCandidate.from_dict 直接送入 DevelopmentContextCycle。

引用必须属于输入证据，模型不能新增权限、修改范围或宣称通过；截断/非法响应
失败，消耗仍保留。confidence=0 表示未经验证，不使用模型自评授予实验资格。
草案不自动执行，不自动存为长期经验；调用方负责保存轨迹与草案。
直接调用设计 API 时依赖调用方提供的公开开发证据，不能识别 observation 字符串
中被错误粘贴的隐藏 rubric，也没有独立证据来源认证；不得直接输入评分器报告。
可选的 probe 后组合流程见下；当前仍不支持自动反复搜索或长期晋升。

### Probe 后组合

Python API 可省略 composed_candidate，改传 `composer(parents, reports)` 回调和
非空 `composer_identity`。两条父候选、模型、预算与组合器身份先冻结；前两组
开发 A/B 完成后才调用组合器。输出独立 ContextCandidate 或 None（弃权），
落盘为不可变 composition.json；恢复时复用该文件，不再次调用组合器。
组合必须独立执行第三组 A/B，不能继承父候选成绩。弃权只执行前四个 arm，
返回 KEEP_BASE。若进程在生成响应后、冻结文件前崩溃，恢复可能重新请求模型，
不承诺外部 API 恰好一次调用。

内置 `probe_composer.compose_after_development_probes` 可作为该回调：使用现有
proposal_composer 角色，核对父候选 digest、development 轨道及不变量，只投影
task_id/verdict/has_confounders，禁止把整份报告、评分细则或自由文本混杂描述
发送给模型。组合文本和假设受长度限制、引用受父证据集合限制，截断拒绝，
模型用量留在传入 TrajectoryLogger。调用方负责持久化该日志并冻结正确的
composer_identity；身份声明不是模型配置的独立认证。

生成与组合共用已有响应解码器：完整前导 think 块可移除，未闭合/嵌套拒绝，
保留响应 SHA-256、长度、解析错误原因/偏移而不保存推理正文。真实 MiniMax
页数修订证据生成复验已产生合法草案，但能力匹配和内容保持未获验收，见
results/jobbench-candidate-design-20261019.json；不要把合法 JSON 当有效经验。

此模式已通过 fixture 的实际两阶段执行、重跑及弃权恢复测试。真实模型有效性、
机制因果归因和完整 R2-C 验收仍未完成。

CLI 中用 `--compose-after-probes` 替代 `--composed-candidate composed.json` 即可
启用后组合；二者互斥。执行器仍使用 `--model`，组合使用项目配置的
proposal_composer 角色（可与执行模型不同），可用 `--composer-private` 请求
私有/本地路由。要求父候选的 applies_to 完全一致，且显式填写 repositories、
task_signals 和 does_not_apply_when；拒绝无范围自动组合。

组合器角色、provider/model、去敏配置摘要及作用范围摘要被冻结在 cycle manifest；
配置变化时不能原样恢复，需另起 cycle。`composer-trajectory.json` 保留生成
状态、路由与用量，失败也落盘，缺用量仍为未知；退出时关闭本次 router 的
HTTP transports。日志尚未与六个 arm 的成本汇总为完整实验账本。模型调用
本身由 60 秒/2500 输出 token 的上限约束，--max-cost 仍是执行 arm 的预算，
不是包含组合器的全流程费用上限。以上链路仅通过 fixture 验证，未运行付费
模型或真实 benchmark 成绩验收。

### 结构化轨迹证据入口

`development_evidence.collect_development_evidence(state_root, event_store, ...)`
接收 experiment_id、arm、development_task_ids、heldout_task_ids 和可选 limit
（1..20）。仅接受 track=development 的冻结 A/B 实验，并检查 task set digest、
run manifest、report digest、任务名单和 trace 的任务归属。clean/held-out 来源
直接拒绝，而不是按目录名称猜测分组。

支持已配对的 search/read_file/run_command 调用与结果，保存事件引用、原事件
摘要及经过白名单投影的工具类型/状态/退出码/参数摘要/同参数出现次数；优先
保留失败记录。缺失或不支持的事件不伪造证据，报告 omitted_count。不传输原始
命令、路径、搜索词、输出或 evaluator 内容。它能观察操作失败和重复，不能
据此证明根因、状态等价或任务失败；当前也未覆盖所有工具/适配器 payload。

`design_from_development_experiment(...)` 将该投影直接送入前述设计 API，返回
候选与 source_evidence；无可用事件时 NO_ENTRY 且不调用模型。调用方仍需保存
结果和轨迹。本地文件/事件一致性检查不等于防恶意篡改的来源认证，尚无真实
模型验收或独立 attestation；不能把 fixture 链路通过说成经验质量提升。

### 从轨迹生成文件

开发实验采集器现支持 `check_document_layout` 的结构化结果：只投影页数、
调用参数中的上限、是否满足上限和调用引用；参数使用摘要，不暴露路径、正文或
renderer 路径。结果的路径、上限、布尔值、状态须与调用及页数一致，否则跳过。
未指定上限保留 unknown，不当成满足任务约束；渲染成功不代表视觉或内容质量。
原有 development manifest/task/run 归属检查仍适用，不能从 held-out 偷取证据。
本次测试覆盖采集器到结构化证据的链路；尚未据此完成原生文档 A/B 执行或收益验收。
对已冻结 compact repair 轨迹做离线投影复验，提取 4 条布局结果，均为
2 页且未满足 1 页上限；未调用模型、未修改源材料。这仅验证投影器兼容真实
轨迹，不将该 seeded repair 冒充带完整开发实验 manifest 的 A/B。

修订关联增补：采集器也投影成功 `revise_docx` 的 before/after 字节摘要，验证
调用的 expected_sha256 与结果一致，不传递 replacement blocks、正文或备份路径。
布局结果只在相同路径及 source_sha256 对应唯一先前修订时记录 revision_event_id；
摘要不匹配、未来修订或重复版本生产者保持 unknown，不按相邻步骤推测。
该引用表示相同字节版本，不证明因果收益、语义保持或视觉质量。对上述冻结轨迹
离线回放，4/4 布局检查可关联唯一修订，0 因果效果声明，未调用模型。

前后比较增补：只有修订前已检查的 before_sha256 与修订后已检查的 after_sha256
可配对，且页数上限和记录中的 renderer 路径一致、前置检查唯一，才给出 page_delta
（after minus before）。缺失、重复或不一致保留 null；不会选最近一条无关检查。
真实 compact repair 离线回放的四次结果为 null、0、0、0：首次缺少前置检查，
随后三次未见页数减少。这是局部布局结果，不是内容保持、任务成功或因果收益。
renderer 路径相同不证明二进制/字体环境相同，完整环境等价仍由实验 manifest 验证。

真实 profile 生成复验仍失败：MiniMax-M3 在 4000 输出 token 内只返回推理块，
finish_reason=length，没有最终 JSON，未产生候选。见
results/jobbench-candidate-profile-20261019.json。缺少输出不等于能力约束已有效。

模型角色可显式添加 request_options，例如
`{"thinking":{"type":"disabled"}}`。支持字段仅 thinking（type=enabled/disabled）、
reasoning_effort（none/minimal/low/medium/high）和 reasoning_split（boolean）；
只为主 OpenAI-compatible provider 透传，不覆盖模型、消息、token 上限或权限。
默认不设置，非兼容 provider 或跨目标 fallback 不静默忽略/移植。具体模型支持
哪些字段、是否遵守由其 API 决定。角色配置仍进入实验配置摘要。

2026-10-19 后续真实单次复验：按 [MiniMax 官方说明](https://platform.minimax.io/docs/api-reference/text-openai-api)
为 M3 显式设置 thinking disabled，保持相同源证据、动作 profile、60 秒和
4000 输出 token 上限，正常返回 NO_ENTRY，1284 input / 12 output，1.591 秒。
结果仅证明本次请求被接受且正常弃权，不证明内部推理过程、候选质量或任务收益；
没有生成、执行或晋升候选。不能将两次单样本比较当作性能提升。证据见
`results/jobbench-candidate-direct-20261019.json`，不因弃权自动重试或扩大预算。

可选 `--executor-profile native-profile.json` 为生成器提供版本化原生动作范围：

```json
{"schema_version":"praxile.executor_action_profile.v1","executor":"praxile","version":"1","actions":["read_document","revise_docx","check_document_layout"]}
```

该模式要求每个候选及组合附带 1..8 个 action_examples，按实际 ActionSchemaRegistry
及 DOCX spec 校验；固定 standard/compact 版式约束进入提示词，不接受任意页边距、
字体字段或不存在的估页工具。profile 和接口摘要进入设计 manifest 与候选。
样例仅是具体 JSON，不会执行；其中摘要/路径不能代替运行时重新观察。

A/B 入口拒绝 executor 不匹配并重新校验 profile 摘要和样例。当前仅支持原生
praxile profile，不能把它标成 mini-SWE-agent 后直接运行；batch 暂不支持。
probe 后组合器只接受两个同摘要的父 profile，混合有约束/无约束、不同版本或
能力范围都在模型调用前拒绝；不取并集扩大范围。组合必须提供重新校验的
动作样例并保留 profile。cycle 独立复核预先组合或回调组合，防止自定义回调
丢掉父约束；实际运行仍受 A/B 的 executor 匹配检查。普通未绑定 profile 的
候选保留原兼容行为。
接口匹配不是环境可用性、权限或语义证明：模型正文仍可能出现与合法样例不一致
的建议，不能据此宣称草案整体可执行。工具健康检测、跨执行器 profile 转换
及真实模型能力约束复验仍缺。

```sh
praxile eval design-context prior-development.first \
  --design-id focused-design-001 --arm baseline --scope applies-to.json \
  --development-task-id owner__repo-dev --heldout-task-id owner__repo-heldout
```

`applies-to.json` 是显式范围对象，例如：

```json
{"repositories":["owner/repo"],"task_signals":["regression"],"does_not_apply_when":["documentation-only"]}
```

需替换实际实验 ID、任务 ID 与范围；来源必须是已经完成的 development A/B。
命令调用配置的 proposal_composer，支持 `--private`、`--resume` 和 `--json`。
输出位于 `eval/v2/candidate_designs/<design-id>/`：冻结 manifest、trajectory、
generated.json、三个候选 JSON 和报告。弃权时没有候选文件，不伪造结果。
候选文件可交给前述 `eval ab`，命令本身不启动实验、不写长期资产。

manifest 冻结来源投影、开发/held-out 名单、作用范围和去敏模型配置摘要；改变
这些输入必须另用 design-id。生成响应先保存，再生成派生候选文件，恢复可修复
缺失文件且不重复模型调用；已有文件不一致则拒绝覆盖。若模型响应与 generated
文件之间崩溃，仍可能重新请求模型，不能保证外部 API 恰好一次。轨迹在失败
路径同样保存已知用量，尚不代表完整跨阶段成本账本。

CLI 沿用 `eval ab`，同时提供以下参数启用组合流程：

```sh
praxile eval ab first.json development-tasks.jsonl \
  --second-candidate second.json --composed-candidate composed.json \
  --development-only --development-task-id owner__repo-dev \
  --heldout-task-id owner__repo-heldout --instance-id owner__repo-dev \
  --experiment-id development-cycle-001 --model openai/MiniMax-M3 \
  --timeout 900 --step-limit 50 --cost-tracking ignore_errors
```

替换示例任务 ID、候选文件与本地官方数据导出；模型及凭据配置沿用普通 A/B。
名单参数可重复，选中的任务集必须与 development-task-id 精确一致。不能同时
使用 capability-protocol/proxy-eval，避免把开发组合混入 held-out 协议。
普通 A/B 不提供这些新参数时行为不变。`--resume` 恢复整个 cycle；
`--json` 输出其完整报告。timeout/step-limit/max-cost 均为每个 arm 的限制，
不是六个 arm 的总预算；ignore_errors 不意味着零费用。该示例会执行真实模型，
请先准备候选及任务清单并确认费用。

受控 Context A/B 的分析报告新增 `improvement_episodes`，逐 task 保存 `praxile.improvement_episode.v1`。沿用原 report.json，不新增第二套数据库，也不改变既有 promotion/terminal-selection 门禁。

每项记录包含：

- baseline/candidate run ID、manifest digest、trace ID；候选 ID 和 digest。
- component_diff：冻结候选类型、digest 与注入标记；configuration_evidence 从两份
  manifest 保存精确上下文差异、前后 SHA-256 和 test/replace JSON Patch。
  该补丁作用于 policy 的 /context，可重放配置差异；不是代码或 hook 补丁，
  不导出 settings 等其他配置。缺失上下文不是空上下文，标记 unavailable。
  Patch 包含候选原文，随私有实验报告存储，公开前需审查；不能仅凭配置差异
  证明选中表示实际到达执行器或产生行为收益。旧调用没有 manifest 时证据为 null。
- behavior_prediction：冻结候选原有 expected_effect，不从实验结果倒推预测。
- probe：配对执行的 trace 引用；没有伪造未执行的局部 probe。
- observed_difference：官方 resolved 状态变化、双方确实提供的工具调用/延迟/可比较成本差异，以及原 progress 记录。
- confounders、uncertainty、verdict、next_action 与 NO_ENTRY 决定。

`helped/hurt/no_observed_effect` 只描述当前一对执行的任务结果变化，不能等同于行为机制因果归因。任务相同结果但成本降低时，outcome verdict 仍为 no_observed_effect，成本差异另列。缺 trace、官方结果、冻结条件验证或候选暴露证据则 confounded。缺失指标不补零，不可比较费用不输出差值。

每条均明确 `prediction_verified=false`、`promotion_eligible=false`，默认 `experience_entry=NO_ENTRY`。单对执行不能证明可迁移因果收益；后续仍需要重复实验、单组件消融和语义事件对齐。旧实验的分析可以产生这些解释记录，但不会自动将 held-out 结果用于生成候选。

本轮也修正 A/B 分类统计中的 progress 汇总：现在使用该失败类别内任务，而不是对每个类别重复汇总整个任务集。

## 不完整用量

开发 cycle 的 usage 字段列出各执行 arm 和可选组合器，报告已知 token/费用小计
及覆盖计数。组合器从 composer-trajectory.json 重算用量，不信任缓存摘要；
无日志的自定义回调保持 unknown。recorded_scope_cost_total 仅在所有组件完整
定价时出现，whole_experiment_cost_total 仍为 null：上游候选生成、独立 judge、
未记录请求还未统一纳入。组合器 cache 用量与价格没有记录时不补零。单项已知
费用保留，但不得当作整场实验费用或收益证据。

Eval trace/aggregate 增加 tokens_complete、tokens_total 和覆盖计数；原 tokens
仅为已知小计。存在没有 TokenUsage 的 MODEL_CALL 或完全没有模型调用事件时，
完整总量保持 null；明确的零用量对象才计为零。A/B 传播不完整标记，不把小计
作为效率改善证据。汇总旧报告若没有完整性标记，不宣称完整覆盖。
此检查范围是规范化模型事件；旧适配器已填零的字段、未进入 trace 的请求和
独立 judge 仍不可还原，不能据此宣称全实验账本已完整。

A/B task、类别与全量汇总不再把缺失 token/工具次数/延迟/费用补零。token 的
input/output/cache 必须明确为非负整数，缺一个分项即总 token 未知；工具次数
同样严格，延迟允许非负有限小数。无效布尔、负值、NaN、数值字符串都不算有效
证据。`cost_complete=true` 还需实际有效金额，不能仅凭该标记构造费用差值。

差值缺任何一侧时为 null；汇总缺任何任务时也为 null，不把部分任务小计伪装成
总量。`metric_coverage` 为每项差值记录 paired_tasks/total_tasks；逐任务有效
差值仍保留。任务结果未变且效率证据不完整时 decision=inconclusive，不宣称
节省；客观 resolved 改善/退步仍按原优先级单独报告。进展计数的差值也要求双方
有明确观察，缺失不等于未发生。此修复不补造旧实验缺失的用量。

当前未实现：可执行 component diff、行为预测的独立 verifier、子任务/语义事件对齐、独立 probe 生命周期，以及 JobBench/WorkBuddy 中性评分对象的同类接入。测试用 fixture A/B 证明记录生成和持久化，不是模型性能证据。

## 操作身份对齐增补

受控 A/B 和 Context Policy ablation 共用的 compare_ab_reports，现在对每个 task 的两个 trace 生成 operation_alignment，ImprovementEpisode 同时保留该记录。支持 native/mini-SWE 的完整 read_file、search、run_command 参数，按规范化参数摘要匹配，不按 step_id/message_index 对齐。

只有两侧各唯一出现一次的同参数操作建立事件引用对；重复、截断或无法识别的动作保持 unpaired/unsupported。参数不同不做模糊猜测。报告包含操作类型、摘要和 event IDs，不复制命令正文；摘要不是加密措施，原始证据仍受原 trace 权限管理。

同一命令不代表同一状态：工作目录、环境与文件内容可能不同，因此每一对均标 state_equivalence=unknown，causal_alignment=false。此步不是完整子任务语义对齐，也不验证行为预测或晋升。4 项专门测试覆盖乱序、重复命令、截断、跨 adapter 结构及不同参数，另有 A/B 回归验证接入。

## 子任务引用对齐

A/B task comparison 与 ImprovementEpisode 现包含 `subtask_alignment`，版本为
`praxile.subtask_alignment.v1`。从两条 trace 的 SUBTASK_EPISODE 读取已关闭子任务，
按只规范化空白的精确 goal 摘要配对；不按步骤号、不做模糊语义猜测、不复制目标
正文。两侧都唯一才配对，重复目标/no counterpart 留在 unpaired。

配对保存 episode/event IDs、确实存在于相应 trace 中的 action event IDs、前后
快照摘要与 claimed_outcome。未关闭、非法或动作引用悬空的子任务列入 unsupported。
前置快照缺失或不能规范序列化时 unknown，摘要相同只记 equal_recorded_fields，
不同记 different_recorded_fields；`state_equivalence=unknown`、
`semantic_equivalence_verified=false`、`causal_alignment=false` 保持不变。

因此该记录可定位“两个执行中哪个子任务值得比较”，但不是子任务成功验证器，
也不证明自然语言目标等价或外部应用状态一致。完整语义状态对齐仍未完成。
6 项专门测试覆盖乱序、重复目标、状态差异、非法摘要和悬空引用；旧 A/B 比较
回归保持通过，不触发模型调用或经验晋升。

## 冻结行为测量

候选的 `expected_effect` 可在实验前增加以下精确测量定义；不能在分析时根据
结果反推或改写预测。现有 candidate digest/冻结协议继续约束该字段。

```json
{
  "hypothesis": "The candidate reduces repeated identical searches",
  "measurement": {
    "kind": "repeated_exact_operations",
    "operation": "search",
    "direction": "decrease"
  }
}
```

operation 支持 search/read_file/run_command，direction 为 increase/decrease。
`ImprovementEpisode.behavior_probe` 保存版本化 `praxile.behavior_probe.v1`、
两侧重复数、差值、操作摘要及具体 event IDs；每个同参数组的重复数为
`max(0, occurrences - 1)`。使用现有 control-plane 操作对齐结果，不按第 N 步配对，
不依赖模型自报“我少搜索了”。不会把命令正文复制到解释记录。

测量方向符合预测时，嵌套 `prediction_verified=true` 仅代表该算术预测得到观察支持；
嵌套 verdict 的 helped/hurt 只描述指定指标的方向。原顶层 verdict 仍基于任务结果，
顶层 prediction_verified=false 仍表示没有证明完整机制假设。例如双方都失败，
重复搜索从 2 降至 0：任务 verdict=no_observed_effect，行为 verdict=helped，
promotion_eligible=false，experience_entry=NO_ENTRY。重复减少不证明策略更优。

相关动作参数截断/缺失、未知动作类型可能影响覆盖、重复事件引用、缺测量定义或
没有观察到目标操作时 abstain；缺执行不变量或候选暴露时行为 verdict=confounded。
已知非目标动作（如 edit_file）不会让搜索计数一律失效。旧 alignment 缺少类型信息
时保持保守弃权。环境状态等价性仍 unknown，也没有重复试验或因果显著性声明。

638 项快速单元测试通过，包含完整/不完整证据、长链缺信息、任务失败与行为方向
分离及旧 A/B 回归。真实模型预测质量、其他行为测量和中性 benchmark 的同类接入
仍未验证；完整 R2-B 不勾选。
