# 配对实验解释记录

受控 Context A/B 的分析报告新增 `improvement_episodes`，逐 task 保存 `praxile.improvement_episode.v1`。沿用原 report.json，不新增第二套数据库，也不改变既有 promotion/terminal-selection 门禁。

每项记录包含：

- baseline/candidate run ID、manifest digest、trace ID；候选 ID 和 digest。
- component_diff：指向冻结候选的类型和 digest，是否确有注入；当前是候选引用，不是可执行补丁。
- behavior_prediction：冻结候选原有 expected_effect，不从实验结果倒推预测。
- probe：配对执行的 trace 引用；没有伪造未执行的局部 probe。
- observed_difference：官方 resolved 状态变化、双方确实提供的工具调用/延迟/可比较成本差异，以及原 progress 记录。
- confounders、uncertainty、verdict、next_action 与 NO_ENTRY 决定。

`helped/hurt/no_observed_effect` 只描述当前一对执行的任务结果变化，不能等同于行为机制因果归因。任务相同结果但成本降低时，outcome verdict 仍为 no_observed_effect，成本差异另列。缺 trace、官方结果、冻结条件验证或候选暴露证据则 confounded。缺失指标不补零，不可比较费用不输出差值。

每条均明确 `prediction_verified=false`、`promotion_eligible=false`，默认 `experience_entry=NO_ENTRY`。单对执行不能证明可迁移因果收益；后续仍需要重复实验、单组件消融和语义事件对齐。旧实验的分析可以产生这些解释记录，但不会自动将 held-out 结果用于生成候选。

本轮也修正 A/B 分类统计中的 progress 汇总：现在使用该失败类别内任务，而不是对每个类别重复汇总整个任务集。

当前未实现：可执行 component diff、行为预测的独立 verifier、子任务/语义事件对齐、独立 probe 生命周期，以及 JobBench/WorkBuddy 中性评分对象的同类接入。测试用 fixture A/B 证明记录生成和持久化，不是模型性能证据。

## 操作身份对齐增补

受控 A/B 和 Context Policy ablation 共用的 compare_ab_reports，现在对每个 task 的两个 trace 生成 operation_alignment，ImprovementEpisode 同时保留该记录。支持 native/mini-SWE 的完整 read_file、search、run_command 参数，按规范化参数摘要匹配，不按 step_id/message_index 对齐。

只有两侧各唯一出现一次的同参数操作建立事件引用对；重复、截断或无法识别的动作保持 unpaired/unsupported。参数不同不做模糊猜测。报告包含操作类型、摘要和 event IDs，不复制命令正文；摘要不是加密措施，原始证据仍受原 trace 权限管理。

同一命令不代表同一状态：工作目录、环境与文件内容可能不同，因此每一对均标 state_equivalence=unknown，causal_alignment=false。此步不是完整子任务语义对齐，也不验证行为预测或晋升。4 项专门测试覆盖乱序、重复命令、截断、跨 adapter 结构及不同参数，另有 A/B 回归验证接入。
