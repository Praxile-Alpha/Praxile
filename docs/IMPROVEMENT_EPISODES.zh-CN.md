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
