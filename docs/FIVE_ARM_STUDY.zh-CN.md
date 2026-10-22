# 五组研究计划

## 命令行预检与执行

```sh
python -m praxile.eval.five_arm_cli \
  --plan /path/to/compiled-plan.json \
  --task /path/to/frozen-task.json \
  --execution-config /path/to/execution.json \
  --judge-config /path/to/judge.json \
  --input-roots /path/to/input-roots.json \
  --output /path/to/new-study
```

默认仅预检；检查通过后，在同一命令末尾添加 `--execute` 才开始 Docker、模型
及可计费 judge 调用。多任务重复 `--task`，重复 task_id 拒绝。plan 必须为
five_arm_plan 编译结果，而非原始 spec。CLI 与 execute_study 共用 prepare_study，
验证任务、候选摘要、预算及冻结 judge/selection；CLI 另检查 worker/dependency
wheel。模型要求非空，镜像须 digest-pinned，凭据 broker 使用无内嵌认证的 HTTPS。

execution.json 必填字段如下，替换示例路径、模型及 SHA256 后使用：

```json
{
  "wheel": "/path/to/praxile.whl",
  "wheel_sha256": "ACTUAL_WHEEL_SHA256",
  "model": "YOUR_EXECUTOR_MODEL",
  "model_request_options": {"reasoning_effort": "low"},
  "base_url": "https://YOUR_PROVIDER/v1",
  "credential_file": "/path/to/private-executor-key",
  "output_budget": 10000,
  "max_steps": 50,
  "development_candidate_file": "/path/to/candidate.json",
  "development_candidate_sha256": "ACTUAL_CANDIDATE_SHA256",
  "repository": "public/docs",
  "terminal_selection": {
    "schema_version": "praxile.five_arm_selection.v1",
    "strategy": "first_delivered",
    "eligibility": "executor_returned_cleanup_and_required_outputs",
    "uses_quality_scores": false
  }
}
```

可选 dependency_wheels 是 `[{"path":"/path/to/dependency.whl","sha256":"..."}]`；
required_capabilities 是版本化能力名称数组。禁止未知字段和按 arm 覆盖配置。
model_request_options 可省略（空对象）；只允许 thinking.type、reasoning_effort、
reasoning_split，不允许覆盖 messages、工具或 token 预算。参数传递至所有 arm 的
owned-native/adapter/worker 和 broker，并记录在 cell 与 execution 报告；供应商
是否接受这些参数仍需真实调用验证，不能从配置推断后端实际执行了何种推理。
独立 judge 仍使用自己的 request_options，不继承 executor 设置。

单次 owned-native CLI 使用 `--model-request-options /path/to/options.json` 读取
相同白名单对象。参数纳入命名 run 的计划摘要；改变参数不能 resume 已完成
记录。新计划显式记录空对象，因此缺该字段的旧记录也不能冒充新条件复用，
应保留旧证据并使用新 run-id，而不是修改原 claim。
judge.json 使用下文 judge API 的同名配置字段；其中 credential_file、evaluator_root
为路径字符串，不嵌入 API key。input-roots.json 为 `{"TASK_ID":"/path/to/public-inputs"}`；
无输入任务可省略此参数。所有相对路径均相对于**当前工作目录**，不是配置文件目录。

预检只读取候选、评审要求和包等本地证据，不读取密钥正文、不创建实验目录或
quota，也不联网。stdout 仅展示计划摘要、计数和预算，不输出私有路径/任务正文。
预检成功不代表凭据可用、公共输入已核验或基础设施可运行；实际执行再次校验，
目录必须不存在，不提供 resume 或自动重放。执行返回 0 只说明调度完成，不是
任务成功；随后使用下节 audit。错误返回 2，中断返回 130。完整总 token 控制、
来源认证、真实五组收益及自动晋升均未由该入口完成。

## 离线核验与开发标签分析

```sh
python -m praxile.eval.five_arm_audit \
  --study /path/to/study --study-sha256 EXPECTED_STUDY_JSON_SHA256 \
  --task /path/to/frozen-task.json --evaluator-root /path/to/development-requirements
```

