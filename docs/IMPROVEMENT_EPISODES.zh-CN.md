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

#### 原生 Harbor 开发证据入口

`praxile.eval.v2.native_development_evidence` 支持直接从原生 worker 的
`praxile-result.json` 提取开发证据，不需要伪造旧版 A/B manifest。

```sh
python -m praxile.eval.v2.native_development_evidence \
  --result /absolute/trial/agent/praxile-result.json \
  --sha256 SOURCE_RESULT_SHA256 \
  --development-task-id NATIVE_TRAJECTORY_TASK_ID \
  --heldout-task-id EXCLUDED_NATIVE_TASK_ID \
  --track development \
  --output /absolute/new-development-evidence.json
```

任务 ID 是 trajectory 的原生 `task_id`，不是 benchmark 名称；调用方必须先建立
开发/held-out 划分，不能将看过评分的 held-out 任务改名后作为开发来源。
当前划分由调用方显式声明，并非来自独立签名或不可变 registry，因此输出始终
标记 `provenance_attested=false`。不要将该入口描述为物理隔离或来源认证。

入口校验原始字节 SHA256、稳定 task/start_time、任务归属及原生事件身份。
复用操作 identity、文档修订/布局白名单和依赖闭包选择，最多输出 20 条证据；
只传递结构化工具状态、退出码、参数摘要和允许的文档指标，不读取相邻 verifier
文件，不导出原始命令、输出正文、官方分数或 rubric。CLI 不调用模型，不写经验库，
输出为 0600 文件且拒绝覆盖。`design_from_native(...)` 可显式调用既有候选设计器；
无可用证据时直接 NO_ENTRY，不发起模型请求。设计调用的持久化与模型配置仍由调用方负责。

真实 Office 留存验收轨迹离线投影得到 3 条证据（2 次命令 blocked、1 次 success），
源摘要 `351865f7c0cf274d95d72ad526e4f877d1d0388331e42a97f84d8641be248ad6`。
没有调用模型，未读取私有失败细节，也未生成或晋升候选。它们不足以证明错误根因、
修复有效性或跨任务迁移。下一阶段仍需更细的公开工具证据和独立受控实验。

投影 v2 增补：原生与既有 A/B 入口共享 `read_document` 和 `check_deliverables`
结构化投影。文档读取仅输出 source SHA256、单位类型、总量、读取起点/数量及下一页，
不输出文件名、sheet 名或单元格/段落内容。公开交付检查记录 contract digest、
`run_argument`/`model_proposed` 绑定和 passed/failed/unsupported/optional_missing 数量；
`user_acceptance_verified` 与 `semantic_quality_verified` 始终为 false。
Shell 的 `returncode` 与旧适配器 `exit_code` 均可读取；冲突或类型错误保留未知。

同一真实 Office 轨迹重新离线投影得到 7 条证据：3 次文档读取、3 次命令结果、
1 次公开交付检查。该公开检查使用模型提出的契约，返回一个 unsupported 输出，
不能称为内容错误、用户拒绝或官方失败；Shell 成功项退出码为 0。未重新执行任务，
未调用模型，未修改原轨迹或历史评分，也未生成候选。新 bundle 标记
`projection_version=2`，旧 bundle 不覆盖。证据覆盖增加不等于经验有效性提升。

M3.1 Flash 真实设计验收（2026-10-19）：仅发送上述 7 条公开证据及声明的工具接口，
默认 thinking、4000 输出 token/60 秒上限。请求报告 2105 input、4000 output，
其中 reasoning_tokens=4000，最终正文为空、finish_reason=length；系统拒绝截断，
没有候选。另一次独立 `thinking=disabled` 配置验证被 API 以 HTTP 400 拒绝：
该模型要求 adaptive thinking，不允许 disabled/effort none。两份记录均保留，
不覆盖旧失败，不把它们算作任务 A/B，也不推断 NO_ENTRY 或证据质量。
见 [脱敏验收记录](results/office-flash-candidate-design-20261019.json)。

