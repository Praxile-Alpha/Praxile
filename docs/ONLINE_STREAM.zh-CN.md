# Online Stream 实验控制器

`praxile.eval.online_stream.OnlineStream` 是独立于 held-out 五组实验的顺序执行
Python API。任务必须声明 `split="online_stream"`，按创建时列表顺序运行，
不接受持久化工作区、resume、外部候选或实验协议覆盖参数。

## CLI

```sh
python -m praxile.eval.online_stream_cli create \
  --stream .praxile/eval/online/example \
  --task task-001.json --task task-002.json \
  --execution-config execution.json --repository public/docs

python -m praxile.eval.online_stream_cli status --stream .praxile/eval/online/example
python -m praxile.eval.online_stream_cli next --stream .praxile/eval/online/example
python -m praxile.eval.online_stream_cli next --stream .praxile/eval/online/example --execute

python -m praxile.eval.online_stream_cli accept \
  --stream .praxile/eval/online/example \
  --candidate candidate.json --candidate-sha256 "$CANDIDATE_SHA256"

python -m praxile.eval.online_stream_cli accept \
  --stream .praxile/eval/online/example \
  --candidate candidate.json --candidate-sha256 "$CANDIDATE_SHA256" \
  --approve --approved-by reviewer-id
```

create 只检查本地 worker/依赖并冻结计划，不读取凭据、不启动容器或模型；已有
目录拒绝覆盖。重复的 --task 参数决定顺序。next 默认只读预览，--execute 每次
只执行一个任务，退出码 0 表示控制器返回，不表示任务质量通过。accept 默认验证
候选并预览摘要，只有 --approve 和非空 --approved-by 同时存在才写批准事件。
候选文件摘要变化时拒绝批准。失败日志只显示错误类型，不打印正文、endpoint
或凭据路径；原始现场保留在实验目录。

execution.json 必须提供 wheel、wheel_sha256、model、base_url、credential_file；
可选 max_steps、timeout_seconds、model_request_options、task_local_memory_enabled、
dependency_wheels、required_capabilities。路径相对当前工作目录解析后冻结；API
同样校验 HTTPS endpoint（禁止内嵌凭据/查询参数）、镜像/worker 摘要和预算类型。
公共输入另用 --input-roots 指定 task ID 到输入目录的 JSON 映射。密钥本身仅放在
权限受限的凭据文件，不写 execution.json，也不作为命令行参数传递。

## 使用流程

```python
import asyncio
from pathlib import Path
from praxile.eval.online_stream import OnlineStream

stream = OnlineStream.create(
    Path(".praxile/eval/online/example"),
    tasks,  # 已冻结的 BenchmarkTask 列表，不能混入 heldout/development
    repository="public/docs",
    execution={
        "wheel": Path("worker.whl"),
        "wheel_sha256": worker_sha256,
        "model": model_name,
        "base_url": endpoint,
        "credential_file": Path("/private/credential.txt"),
        "max_steps": 30,
        "timeout_seconds": 600,
    },
    input_source_roots=public_input_roots,
)
asyncio.run(stream.run_next())

# 人工审查 candidate 字典后才可执行此调用。
stream.accept_candidate(candidate, approved=True, approved_by="reviewer-id")
asyncio.run(stream.run_next())
```

首个任务不加载长期经验。每个任务创建独立 lease，approved candidate 在后续
任务进入原生语义激活门控；未命中则 abstain。候选 source_task_ids 必须非空且
仅来自已完成前缀，禁止当前、未来和未记录任务。原始候选快照按每次 dispatch
落盘，后续批准不会改写之前运行。审批只改变实验内候选，不写生产 memory/skill，
也不授予长期 promotion。`approved_by` 是可信控制面传入的声明，不是身份认证。

## 候选生成

已完成任务后可从结构化证据生成草案：

```sh
python -m praxile.eval.online_stream_cli design \
  --stream .praxile/eval/online/example --project . \
  --design-id online-draft-001 --scope scope.json

python -m praxile.eval.online_stream_cli design \
  --stream .praxile/eval/online/example --project . \
  --design-id online-draft-001 --scope scope.json \
  --generate --max-output-tokens 4000 --request-timeout 60
```