多任务重复 --task。该命令只读，不调用模型、续期预算或修改实验；完整可比较
标签返回 0，不完整返回 1，证据校验失败返回 2。study-sha256 是调用方预期的
study.json 摘要，不是工具自行读取后声称可信的来源证明。

API audit_study 接收相同的冻结 plan/tasks，核对任务身份、cell 顺序、评审摘要，
重新验证 selection 与原始产物/trajectory/开发要求，再检查 judge manifest、
输入正文、分组预算与报告/轨迹一致性。关键证据缺失、替换、链接或摘要变化
拒绝核验。未调度、未选择和结果未知的评审保留缺失，不补零；abstain 或包含
uncertain 标签不进入完整共同任务集合，缺任务时不输出比较置信区间。

执行条件核验：新 study.json 保存 executor_conditions，包含 model、worker wheel
摘要、endpoint 摘要、model_request_options 和 max_steps。audit 核对每个已绑定
cell，以及所有已返回 attempt（包括未被选择的重复尝试）的对应字段；同时核对
任务 fingerprint 和 image。即使重算文件摘要，只要某次尝试与共同条件不同，
仍拒绝比较。旧报告缺字段直接拒绝，不依据模型名称或相邻记录推断。
recorded_executor_conditions_checked 仅说明已绑定记录的局部一致性：未返回尝试
仍为 unknown，不认证模型权重、请求实际处理方式、worker 依赖或完整来源身份。
调用方仍须保存可信 study 摘要；同时改写整套证据及摘要不在此检查的防御范围。

机制配置核验：audit 按冻结 schedule 验证 candidate_identity 及每次已返回执行的
task_local_memory_enabled/development_context_identity。冻结组、重复组、仅任务内
组不得携带候选；经验组与完整组必须使用 study 中该任务的同一候选身份。
记忆开关须为严格布尔值，缺字段、整数冒充布尔值或与组定义不符都拒绝。
recorded_mechanism_configuration_checked 不等于机制产生收益：候选通过通道后仍
可能被 activation gate 弃权，模型也可能没有调用任务内工具。该检查不验证实际
激活次数、任务内改进质量或后端执行，旧缺证据记录不能当作已验证消融对照。

### 实际机制观察

cell_reviews[].mechanism_observations 逐 attempt 读取 execution.json 绑定的
source_result_sha256，再核对 worker trajectory。包括未选中的重复尝试；摘要
变化拒绝，未返回尝试或缺 source 摘要保留 unknown。输出只含观察值、计数和
摘要，不输出任务正文、候选内容或工具输入输出。

分别记录 activation_observed、injection_observed 和 subtask_note/subtask_reflect
的 attempted/success/blocked/other 次数；旧字段缺失保留 null，显式空动作数组
才表示未观察到动作。已开启但动作数组中没有记忆动作时标记
configured_task_local_without_observed_actions，不能据此认定机制永久无效。
关闭记忆却记录成功、worker/runtime 开关或注入互相矛盾、未正向激活却注入、
候选身份不符等情况拒绝核验。工具成功记录仍不是独立的效果验证；没有推断
反思是否改善后续动作或任务质量，causal_benefit_verified 始终为 false。

统计量明确为 llm_declared_requirement_preservation：已声明要求中被 judge 判为
preserved 的比例，不是 resolved rate 或官方成功率。共用既有 task-cluster
分析，记录已检查 cell 数和证据覆盖，重复运行不充当独立任务。引用和输入
文件一致性不认证模型理解正确，也不防御同时改写整套文件与预期摘要的主机。
semantic_accuracy_verified、controlled_execution_verified、promotion_eligible
仍为 false。完整五组 fixture 与篡改拒绝已验证，真实收益仍须独立验收。

### 全部尝试与 Judge 的局部成本账本

离线 audit 同时返回 broker_usage。它验证每次 executor attempt 的执行报告
摘要，再读取其绑定的 broker_usage_sha256；重复组中未被选中的尝试也计入。
judge 用量来自研究回执绑定的 broker-usage.json，不把共享 quota 快照中的
累计 reserved_calls 重复当作本次调用成本。