`design_development_candidates(..., max_output_tokens=4000)` 现允许调用方显式传入
256..16384 的整数预算，默认不变，并记录请求预算、reasoning token 数、正文长度
和截断诊断。它不会自动增加预算、切模型或重试；调整预算应成为新实验计划的一部分。
CLI `eval design-context` 现支持 `--max-output-tokens 8000`。显式预算产生
`praxile.candidate_design_run.v2` manifest，冻结输出上限与 60 秒超时；相同预算
resume 可重建已生成文件而不重复请求，变更预算（包括改回隐式默认）拒绝恢复，
必须使用新 design ID。未指定参数时保留 v1 manifest 形态与隐式 4000，兼容旧计划，
不迁移或覆写历史 manifest。这不改变 worker 或执行器角色的预算。模型请求选项
必须按 provider 实际支持配置，M3.1 Flash 不应使用已被实测拒绝的 thinking=disabled。

超时口径：这里的 60 秒是每次 HTTP 请求超时，不是整个设计流程的墙钟硬截止。
默认 model.max_retries=1 可能在网络故障后重试一次；设计器自身不循环生成，
并不等于传输层零重试。需要单请求实验时，在实验配置明确设置
`model.max_retries=0`（并检查 provider 覆盖）。不能仅凭一次 router 调用宣称
API 只被调用一次或最终供应商只计费一次。

后续独立 8000 输出 token 验证仍使用默认 adaptive thinking，最终读取超时，
无完整响应、无候选，输入/输出 token 与费用均未知。该次继承了一次传输重试，
不能宣称 60 秒总截止或严格单请求。未继续扩大预算；后续须明确冻结传输策略并
记录尝试次数，再评估 provider 支持的推理控制，而非将超时当作模型弃权。

非流式传输观测增补：urllib/httpx 的 `post_json` 返回值以不改变 JSON 正文的属性
携带 `praxile.http_attempts.v1`，失败异常也携带该记录。字段包括配置重试上限、
尝试次数、每次 success/timeout/http_error/unavailable/decode_error、HTTP 状态码
及耗时；不记录 URL、headers、请求正文、异常正文或密钥。两种 provider 将记录
传到候选设计轨迹；失败时保存最后 provider 的异常链，不把它称为全部 fallback
路由的汇总。重试后成功也保留 `failed_attempt_usage_unknown=true`，最终响应的
usage 不能覆盖先前失败尝试可能产生的供应商消耗。

此观测暂不覆盖流式请求和取消期间的完整证据，不能恢复历史请求的尝试明细。
单次 socket 超时和 retry backoff 语义未改，尚未实现总墙钟硬截止或远端计费取消。
验证使用可控传输替身，无新增模型调用；真实超时实验不能追溯补填成已完整观测。

流式增补：urllib/httpx 在首个解析事件交付给调用方之后，遇到网络错误不再自动
重试整个请求，避免重复文本或重复结构化动作。首个事件之前的失败仍遵循原配置。
流式异常携带尝试记录、`events_delivered` 和 `automatic_replay_forbidden`，
记录 scope 为 `streaming_transport_failure`，不包含 SSE 正文。读取事件边界的
取消会记录 cancelled 并关闭响应；调用方显式关闭生成器也关闭响应。
成功结束的流暂不提供汇总收据，取消发生在首个尝试之前或退避期间也未覆盖完整记录；
阻塞读取时仍依赖底层超时，不是即时取消或总墙钟截止。该行为由两套可控传输替身
验证，不等于外部 provider 的实际计费或远端请求终止证明。

实验进程截止（POSIX）：已有 worker supervisor 也可显式包裹独立设计脚本，
将模型连接、传输重试及路由 fallback 一起限制在同一个本地进程组的预算内：

```bash
python -m praxile.eval.process_supervisor \
  --timeout 120 --status /private/tmp/design-supervision.json \
  --cancel-file /private/tmp/design-cancel \
  -- python /absolute/path/to/isolated_design.py
```