默认仅预览证据数量和账本摘要，不加载模型配置、不请求模型。--generate 使用
项目配置的 proposal_composer 角色，最多一次生成请求、无自动修复重试；scope.json
必须明确 repositories、task_signals、does_not_apply_when 三个字符串数组。
需先确认这些工具执行事实可以发送给配置的模型。

生成输入来自已完成前缀，优先选择最近任务、最多 20 条结构化工具证据；原生内部
task ID 映射到受账本绑定的 benchmark task ID，事件 ID 保持原值。映射、来源
摘要、选中数及遗漏任务数保存在生成 manifest，不发送原始命令/输出、文档正文、
未来任务或私有评审材料。无可用证据时不调用模型，返回 NO_ENTRY；被判定为
held-out 的候选轨迹不能经该投影进入在线学习。

草案写入项目 `.praxile/eval/v2/candidate_designs/<design-id>/`，报告明确标记
online_stream 和 source_ledger_sha256。两个候选及组合仍是未经验证的草案，
不会自动批准、试用或继承其他版本成绩。审查其中一个候选后，计算其文件摘要，
再执行上面的 accept 命令。原生 worker 仍复核适用范围及 anti-scope。

加 --resume 只复用同一来源、配置和范围的已保存生成；缺少保存结果时拒绝重发。
后续任务或批准事件改变来源账本摘要时需新的 design-id。Python 对应接口为
`run_online_design(config, stream_directory, design_id=..., applies_to=...)`。

## 跨任务请求配额

create 可添加 `--chat-budget budget.json`，Python 对应 `chat_budget={...}`：

```json
{"max_calls": 200, "output_budget": 500000, "ttl_seconds": 86400, "request_byte_budget": 10000000}
```

request_byte_budget 可省略，其他三项必填。控制器独占创建 stream/quota.sqlite，
冻结上限和过期时间；后续任务的 executor、任务内 reflection、独立 judge 及在线 design 共用
这份持久化配额。每次请求发出前原子预留调用数、最大输出 token 和可选的实际
序列化请求字节数；超时不退还，重启不重置，数据库丢失或上限变更直接拒绝。
每个 broker 仍保留自身更小的调用/输出限制。status 显示余额、角色预留及有效期。

在线 design 的 proposal_composer 使用共享配额的无传输重试通道，适配
OpenAI-compatible 和 Anthropic provider；路由若回退，每次实际请求均重新预留。
缺少显式 max_tokens 或不支持的 provider 拒绝执行，未启用共享配额时行为不变。
候选设计 manifest 记录请求准入策略，trajectory 保存配额开始/结束快照。
恢复已生成结果不额外调用模型；未知请求结果仍不可自动重发。
在线 compose 也使用该准入通道，其独立重跑共用 stream 配额。
预算 scope 为 executor_reflection_judge_online_design_composition；其他离线演化工具或
绕过上述入口的外部调用不在这一份配额内。
output_budget 是最大输出预留量，不是实际消耗；request_byte_budget 不是输入 token。
不保证供应商金额、全流程 token 或墙钟上限，不应称为完整费用控制。配额依赖
可信本地控制器，不能防御拥有宿主文件权限的用户恢复旧数据库或绕过代理。
没有配置该选项的既有 stream 保持原行为，judge 使用单次评审配额。

## 已完成前缀的隔离 Probe

```sh
python -m praxile.eval.online_stream_cli probe \
  --stream .praxile/eval/online/example --task-index 0 \
  --candidate first.json --candidate-sha256 <FIRST_SHA256> \
  --candidate second.json --candidate-sha256 <SECOND_SHA256> \
  --output .praxile/eval/online-probes/example
```

默认只预检；增加 --execute 才执行。可重复 task-index，最多 5 个，必须都已完成。
两个父候选必须不同、有假设，来源只能属于完整已完成前缀。每个选中任务分别以
baseline、first、second 三个独立 lease 重跑，共享原 stream 的执行配置与可选配额。
这是已见任务的 development replay，明确标识 online_prefix_development_probe，
不是新的 held-out/online 成绩，也不推进正式任务序号或批准候选。

manifest 冻结来源账本及任务/候选，report 每次 dispatch 前持久化未知状态；
中断后不自动重放。报告绑定执行与轨迹摘要，投影结构化工具证据，不含原始
命令、正文或隐藏 rubric；控制器不调用 evaluator。stream 中途前进时拒绝继续。
所有动作仍由 owned native 环境执行，检查输入摘要及隔离生命周期。