按 executor/judge 分别列出 expected_receipts、bound_receipts、known_admitted_calls、
admitted_calls、known_tokens、reported_calls 和 total_tokens。校验 admitted =
responses + failed + inflight，以及 token 报告覆盖与已知小计/完整总量的一致性。
缺回执使调用总量未知；失败或缺 token 字段使 token 总量未知，但保留已知小计。
显式报告的零值仍是零，不与缺失混淆。篡改未选尝试的回执也会使核验失败。

scope 仅为已绑定执行尝试与 judge broker 的供应商报告；延迟是请求耗时小计，
不是墙钟时间。all_scheduled_cells_recorded 单独说明研究覆盖，未调度 cell 不
能从已有小计推断成本。cost_usd 保持 null，billing_verified 和
total_token_bound_verified 保持 false；proposer/probe/composer 等上游设计阶段
未并入，不能将此账本称为完整实验费用认证。

### 任务级 Token 成本比较

audit 的 broker_cost_comparison 将每个 cell 的全部已绑定 executor 尝试和独立
judge 用量相加，输入/输出 token 分开比较。先计算同一任务、同一 arm 的各次
repetition 均值，再对每个任务计算 arm - frozen_executor，最后对任务等权平均。
大 token 任务不额外加权，重复运行不增加独立任务样本数。

五组及两个 token 字段共用完整任务集合：任一组、任一次重复缺失或未知用量，
整项任务不进入任何成本配对；报告 missing_cells/unknown_cells 供核查。显式
零仍参与计算。fixture 验证重复组未选尝试与 judge 都计入，而非仅统计最终产物。
这是描述性差值；缺失可能带来选择偏差，不能直接宣称效率显著提升。

confidence_interval 保持 null：目前没有预先冻结且经验证的实际输入 token 上界，
不能把 plan 的 total_tokens 声明当作已执行的限制套用有界统计公式。cost_usd
仍未知，未计入上游经验设计；此比较也未按任务质量筛选，judge abstain 时
完整报告的成本仍可参与成本统计，不表示该任务质量合格或可晋升。

## Development Cell 执行入口

`praxile.eval.five_arm_execution.execute_cell(plan, cell_id, task, ...)` 将已编译
计划中的一个 cell 映射到 owned-native runner。五组的经验/任务内记忆开关
由计划决定，不接受调用者覆盖；仅经验和完整组要求冻结 candidate，其余组
不传递候选。每次尝试使用独立租约和全新状态，同一 cell 的尝试共享持久化
broker quota。重复组额度耗尽即停止，不按尝试重置。参数和默认执行环境沿用
owned-native；本入口当前仅接受匹配 task_id 的 development 任务。

调用方必须另传 output_budget（不大于声明 total_tokens），这是**输出额度**，
不能冒充总 token 预算。model_calls 来自冻结计划，wall_seconds 同时约束 quota
有效期和剩余执行时间；超时后等待 runner cleanup，清理开销可能超过期限。
不覆盖失联进程或供应商取消，不认证完整输入 token、费用或来源身份。

目录独占创建，cell.json 在 dispatch 前保存意图，每次返回绑定 execution.json
摘要。失败/取消保留 outcome_unknown 并停止，不继续尝试或自动重放已有目录。
返回 executed_not_scored 仅表示所有尝试已返回且报告确认 cleanup，不表示
任务成功。默认 selected_attempt 为 null；不调用 judge、不读 held-out rubric、不
自动选优或晋升。可选的冻结顺序选择见下文；judge 共享额度接入和真实五组
验收仍待实现，controlled_execution_verified 保持 false。

## 完整 Development 研究调度

`execute_study(plan, tasks, directory=..., execution=..., input_source_roots=...)`
顺序执行计划内全部 cell。tasks 是 task_id 到 BenchmarkTask 的精确映射，不能
缺任务、增加隐藏任务或替换 development split；public inputs 按任务显式提供
source root。execution 是所有组共用的 owned-native 配置：wheel、wheel_sha256、
model、base_url、credential_file、output_budget、development_candidate_file、
development_candidate_sha256、repository；可选 max_steps、dependency_wheels、
required_capabilities、terminal_selection。不接受逐组覆盖机制开关。