路径是示例，实际运行使用新的实验目录；取消文件存在时不启动 worker。
截止从监督器开始计时，不在每次 HTTP 重试后重置；到期返回 124，取消返回 143。
监督器杀死所属进程组并 wait 回收直接子进程，写入 `worker_reaped` 和包含清理的
`elapsed_seconds`。这不是硬实时保证，进程创建、内核调度和回收仍可能增加延迟。
它不执行模型调用、不自动重试，也不改变普通 `router.chat()` 的默认行为。
已有容器 worker 使用该机制；普通候选设计 CLI 尚未自动包裹监督器。

真实回环 HTTP 测试覆盖 urllib/httpx 的阻塞读取、总截止和取消：客户端自身超时
设为 60 秒，外层 2 秒终止，服务端实际观察连接关闭，并验证子进程已回收。
与原监督器测试合计 10 项通过，未使用付费 API。测试不证明远端生成或计费停止，
因此收据始终保留 `remote_request_cancelled=null`、`remote_usage_known=false`。
强制终止不会执行 worker 的 finally，内存中的 trajectory/usage 可能未落盘；
监督收据只证明本地生命周期，不得补写为零费用、模型弃权或成功候选。

候选设计 CLI 的请求前 checkpoint：`run_candidate_design` 在进入生成器之前先
原子写入 `request-intent.json` 和初始 trajectory。意图记录为 `outcome_unknown`，
`request_dispatched=null`、`remote_usage_known=false`；这不是服务端收件证明。
checkpoint 写入失败时不进入生成器。意图记录保持原样，最终结果以独立的
`generated.json` 为准，不将它的未知字段用于覆盖后续实际响应的 usage。

`resume` 仅在已有 `generated.json` 时恢复派生文件而不重新请求模型。已有 manifest
但没有完整生成结果时（包括旧版本没有意图记录的设计），只要有待发送的开发证据，
即拒绝自动重发并要求新 design ID。它可能保守拒绝实际上尚未发出的请求，避免在
未知结果下重复消费。没有证据的 NO_ENTRY 路径仍可恢复，不产生请求意图。
这不阻止一次 router 调用内部配置的 retry/fallback，后者仍需独立冻结预算。

真实进程测试在生成器阻塞期间由 supervisor 强制终止，然后启动新进程尝试 resume：
意图及初始轨迹可读、无 generated 文件、恢复被拒绝，生成器进入次数保持 1。
不模拟付费 API，也不宣称恢复了被杀进程的内存响应。原子文件替换覆盖进程中断，
未提供断电 fsync 保证。完整响应恢复与所有入口统一接入仍未完成。

原生 benchmark 现在可以使用同一持久化设计入口，而不必编写直接调用生成器的脚本：

```bash
praxile eval design-native-context /absolute/path/to/native-result.json \
  --source-sha256 SOURCE_SHA256 --track development \
  --design-id native-design-001 --scope scope.json \
  --development-task-id NATIVE_DEVELOPMENT_TASK_ID \
  --heldout-task-id EXCLUDED_HELDOUT_TASK_ID \
  --max-output-tokens 8000 --json
```

大写项须替换为实际冻结材料的摘要和成员 ID，不要为满足参数虚构 held-out 成员。
支持重复声明成员，`--executor-profile` 可选，使用已配置的 `proposal_composer`。
该入口仅接受 development；校验原生文件摘要及成员后投影允许字段，不读取相邻
评审文件，不将原始 command/stdout 或评分详情发给设计模型。成员声明仍不是
独立 split 认证。源摘要、投影版本、预算、模型配置身份与作用域进入同一设计 manifest。