本阶段 `executions_returned_not_scored` 只证明执行器返回且清理完成，不证明任务成功。
候选暴露会核对执行身份、worker 激活/注入、runtime 注入标志及冻结正文/摘要。
只有全部一致且实际记录注入时 candidate_exposure_verified 才为 true；缺失证据
保持 false，互相矛盾直接拒绝。机制报告标记 scope=bound_runtime_prompt_construction，
remote_consumption_verified=false；它不是远端消费证明或因果效果证明。
可以基于下面的反馈回放调用组合并独立重跑；仍没有任务级 helped/hurt 或因果
提升结论，不能继承父候选的效果。

### Probe 反馈回放

```sh
python -m praxile.eval.online_stream_cli probe-feedback \
  --stream .praxile/eval/online/example --probe-directory .praxile/eval/online-probes/example \
  --manifest-sha256 <MANIFEST_SHA256> --report-sha256 <REPORT_SHA256>
```

离线复核冻结前缀、完整三组顺序、模型/镜像/worker/请求选项/执行预算等记录、
每份报告和轨迹摘要及候选暴露。stream 已前进则拒绝使用旧反馈继续该轮实验。
从绑定轨迹重建操作事件，只按精确操作身份对齐，不按步号配对，不把同参调用
视为相同环境状态。支持候选 expected_effect.measurement 中预冻结的
repeated_exact_operations 测量；没有测量或覆盖不足时 abstain。

输出 parent_reports 可供后续组合器使用，保留行为差值与事件引用，但不含正文、
原始命令、hidden judge 或 rubric。任务 verdict 始终 confounded，原因包括没有
任务结果评测及单次执行对不证明状态等价；注入缺失时 prediction_verified=false。
该回放只检查可信本地文件的一致性，不是外部签名。

### 组合并独立重跑

```sh
python -m praxile.eval.online_stream_cli compose \
  --stream .praxile/eval/online/example --probe-directory .praxile/eval/online-probes/example \
  --manifest-sha256 <MANIFEST_SHA256> --report-sha256 <REPORT_SHA256> \
  --project . --composition-id combined-001 --scope scope.json \
  --output .praxile/eval/online-compositions/example
```

默认仅审计反馈；加 --execute 才配置 proposal_composer、调用模型并可能启动新的
执行环境。scope.json 与 design 的显式适用范围格式相同。组合目录必须新建，
位于 stream 和原 probe 之外。模型身份、父候选、源摘要和范围先持久化，发送前
记录 request-intent；结果与模型成本轨迹先保存，再开始重跑。

组合器只接收允许的父候选与局部行为反馈，不接收隐藏 judge/rubric，允许 abstain。
若生成组合候选，来源仍必须属于冻结已完成前缀，随后对每个 probe 任务启动新的
baseline/组合两个 lease；不复用父候选输出，不把已见任务改称 held-out。
超时/中断的同目录执行禁止重放；没有自动恢复或自动选择表现最好的一次。

结果标为 composition_abstained 或 composition_rerun_not_scored。后者只证明重跑
返回且执行记录可用，任务质量、迁移效果尚未评测；两者 decision 均为 KEEP_BASE，
candidate_adopted=false、promotion_eligible=false。正式在线候选更新仍需用户审批。

### 组合来源链审计

```sh
python -m praxile.eval.online_stream_cli audit-composition \
  --stream .praxile/eval/online/example --probe-directory .praxile/eval/online-probes/example \
  --composition-directory .praxile/eval/online-compositions/example \
  --manifest-sha256 <COMPOSITION_MANIFEST_SHA256> --report-sha256 <COMPOSITION_REPORT_SHA256>
```

先重做父 probe 审计，再核对 feedback、组合候选和模型轨迹的摘要；重建组合
payload 并核验 input_sha256，检查父子来源、允许的证据引用及能力范围。
对生成组合的情况，必须存在新的 baseline/组合两条完整执行记录，并复核
执行条件、候选暴露及行为测量。没有完整证据或处于 outcome_unknown 时拒绝，
abstain 可以单独审计但不能伪装成组合重跑。既有缺少这些摘要的记录不能直接
声称通过新审计。调用者须固定摘要；不提供外部签名或供应商响应真实性证明。

输出仍为 KEEP_BASE、semantic_accuracy_verified=false；来源链完整不等于质量
或迁移效果通过。本接口不调用模型、不写回在线状态。