在创建研究目录前校验任务映射及全部候选身份。按冻结 repetition/task/arm 顺序
执行，cell 独立配额；组内重复尝试共享 quota。研究目录独占创建，plan.json 保存
计划，study.json 保存任务 fingerprint、候选身份、逐 cell 意图及报告摘要。
异常/取消保留已返回 cell 并停止；进程重启不能自动重放同一研究。额度耗尽
可作为该 cell 的终态记录，不补齐重试或偷偷增额。

dispatched_not_scored 表示调度已结束，不代表任务完成或研究有效；部分 cell
可能 budget_exhausted。本阶段测试使用真实 cell/quota 调度与模拟 executor，
并非真实 Docker/model 五组验收。依然缺质量驱动选择、judge 配额接入、
冻结身份来源认证及完整成本/效果分析，不授予 promotion。

## 冻结的交付顺序选择

可通过 `selection_policy("first_delivered")` 或 `selection_policy("last_delivered")`
构造规则。在生成计划前，将规则 canonical_json 的 SHA-256（含 sha256: 前缀）
写入 spec.identities.terminal_selection；随后把同一规则作为 terminal_selection
传给 execute_cell，或放入 execute_study 的 execution 字典。摘要不匹配在创建
执行目录前拒绝，不能完成实验后换规则选结果。未提供规则仍保持不选择模式。

选择器读取每条已返回尝试的执行报告，验证记录摘要、顺序及路径归属，拒绝
符号/硬链接和路径逃逸。只将报告中 executor_returned、cleanup_verified=true、
required_outputs_collected=true 的尝试列为 eligible，按预先固定的首/尾规则选择。
缺字段不当作交付完成；没有 eligible 则 selected_attempt=null，不兜底选失败版本。
终态 budget_exhausted 也只在已返回尝试内选择，异常和 outcome_unknown 不选择。

这是**回执层面的交付选择**，不重新认证每个输出文件，不判断任务正确性，
不消费 judge/隐藏分数，不产生 best-of-N 质量提升声明。所有尝试和未选结果
继续保留；selection_performed 只表示规则已执行，quality_verified 和
promotion_eligible 始终 false。当前安全文件读取沿用 POSIX artifact reader。

## 结构化 Judge 的 Broker 前置支持

ChatCredentialBroker 可显式接受 response_contracts（1..32 个既有格式的 name/schema
对象，总序列化大小不超过 1 MiB）。创建时复制冻结成精确的 submission_request，
请求只能提交其中一个固定 tools/tool_choice 组合；不得改 schema、函数名、自动
选工具或开启 parallel_tool_calls。未配置此能力的 executor broker 仍拒绝 tools。
这仅允许提交结构化结果，不提供工具执行权限；响应仍需 provider 和内容评审器
完成 schema、引用及覆盖检查。

judge broker 可使用同一 SharedChatBudget/PersistentChatBudget 和 budget_role="judge"，
实际转发前扣减共享请求/输出额度。真实本机 HTTP + OpenAI-compatible provider
测试验证 executor 先消耗额度、judge 提交一次、后续转发被拒绝及线程清理；
上游为 fixture，不是付费模型评审。完整研究的 judge 创建、每组 contract 冻结、
评分编排和证据绑定尚未接入，本项是协议前置支持，不等同 judge 集成完成。

开发内容评审入口现已实现：run_broker_content_review 在调用前冻结各分组
contract，实际请求经 broker 以 judge 角色扣减共享额度；支持直接打开 cell 的
PersistentChatBudget，保留独立输入/轨迹/评审及 broker 用量回执。没有重试或
fallback，额度过期或不足会弃权。它仍需控制面显式提供已抽取的公开正文，
尚未自动从五组 selected_attempt 绑定产物/来源，研究级评分集成仍未完成。
详见 CONTENT_PRESERVATION_REVIEW.zh-CN.md。

## 选中产物到 Judge 的来源绑定