`run_native_candidate_design` 与原 `run_candidate_design` 共享请求意图、锁、轨迹、
生成结果落盘和恢复逻辑，输出仍位于 `eval/v2/candidate_designs/<design-id>`。
新入口不依赖旧 SWE A/B experiment/EventStore；旧命令的 manifest 和参数保持兼容。
`--resume` 不会重发未知请求；改变源文件（即使提供新匹配摘要）须使用新 design ID。
不会启动实验或晋升资产，也不会自动启用进程截止；需要总截止时继续显式包裹 supervisor。
低层 `design_from_native` 保留供内存调用，其调用方仍需自己管理持久化，历史临时脚本
不会因新增 CLI 被自动迁移。已完成用真实投影及设计解析器的离线入口测试，尚未通过
此新 CLI 重新进行付费模型验收。

后续真实持久化入口验收：在 `6d89601` 使用 `run_native_candidate_design`，固定同一
7 条公开 Office 证据、M3.1 Flash、8000 输出上限、零传输重试、无 fallback 和
120 秒外层截止。一次 HTTP 尝试在 60.124 秒读取超时，进程 60.316 秒失败退出并
被回收；请求意图、manifest、失败轨迹都保留。没有候选，usage/费用未知，不记作
模型 NO_ENTRY。外层未到截止，所以本次不是强杀测试。公开摘要见
`results/office-flash-durable-design-20261019.json`。失败后未追加付费请求。

为使推理模型的等待预算可显式调整，两种设计 CLI 新增 `--request-timeout 120`
（整数 1..300 秒）。省略仍为 60；底层生成器参数为 `request_timeout_seconds`。
显式值进入 v2 manifest 的 generation_budget.timeout_seconds，并传到实际 router
调用与轨迹；resume 改变预算拒绝，须新 design ID。旧隐式 v1 以及已显式设置输出
预算的 v2 manifest 字段不变。该选项仍是单次请求超时，不是总截止，也不会关闭
内部重试。需要总截止仍应使用 supervisor，例如单请求 120 秒、进程 150 秒；
这是下一次实验配置示例，并非已经完成更长预算的验收。

120 秒预算的后续实测（`ed38302`）：新的 `office-flash-durable-002` 使用同一
7 条证据、8000 输出上限、零重试、无 fallback、150 秒进程截止。HTTP 单次响应
耗时 87.560 秒，报告 2105 input / 8000 output，其中 8000 reasoning，最终正文
为空且 finish_reason=length。设计器拒绝截断响应，进程退出码 1，未生成候选，
未启动 probe/晋升。费用未知，不能把本次已知 usage 用于补全先前超时请求的费用。
见 `results/office-flash-durable-design-120s-20261019.json`；本轮没有第二次付费请求。