### 组合产物独立评审

```sh
python -m praxile.eval.online_stream_cli review-composition \
  --stream .praxile/eval/online/example --task-index 0 \
  --probe-directory .praxile/eval/online-probes/example \
  --composition-directory .praxile/eval/online-compositions/example \
  --manifest-sha256 <COMPOSITION_MANIFEST_SHA256> --report-sha256 <COMPOSITION_REPORT_SHA256> \
  --judge-config judge.json --output .praxile/eval/composition-reviews/example
```

默认离线预检；加 --execute 才将两侧产物分别发送给独立 Judge。先完成组合来源
链审计，并预检 baseline/组合两侧全部输入输出摘要，再开始任何评审调用。
judge.json 格式同独立内容评审，artifacts 只包含选中任务；已预冻结的 stream
必须匹配原 Judge 身份，未预冻结的情况保持 post-hoc 标记。

评审目录和 evaluator 必须在 stream、probe、composition 之外。两侧使用同一
评审配置、预算与抽取规则，各自绑定执行报告；共享 stream 配额（若已配置）。
父 report 记录子 receipt 摘要；失败或中断保留未知，目录禁止自动重放。
评审文件不进入组合反馈、不批准候选、不改写正式在线账本。输出仍 KEEP_BASE，
LLM 标签不是官方分数或校准真值；成对结果的独立审计与效果选择仍待完成。

## 独立内容评审

已完成任务可单独评审，不影响后续经验选择：

```sh
python -m praxile.eval.online_stream_cli review \
  --stream .praxile/eval/online/example --task-index 0 \
  --judge-config judge.json --output .praxile/eval/online-reviews/example-task-0
```

默认仅离线预检；添加 --execute 才发送材料给 judge。task-index 从 0 开始，
不能选择未完成任务。judge.json 格式与五组 judge 配置一致，artifacts 只包含
这一次待评审任务：credential_file、endpoint（HTTPS /chat/completions）、model、
total_output_tokens、public=true、evaluator_root、artifacts 均必填；可选
batch_size、timeout_per_call、request_options。artifacts 内指定冻结的 source_path、
output_path 以及可选 text/docx_body/pdf_text 抽取格式。私有 evaluator 必须为任务
中冻结的 praxile-content-review v1 requirements，不能随意换成未知官方评分器。

评审目录与 evaluator 文件须在 stream 目录之外。控制器复核执行报告、输入、
输出与轨迹摘要，使用独立 broker 和调用/输出额度；receipt.json 绑定任务序号、
完成事件摘要、执行证据、judge 配置身份及全部评审产物摘要。目录只允许创建
一次，失败后不静默重试；传输失败可表示为 abstained，未知 token 不补零。

CLI 输出 preserved/missing/contradicted/uncertain 标签计数。它们是未校准的 LLM
内容保留判断，不是任务成功率。默认 judge 在任务结束后配置，明确记录
judge_precommitted_before_execution=false，不能据此声称冻结对照或性能提升。
这份评审不会写入 stream 账本、自动批准候选或进入 design 的工具证据。
Python 接口为 `run_online_review(stream_directory, index, judge=..., directory=...)`。

创建 stream 时可增加 `--judge-config judge.json`，或调用
`OnlineStream.create(..., judge=judge_config)`，在任何任务 dispatch 前冻结评审身份。
创建阶段 artifacts 必须覆盖所有任务，评审某个任务时配置只包含该任务。
冻结内容包括模型、endpoint 摘要、请求选项、调用超时、batch size、输出额度、
抽取路径/格式和 evaluator 摘要；评审时任何身份变化都会在调用模型前拒绝。
凭据内容及 evaluator 正文不会写入 stream；凭据文件可轮换。status 和评审 receipt
报告 judge_precommitted_before_execution=true。既有流不能补写这一声明。
这是可信本地控制器的配置承诺，不是签名时间戳，也不证明模型供应商版本未变。
独立评审仍不进入学习链路，不自动 promotion；预冻结不等于质量校准或官方评分。

### 离线审计

保留 receipt.json 的 SHA-256 后，可独立复核，不读取密钥或调用模型：

```sh
python -m praxile.eval.online_stream_cli audit-review \
  --stream .praxile/eval/online/example --task-index 0 \
  --judge-config judge.json --output .praxile/eval/online-reviews/example-task-0 \
  --receipt-sha256 <64位SHA256>
```