`praxile.eval.five_arm_review.prepare_selected_cell_review` 接受冻结 plan、cell_id、
BenchmarkTask、cell_directory 和预期 cell_sha256，以及独立 evaluator_root、
source_path/output_path。先验证 cell 摘要/计划/任务身份、重放冻结 selection，
再复用 prepare_owned_review 验证完整输入输出合同、产物集合摘要、trajectory
与开发要求摘要。支持 text/docx_body/pdf_text 抽取，保留现有抽取版本和限制。
选择摘要不一致、缺交付版本、链接、篡改或任务不匹配时，在模型调用前拒绝。

`review_selected_cell` 进一步打开该 cell 已有 quota.sqlite，调用独立 broker
内容评审；要求 public=True（显式授权发送该次开发要求和产物）。不创建、补充
或续期额度。judge 状态和 evaluator_root 必须位于整个执行 cell 外。只支持
praxile-content-review/1 开发 evaluator，不接官方隐藏 rubric。

judge manifest 保存 execution_binding 和 selection_binding，串联 plan/cell/
policy/选中执行报告、产物/trajectory/要求/抽取摘要。它检查本地证据一致性，
不是恶意主机上的真实性证明；纯文本/DOCX/PDF 抽取也不代表视觉质量验收。
该显式入口已通过真实本地文件、SQLite、评审链路的 fixture 上游测试；完整
研究尚未在每个 cell 结束后自动调度 judge，过期额度会弃权而非自动延长。

## 研究内逐 Cell 开发评审

execute_study 现支持可选 judge 配置，在每个 cell 完成固定 selection 后评审
选中版本，随后才进入下一 cell。未选出版本记录 not_selected，不挑失败产物
或调用 judge。评审标签不传回 executor，不改变选择，不触发额外尝试或晋升。

配置必填 credential_file、endpoint（HTTPS chat/completions）、model、public=True、
total_output_tokens、evaluator_root、artifacts。artifacts 精确映射全部开发任务到
source_path/output_path，可指定 source_format/output_format。可选 batch_size、
timeout_per_call、request_options；所有任务沿用同一 judge 模型和预算设置。
先用 five_arm_judge.judge_identity(judge, tasks) 生成不含凭据/私有目录的身份，
将 canonical_json 身份 SHA-256（sha256: 前缀）写入计划 identities.judge，再冻结
计划。调度前核对该摘要和开发要求文件摘要，且要求预注册 terminal_selection。
该配置绑定不证明供应商别名背后的模型权重或执行二进制身份。

judge 使用 cell 剩余额度和截止时间，不为评审自动预留或续期；executor 消耗
完预算可能导致评审 abstain。研究级回执保留每份评审报告以及 manifest、输入、
trajectory、broker-usage 文件摘要。judge 异常保留已知执行回执并停止研究；
取消会等待同步评审线程收尾，并阻止后续分组请求，已发请求仍可能持续到
超时。评审目录位于 study/reviews，要求存储必须在整个 study 外。

该链路已通过真实调度/SQLite/抽取绑定/评审逻辑加 fixture 上游的完整五组
测试，无真实 Docker/云模型验收。输出仍是 development 标签，不是官方成绩，
不证明完整总 token/费用控制、judge 校准或收益；R3 总项保持未勾选。

计划模块只冻结开发实验计划，不运行模型，不评分，不授予晋升资格。声明结果
分析见后文；真实五组验收、完整预算控制和来源验证尚未完成，不能把成功生成
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

## 从已有 benchmark 导入报告分析

```bash
python -m praxile.eval.five_arm_imports \
  --plan /path/to/study-plan/manifest.json \
  --bindings import-bindings.json --output study-analysis.json
```

bindings 数组每项含 cell_id、path、sha256、task_checksum、maximum。path 指向
现有 JobBench/WorkBuddy importer 输出的 JSON，而不是原始 judge 文本；sha256
使用 `sha256:` 前缀，task_checksum 和 maximum 应取自冻结的任务材料。所有
bindings 必须属于同一种 benchmark，且不能复用同一任务的同一份报告冒充不同
cell。缺失 cell 保留为缺失，不自动补跑。空 bindings 不声称已核对来源。

