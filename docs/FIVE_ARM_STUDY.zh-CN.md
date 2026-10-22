# 五组研究计划

计划模块只冻结开发实验计划，不运行模型，不评分，不授予晋升资格。声明结果
分析见后文；真实五组执行、预算控制和来源验证尚未接入，不能把成功生成
manifest 当成五组实验已完成。

## 输入

准备一个 JSON 对象，且仅含下列字段：

| 字段 | 内容 |
| --- | --- |
| study_id | 1..61 字符，字母或数字开头，其余可用字母、数字、下划线、点、短横线 |
| task_ids | 按执行顺序排列的非空开发任务 ID 列表，唯一 |
| heldout_task_ids | 非空保留任务 ID 列表，唯一，不能与开发任务重叠 |
| repetitions | 1..100 的整数；不是把重复运行当作独立任务样本 |
| repeat_attempts | 2..100 的整数；仅重复执行对照组允许多次尝试 |
| identities | 下文所列九项 SHA-256 身份摘要 |
| budget | total_tokens、model_calls、wall_seconds 三个正整数 |
| arm_order | 下文五个组名，各出现一次，顺序冻结 |

identities 必须包含 dataset、executor、model、environment、judge、memory_snapshot、
experience_policy、task_local_policy、terminal_selection。值均为 `sha256:` 加
64 位小写十六进制摘要。应对实际冻结的材料与配置计算摘要，不填“latest”或
示例假值。摘要仅为调用方声明，本模块不读取原始材料，也不认证来源。

## 五组语义

| 组名 | 使用经验 | 任务内改进 | 最大尝试次数 |
| --- | --- | --- | --- |
| frozen_executor | 否 | 否 | 1 |
| budget_matched_repeat | 否 | 否 | repeat_attempts |
| experience_only | 是 | 否 | 1 |
| task_local_only | 否 | 是 | 1 |
| full_praxile | 是 | 是 | 1 |

budget 是每个 arm/task/repetition 的共同总上限，所有角色及重试共享，不能
给每次尝试或额外 judge 重新分配一份预算。model_calls 必须至少容纳 repeat_attempts。
这个约定尚未由执行器强制实施，字段 budget_enforcement_verified 始终为 false。
相同上限也不等于实际用量相等，后续应同时报告消耗与有效结果。

所有组禁止持久化更新，经验快照在研究期间冻结；本版本只支持 development，
不执行 held-out，也不混入 online-stream。多尝试的终局选择规则须事先冻结，
不能事后借助保留集答案择优。执行器实际遵守这些规则仍需另外验证。

## 冻结与调度

```bash
python -m praxile.eval.five_arm_plan --spec study.json --output /path/to/study-plan
```

写入 manifest.json，返回摘要、计划单元数及 plan_only。相同输入可以重复调用，
已存在 manifest 内容不同时拒绝覆盖。文件锁保护并发冻结，但不是防篡改签名。
不在输出目录保存凭据、任务正文、rubric 或答案。

Python API `compile_plan(spec)` 生成计划；`iter_schedule(plan)` 按 repetition、
任务顺序、组顺序生成独立 cell_id。它不展开各组内部尝试，每个 cell 的
shared_budget 是整个 cell 的额度。生成前重新编译并比对计划，防止只改 arm
标志而没有修改输入；返回的预算副本不会改变冻结计划。

本次测试覆盖计划冻结、拆分隔离声明、调度顺序、预算共享语义、输入非法值、
篡改检测及 CLI；没有真实五组效果、统计显著性或能力提升结论。

## 声明结果分析

```bash
python -m praxile.eval.five_arm_analysis \
  --plan /path/to/study-plan/manifest.json \
  --results cell-results.json --output study-analysis.json
```

results 是数组，每项仅含 cell_id、plan_digest、source_sha256、status、score、
maximum。cell_id 来自 iter_schedule；plan_digest 来自冻结命令；source_sha256
为来源报告的 `sha256:` 摘要。status 为 completed/failed/unknown。completed
要求 0 <= score <= maximum，其他状态 score 必须为 null，不能凭失败状态补零。
maximum 必须有限且大于零，同一任务跨组和重复保持一致。

分析器拒绝计划摘要不符、未知 cell、重复 cell、分母变化和非法分数。先按
每个任务在每组内的 repetitions 求归一化均值，再计算相对 frozen_executor
的四个任务级配对差值；不同任务等权，不把 repetitions 当独立样本。

所有比较使用五组共同完整的任务集合。任一 cell 缺失或未完成，其所属任务
对所有组均排除，并列出缺失/未完成 ID；输出描述性均值但不输出置信区间。
只有全部计划任务齐全，才输出四个预设比较的 Bonferroni 修正 Hoeffding
familywise_ci95。区间假设任务簇相互独立，相关任务族需另外设计聚类。

source_sha256 和分数目前只是声明，本分析器不打开来源报告、不验证签名或
runtime 配置，也不实现终局选择、预算强制或效果晋升。因此结果完整也只是
complete_declared_results，controlled_execution_verified 与 promotion_eligible
始终为 false。真实五组验收仍未完成。