审计重新检查 stream 完成事件、原始产物、evaluator、评审输入、manifest、
报告、trajectory 和各文件摘要；重新生成 batch plan，核对标签计数，拒绝跨任务
或跨模型结果。`complete_judge_labels` 只表示标签完整且没有 uncertain，
不是任务成功；abstained/uncertain 返回 unknown。缺失证据直接拒绝，不能补零。
broker usage 文件校验摘要，但本接口不证明供应商计费准确。
调用者固定的 receipt 摘要与本地文件一致性不等于外部签名或语义正确性，
结果始终保留 semantic_accuracy_verified=false、promotion_eligible=false。

### 在线流汇总

```sh
python -m praxile.eval.online_stream_cli summary \
  --stream .praxile/eval/online/example --reviews reviews.json
```

reviews.json 格式为 `{"reviews": [...]}`，列表每项包含 task_index、directory（评审目录）、
receipt_sha256 和 judge（该任务完整 judge 配置对象，密钥仍只用文件路径）。
空列表合法，表示尚未提供评审。该命令逐项执行上面的独立审计，不调用模型；
重复任务序号、未完成任务的评审、不同模型/预算的 judge 协议混用、执行中或
汇总期间发生变化的 stream 均拒绝。未提供的评审不会被自动发现或偷偷挑选。

输出按冻结顺序覆盖全部计划任务：complete_judge_labels、unknown、missing_review、
not_executed。每个标签完整任务先计算 preserved/requirement_count，再做任务等权
平均，避免 rubric 条目多的任务获得更大权重。observed_task_mean 仅描述完整子集，
同时输出覆盖率及 unknown_or_unexecuted_tasks。planned_task_mean_bounds 将其余任务
分别按 0 和 1 给出缺失结果上下界；它不是置信区间，也不假设在线任务相互独立。
例如四个计划任务只有两个完整标签分数 0.5、1，观察均值为 0.75，覆盖率 50%，
计划任务均值的缺失界为 [0.375, 0.875]，不能把它写成全任务成功率 75%。
本接口不估计因果提升、完整成本或官方分数，不与 held-out 分数合并。

## 顺序账本和中断

stream.json 保存冻结任务、执行配置及哈希关联的事件链：dispatch、completed、
approved_candidate、outcome_unknown。dispatch 必须先完成文件 fsync、原子替换
及目录 fsync，再启动执行；平台不支持这些持久化操作时失败，不绕过。网络调用
期间不持有文件锁，但 pending 状态阻止另一个调用抢占。账本最大 16 MiB，超限
在继续执行前拒绝；不是无限期 7x24 服务。

重新构造 `OnlineStream(directory)` 会复核历史报告和 worker 轨迹摘要。执行
返回且 cleanup_verified 才记录 completed；这表示执行结束，不表示任务成功。
超时、取消、进程被 kill 或证据写入不完整时不能自动重放，也不能跳过前缀继续
更新经验。本阶段没有人工恢复未知执行的命令，需保留现场并另建明确的新实验。

文件系统所有者及控制面是信任边界：哈希链能检查内部一致性，不防管理员重写
整个链；历史材料需另行保存外部摘要以获得更强认证。报告/轨迹被修改会阻止
后续运行，但控制器不是对所有产物和供应商账单的完整独立审计。

## 协议隔离与未完成项

候选经原生 adapter/worker 输出 online_stream_candidate，CONTEXT_ACTIVATION /
CONTEXT_INJECT 保留 online_stream。控制面记录完整已完成前缀，但 adapter 只
接收模式和候选，不接收全体任务或私有 evaluator。五组 planner 不接受此 split，
不能把在线结果混入冻结 held-out 分析。

目前已接通显式 design、probe、反馈审计、compose 与组合独立重跑，更新正式在线
候选仍需人工批准。独立内容评审、运行前冻结身份、在线描述性汇总与上述调用的
共享准入预算、组合来源链审计及成对独立内容评审已接入；尚缺成对评审结果审计、
效果选择与真实模型多任务验收，LLM 标签也尚未证明质量校准。
测试以替身执行器验证顺序、审批、源限制、证据变化、重启、取消及并发拒绝；
Harbor 兼容测试验证模式透传，不等于真实模型收益。不要把本阶段称为 R3 完成。