入口单次读取最多 16 MiB 的普通文件，对同一份字节校验摘要和解析。核对计划
task_id、任务 checksum 和分母后，仅提取该 benchmark 的官方主评分字段；
不接受 self_reward 代替官方字段，不把超时或缺评审当零分。结果保留摘要和
任务 checksum，不复制原报告中的私有 rubric、文档正文或本机路径。

`source_file_digests_verified=true` 仅说明绑定的本地文件摘要匹配。它不能证明
文件由官方评分器生成，不能证明任务 checksum 与计划 dataset 摘要的关系，
也不复验原始 verifier 证据、executor 配置或预算。完整的执行来源绑定和真实
五组验收仍待实现，controlled_execution_verified/promotion_eligible 保持 false。

## 共享 broker 配额

控制器可创建一个 `SharedChatBudget(max_calls=..., output_budget=..., ttl_seconds=...)`，
并把同一对象传给各 `ChatCredentialBroker` 的 shared_budget，budget_role 分别
声明 executor/composer/judge。各 broker 仍保留自己的本地限制，共享配额通过
锁串行预留。未通过鉴权、本地验证或本地配额时不消耗共享额度；一旦预留则
失败、超时或缺少 usage 都不退款。创建新 broker 但复用该预算不会重置余额。

status 包含各角色 reserved_calls/reserved_output。它只限制同一进程中经过
这些 broker 的调用准入与请求输出上限，不限制输入 token、计费金额，也不会
在 TTL 到期后取消已经发出的请求。该对象不落盘、不支持跨进程共享；控制器
重新创建预算或绕过 broker 的请求不在保证范围内。

五组计划中的 total_tokens 包含输入和输出，不能直接当成 output_budget 使用
并宣称总 token 已被限制。五组调度尚未自动配置共享预算，真实输入 token
计数、所有角色接入及任务总墙钟控制仍未完成，budget_enforcement_verified
继续为 false。

Harbor `PraxileAgent` 现在接受控制器传入的 shared_chat_budget，同一对象可复用
于多次 adapter 尝试，创建临时 broker 时传入共享预算且标记 executor 角色。
必须同时使用 credential_file；无 broker 的直连路径拒绝该配置。清理后的
praxile-broker-usage.json 包含共享状态，单次 broker 撤销不重置共享余额。
测试在 Harbor 0.18/0.22 使用 fixture worker 验证两次尝试及异常清理，不是
真实容器五组验收。owned-native CLI 和五组调度尚未自动创建或持久化此对象。

Python `run_owned_native(..., shared_chat_budget=budget, credential_file=...)` 也已接入：
预算开始状态进入运行计划及 execution.json，清理后保存结束状态。成功返回需
存在 broker 用量回执，摘要写入 execution.json；命名运行完成时把该回执纳入
完成证据集合。状态快照可能包括同一共享对象的其他角色消耗，不能直接当成
单个任务成本。此路径拒绝 shared_chat_budget 与 resume 同时使用，避免以内存
中新建额度冒充已恢复额度。普通不使用共享预算的恢复流程保持不变。

## 持久化配额对象

`PersistentChatBudget.create(path, max_calls=..., output_budget=..., ttl_seconds=...)`
创建 SQLite 配额，已有文件拒绝覆盖。之后使用 `PersistentChatBudget(path)`
打开同一额度，可传给上面的 broker、adapter 和 owned-native Python API。
BEGIN IMMEDIATE 串行扣减，FULL 同步提交预留后才返回准入；每次操作显式关闭
连接。角色预留小计必须与剩余额度守恒。数据库丢失、损坏、不一致或过期时
拒绝请求，不能静默创建一份新预算。

重启和多个协作进程可以打开同一 SQLite 文件；调用失败、超时或预留后崩溃
均不退款。TTL 保存为墙钟到期时间，检测到时钟早于上次预留时拒绝。该对象
不防止恶意时钟调整、管理员复制旧数据库或绕过 broker，也不认证金额或输入
token。owned-native 仍拒绝 shared budget 与 resume 组合：额度持久化不等于
任务未知操作可安全重放，恢复执行协议尚未接入。