2026-10-19 核对 [MiniMax 官方调用文档](https://platform.minimax.io/docs/guides/text-generation)：
M3.1 Flash 推理不可关闭；省略 effort 默认 max，OpenAI-compatible 协议使用
`reasoning_effort`，接受 low/medium/high/xhigh/max。降低推理开销应显式配置强度，
不应发送已实测被拒绝的 thinking=disabled。下列只是已有 provider 的角色配置片段，
不是通用默认，也不是已完成 low 强度实测：

```json
{
  "model_roles": {
    "proposal_composer": {
      "provider": "minimax",
      "model": "MiniMax-M3.1-Flash",
      "request_options": {"reasoning_effort": "low"}
    }
  }
}
```

Praxile 原先支持 low/medium/high，本轮补齐 xhigh/max 的有界枚举；不自动改变任何
角色配置，不把同一参数跨不兼容 provider 降级。测试验证参数进入请求正文，且
不覆盖消息或 token 预算。参数通过本地校验不代表任意 provider/model 都支持；
采用新 effort 必须冻结新设计配置与 ID。官方默认 max 与本次推理耗尽现象一致，
但尚未通过对照证明因果，也没有 low 强度生成有效候选的实测证据。

low 强度独立实测（`b3fed9d`）：相同证据与预算、零重试，14.587 秒返回完整
NO_ENTRY；2105 input / 1243 output，其中 1195 reasoning。模型自报证据不足，
缺 action_parameters / before_after_observations / public_content_constraints，
verified=false。生成/报告文件正常保留，未生成候选，不是任务完成率提升。
见 `results/office-flash-low-design-20261019.json`。这次没有追加第二次模型请求。

### 显式公开任务上下文

原生白名单投影不包含原始命令/输出或任务目标；为补足公开要求，新增可选参数
`--public-task-context public-task.json --public-task-context-sha256 <SHA256>`。
两者必须同时提供。JSON 仅接受以下字段（示例不是官方任务定义）：

```json
{
  "schema_version": "praxile.public_task_context.v1",
  "visibility": "public_task",
  "task_id": "actual-native-development-task-id",
  "goal": "Split the supplied workbook into the requested sheets",
  "constraints": ["Preserve the input workbook", "Write the requested output workbook"]
}
```

文件须无符号链接、JSON 无重复字段，至多 64 KiB；goal 至多 4000 字符，constraints
为 1..20 条、每条至多 1000 字符。task_id 必须等于原生来源的开发任务且不属于
held-out。摘要和内容进入冻结 manifest，变更或删除已冻结上下文不能复用旧设计。
不扫描相邻目录、不从 evaluator/rubric 自动生成、不修改旧轨迹。空证据仍不会
仅凭任务要求触发设计请求。新增字段只在显式提供时进入模型上下文，不新增事件
引用、不赋予执行成功或来源认证；提示词明确它是未信任、未验证的要求。

`visibility=public_task` 是调用方声明，schema 无法判断自然语言是否暗含私有评分
标准或错误要求。调用方必须选择真正公开的任务材料，不得把 held-out 题目/答案/
rubric 填入 goal 或 constraints。该机制不是物理信息隔离证明；权限隔离仍应由
独立执行环境承担。低层设计 API 同样验证字段与开发成员。动作参数与前后状态的
更细证据仍未补齐，此公开上下文入口尚未做真实模型质量验证，不据此勾选完整 R2。

### 文档读取参数与版本观察

原生与旧实验投影统一升级为 projection_version=3。read_document 现在明确保留
已记录的 requested_limit / requested_char_offset，未显式记录的值为 null，不
根据当前工具默认值补写历史。参数摘要加入 char_offset，避免不同字符分页被
误算为同一参数重复操作；非法分页值或截断路径不进入这一结构化投影。

连续成功读取按 task_id + trace_id + 原始记录路径匹配，仅导出前次读取事件 ID、
前次内容摘要，以及 same_bytes / different_bytes / unknown。不导出路径、sheet
名称或正文。相同路径的两个哈希只描述观察到的字节版本，不能推出执行者、具体
修改、前后内容语义或效果；causal_effect_verified=false、state_equivalence=unknown。
不同路径、不同 trace 或不同任务不建立链接。证据截取把 previous_read_event_id
纳入同任务依赖闭包，预算不足时不输出脱离来源的比较结论。

固定 Office 结果 `351865f7...` 的离线回放仍生成 7 条证据：读取参数 limit 依次为
5、20、3；同一输入的第二次读取与第一次同哈希，输出读取没有同路径前次观察，
保持 unknown。这不是输入到输出的因果转换证据，也没有追加模型调用。现有历史
设计 manifest 不改写；投影版本变化需要新 design ID，不能把 v3 塞进旧冻结设计。
任意 shell 的动作参数与文件修改归因仍未补齐，完整语义前后状态对齐仍未实现。

公开上下文 + v3 实测（`bca6326`）：从官方公开 instruction 分段保留目标及三个
sheet/输出路径约束，分别冻结原文件和上下文摘要，未打开 rubric。一次 M3.1 Flash
low 请求在 15.618 秒返回 NO_ENTRY；2517 input / 1409 output，1383 reasoning。
模型自报缺 public_content_constraints 和 action_parameters。前一项不能被当作
确定事实：公开约束已经传入，但其充分性未被证明。见
`results/office-flash-public-context-design-20261019.json`。投影版本与上下文同时变化，
不能称为单因素消融；无候选、无 probe、无晋升，本次无第二个模型请求。

由此增加确定性的 `praxile.design_input_inventory.v1`：记录 evidence_count、
public_task_context_supplied、executor_profile_supplied，始终保留
evidence_sufficiency_verified=false。它只记录验证后实际输入的存在性，不记录
正文，也不覆盖模型的原始弃权解释。不能将“存在”当成“足够”，更不能根据模型
自报缺失自动补取私有材料。本字段从新运行开始记录，历史轨迹不回填。
这一来源的重复设计请求暂止，后续应先解决动作语义证据或选择有独立验证的
新开发 Episode，而非对同一组稀疏状态信号继续增加模型调用次数。

### 修订动作的结构证据

projection_version=4 为成功且版本匹配的 revise_docx 增加 replacement_structure。
先通过现有 validate_docx_spec 校验，随后只统计请求中的布局枚举、各类型 block
数量、文本字符数、表格总行数/单元格数/最大列数。正文、单元格值、文件名不导出。
它描述 replacement_spec_only，不证明渲染结果或内容保持。未显式提供 layout 时为
null；不符合现有 schema 的历史 spec 只保留已成立的字节版本证据，结构为 null。

真实 seeded compact repair 来源 `2a3049dc...` 离线重放得到 10 条投影证据。
四次修订均显式 compact，表格始终 15 行、6 列、90 单元格；文本字符数依次为
4783、3954、3232、2494，heading 数为 3、3、1、1，paragraph 数为 10、4、4、4。
既有独立布局记录仍均为两页，因此只能证明这些内容结构与输出页数被观察到，
不能说 compact 无效、语义完整或原因已确定。它是 seeded repair，不是原始
benchmark 成绩，也不是 held-out 样本。此次没有调用模型或改变源产物。

该升级为后续候选设计提供可审计的动作信息，但仍不输出正文，也未自动生成
“应该删哪段内容”的经验。原 manifest 不改写；投影版本变化使用新 design ID。

修订来源的真实设计（`5602042`）：10 条 v4 证据、显式公开修订目标、原 v1 工具
profile，一次 M3.1 Flash low 请求在 19.840 秒返回完整 9000 字符 JSON；4123 input /
2858 output，其中 726 reasoning。第一个候选在 action_examples 校验失败，未发布
任何候选，也未执行 probe。现有轨迹只记录阶段，无法追溯具体违规字段；不能猜测
本次一定是分页范围或 SHA 填写错误。见 `results/docx-repair-flash-design-20261019.json`。
这是 seeded 修订开发任务，不是原始 benchmark，未发送 rubric，未追加修复请求。

### 能力 Profile V2 与拒绝诊断

新增 opt-in `praxile.executor_action_profile.v2`，其余字段仍为 executor/version/actions。
v1 输出字段与 digest 构造保持不变，旧候选不被隐式迁移。v2 的 action_constraints
仅为声明的动作增加已支持的 read_document 数值范围、check_document_layout 页数
范围和 revise_docx SHA 格式/刷新要求。它不授予权限，constraints_complete=false，
并不替代 ActionSchemaRegistry、文档校验或运行时安全检查。缺少范围提示是代码
审计发现，不是此次真实错误的已证明原因；v2 尚未做真实模型验收。

动作示例拒绝时新增 `praxile.candidate_action_validation.v1`，记录固定 reason、
example_index，以及可确认的 action_type、错误数量、缺少的已知必填字段、未知
字段数量。不记录未知字段名、未知 action 名或字段值，也不原样落盘底层异常。
候选设计轨迹保留该诊断，原校验仍拒绝且没有自动修复调用。历史失败不回填。
边界测试核对 v2 公布的数值上下界与实际注册表一致，验证 v1 形态以及诊断脱敏。

profile v2 真实验收（`8348000`）：一次 low 请求 25.554 秒返回 9770 字符，
4212 input / 3885 output，1412 reasoning；finish_reason=stop，但 JSON 解析失败，
未进入动作校验。无候选、无修复请求，不能据此判断 v2 范围说明是否有效。
见 `results/docx-repair-profile-v2-design-20261019.json`。

### 可选函数提交传输

`ModelRouter.chat(..., response_contract={"name": "submit_result", "schema": {...}})`
新增 opt-in 路径，仅面向已声明 supports_tools 的主 OpenAI-compatible 模型。
契约必须为有界、无引用的 object schema；转换成单个 function 定义及同名
tool_choice。返回必须只有一次同名函数调用，arguments 须为严格 JSON object，
拒绝重复键/非有限值/错误函数/多次调用/仅文本响应；不执行任何函数。
不把契约静默转交其他 fallback，未提供契约时沿用原文本模式。

这一层只验证提交信封和严格 JSON，不声称 provider 强制完整 JSON Schema；
调用方仍须运行候选、引用、动作及安全校验。错误不会触发自动修复请求。
参数 shape、响应大小、未声明工具能力、fallback 拒绝已有离线替身验证。
候选设计 CLI 现在支持 `--response-mode function`（默认仍为文本），适用于
`eval design-context` 和 `eval design-native-context`。调用模型必须显式声明
supports_tools；不支持时拒绝，不静默降级。manifest 保存完整函数合同，
trajectory 保存合同摘要；resume 不允许改变模式或合同。提交函数只传输结果，
不会执行 action_examples，证据引用、动作校验及晋升限制仍由本地控制面执行。
流式接口仍未接入。

一次 MiniMax-M3.1-Flash / low 的 seeded DOCX development 设计调用，指定函数
返回和严格 JSON 解析成功，但第一个 read_document 示例有 4 项 schema 错误。
没有候选发布、probe 或 promotion。已知用量为输入 5778、输出 4240 tokens，
其中 reasoning 538 已包含在输出内；传输耗时 26.169 秒，费用未知。
这验证了函数提交通路，不证明候选质量或任务性能改善，详见
`results/docx-repair-function-design-20261019.json`。

该实测后将 profile 中已知 minimum/maximum/pattern 投影至函数 schema，并增加
仅含已知字段名的 field_error_counts，避免诊断泄漏字段值。旧记录没有这些字段，
不能推断当时具体哪几个参数出错，也未用新 schema 追加模型调用。函数 schema
不涵盖全部文档语义，不能替代本地校验；合同变化需新的 design ID。

随后对新增范围合同做了一次独立实测（bounded-function-001）：输入 5910、输出
4731 tokens，传输 25.475 秒。函数参数 JSON 可解析，但在顶层 proposal_schema
被拒绝，未进入动作示例校验，不能认为参数问题已解决。记录见
`results/docx-repair-bounded-function-design-20261019.json`。没有重试或执行候选。
此后增加 proposal_validation_diagnostic，仅保存已知缺失字段、未知字段数量、
decision 是否合法及候选数量，不保存未知字段名或字段内容。旧结果无法回填。

同合同诊断复验 shape-diagnostic-001 通过了顶层结构，但首个候选在证据引用校验
失败，未进入动作校验；详见 `results/docx-repair-shape-diagnostic-20261019.json`。
这不证明旧结构错误已消除，也不能将未执行的草案计为成功经验。

### 显式限次校验修订

设计命令可选 `--repair-attempts 1`，默认关闭。只有已解析的候选/弃权结构、
文本长度、引用、动作示例或重复候选校验失败才允许一次修订。网络异常、
截断、JSON 解析失败、配置错误和持久化失败不触发该循环。函数提交在 provider
层失败也不会进入修订。单次模型请求内部的 transport 重试仍由其已有配置控制。

修订提示包含原有公开证据、相同 allowed_refs/合同和上次模型提交，后者明确视为
不可信文本，不是证据或指令。原提交仅在本次进程内用于修订，不复制到公开报告
或轨迹；失败阶段、摘要、每次调用用量和 submission_attempt 分别保留。持久化
设计服务在每次调用结束后保存轨迹，再开始下一次；进程中断后仍禁止自动重放。

manifest 冻结修订预算，resume 不得更换。每次请求使用完整的输出/超时预算，
因此开启一次修订可使设计成本上限翻倍；外层 supervisor 应按整个流程另设期限。
原有 candidate 校验、证据范围和 promotion_eligible=false 均不放宽；模型可以
弃权。测试证明预算和状态行为，不证明真实修订成功率；尚未追加付费修订实验。

真实修订验收更新：bounded-repair-001 执行了首次设计及一次修订，各自 HTTP
零重试。首次返回 4 个候选（应为 2），修订后通过顶层结构，但首个 read_document
的 start/limit 类型与范围校验失败。两次用量合计输入 15800、输出 11389 tokens，
无候选、无 probe、无晋升。记录见 `results/docx-repair-bounded-repair-20261019.json`。
这验证了真实限次执行和用量留存，不证明修订能改善候选质量。

该实验后，修订反馈增加控制面生成的具体 proposal/action 诊断、候选位置、
精确候选数及 JSON 整数类型要求，不转发任意 exception 文本、未知字段名或
评审器内容；上次模型提交仍作为不可信内容单独传入，不作为证据。该增强已
通过离线测试，尚未进行追加模型验收，不回填历史结果。
[MiniMax 官方兼容文档](https://platform.minimax.cn/docs/api-reference/text-openai-api)
列出 tools 支持，但未在该页明确说明 response_format 保证；因此不以文本 JSON
模式或未验证的 provider 行为作为结构化输出成功证据。

生成器 v2 支持结构化弃权说明：固定 reason code 与 missing_evidence 枚举，
同时落入 trajectory/generated/report；标为 model_self_report、verified=false。
旧 `{decision: abstain}` 仍接受但 reason=unspecified；任意附加文本、凭据或私有
rubric 请求不属于该协议。诊断不触发自动取数/重试/晋升。设计 manifest 的
implementation 已升级，旧设计不能混用新提示词继续生成；应使用新 design ID。

2026-10-19 关联证据复验（v2 诊断实现前）：8 条修订/布局证据仍返回 NO_ENTRY，
2359 input / 297 output、5.031 秒，0 候选。不能推断具体弃权原因，也不能把
增加证据量视为质量改善。公开记录见 `results/jobbench-candidate-linked-20261019.json`。
后续 v2 单次真实复验正常结束且 JSON 可解析，但后续 schema 校验失败：2443 input /
1565 output、19.236 秒，未接受候选。原版本仅记录异常类型，原始响应未保留，
无法反推具体失败分支，不能称为诊断验收成功。见
`results/jobbench-candidate-diagnostic-20261019.json`。
为解决该排障缺口，后续失败记录增加固定 failure_stage 和 candidate_position，
不记录异常正文或原始模型响应；fixture 验证字段、长度、引用、重复候选和弃权
分支可区分。这个改动不放宽校验，也不触发自动重试。

阶段诊断版真实单次验收：返回合规 NO_ENTRY，reason=insufficient_evidence，
missing_evidence=[before_after_observations,counterexamples]，2443 input / 506 output、
7.35 秒。该结论仍是未验证的模型自述：输入实际含三组页数前后对比（均为 0），
不能把标签解释为完全没有前后观察；确实未提供成功修订对照或独立反例。
本次没有复现上一轮 schema 错误，也不能据此解释上一轮失败。
证据见 `results/jobbench-candidate-staged-20261019.json`。停止对同源轨迹重复生成；
后续需建立公开开发对照及原生候选执行，再讨论实际效用，不能靠增加重试次数
获得可发布的性能结论。

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

证据预算现在按依赖闭包选择：优先失败，但比较结果引用的修订及前置检查必须
同包保留，并排在结论前；共享来源不重复计数。预算容不下整组时省略该结论，
不会只传 page_delta 而丢失依据。跨任务、缺失、循环或未来引用不能进入比较包。
omitted_count 按实际选中数量计算，selection 字段记录策略；原始 call_event_id
仍为源轨迹引用，不将原始工具输入加入模型上下文。这不增加 20 条上限。

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