CLI 可显式创建和检查配额：

```bash
python -m praxile.eval.persistent_chat_budget create \
  --database /path/to/quota.sqlite --max-calls 40 \
  --output-budget 240000 --ttl-seconds 1800
python -m praxile.eval.persistent_chat_budget show --database /path/to/quota.sqlite
```

在已有 `python -m praxile.eval.owned_native_cli` 命令中追加
`--credential-file /path/to/private-key --shared-budget-db /path/to/quota.sqlite`。
默认仅输出计划和额度状态，不预留、不延期、不运行 Docker；加 `--execute`
才转发该数据库对象给执行器。缺失/损坏/耗尽/过期的库或无凭据、同时 resume
均在执行前拒绝；不会自动创建新配额。预检与实际请求间额度可能被其他进程
消耗，真正准入仍由 SQLite 预留事务决定。一次尝试失败后新尝试可使用新
run-id 并复用配额，但是否重试任务应由控制面审查，不能作为自动重放许可。

## 任务内机制隔离

runtime.task_local_memory_enabled 是运行时硬开关。关闭后 subtask_note 和
subtask_reflect 被拒绝，不注入 task_notes，也不调用反思模型；subtask_begin/end
仍可记录证据边界。轨迹 task_local_policy 保存设置，已有轨迹切换开关会要求
新建任务；带笔记的旧轨迹也不能直接在关闭状态继续。

普通项目默认仍开启。新的 native benchmark worker 默认关闭，可通过 worker
CLI 的 --task-local-memory 或 Python run_native 的 task_local_memory_enabled=True
显式开启；反思角色使用同一 benchmark provider/model，以便经过同一 broker。
结果保存 task_local_memory_enabled。native adapter 和 owned-native Python API
现在显式接受同名布尔参数；owned-native CLI 使用 --task-local-memory 开启，
默认关闭。该值进入远程 worker 命令、执行报告及命名 run 的冻结计划；旧计划
缺少此条件时不能直接作为新计划 resume，应创建新 run，不能回填历史证据。
adapter 同时核对 worker 顶层回执和 trajectory.task_local_policy，冲突或非法
类型拒绝。请求开启而缺少任一回执也拒绝；默认关闭时兼容旧 worker，但缺字段
只能标记 task_local_policy_verified=false，不能证明 frozen executor 隔离成立。
五组协调器尚未自动映射组设置；传参/回执测试不代表真实五组效果已验收。

## Owned-native 的冻结经验入口

owned-native CLI 可追加以下参数，把单条冻结 context candidate 传给既有
adapter/worker 激活通道：

```sh
--development-candidate /absolute/path/candidate.json \
--development-candidate-sha256 SHA256 \
--development-only --repository public/docs
```

Python API 对应 development_candidate_file、development_candidate_sha256、
development_only 和 repository。候选文件摘要、结构、scope/anti-scope 在
创建租约前校验；task.split 必须精确为 development。CLI 的 plan-only 同样
验证候选，但只输出身份摘要，不输出经验正文。候选身份纳入命名 run 的冻结
计划与执行报告，修改候选或 repository 不能复用原完成记录。

adapter 在上传前再次校验文件摘要，并冻结传输字节；worker 根据真实任务正文
进行 activation，不因传入候选就强制注入。不开启 task-local-memory 时可用于
“仅经验”的执行通道，同时开启可用于“完整 Praxile”的组合通道，但完整五组
调度、共同预算和效果验收仍未完成。此入口不允许持久化经验晋升，也不提供
held-out candidate 运行路径；不是证明开发/隐藏任务材料隔离的独立沙箱。

这是相对旧版 native worker 的行为变化。旧版 fresh_disabled 仅表示长期经验
检索关闭，不保证任务内笔记/反思关闭；旧结果不能未经审查直接当作新冻结
执行器对照。应冻结新 worker wheel/代码摘要并重新运行所需对照，不能追改
历史实验条件。该开关不阻止模型在普通思维或动作历史中自行反思，不等于
实现了完整任务内候选生成、探测与选择流程。
