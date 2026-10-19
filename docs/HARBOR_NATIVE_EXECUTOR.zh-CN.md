# Harbor 原生 Praxile 执行器（实验性）

入口：`praxile.adapters.harbor_native:PraxileAgent`。它在 Harbor 拥有的环境中安装固定 wheel，并运行真正的 `AgentRuntime`，不是导入外部 Agent 的成绩。

## 边界

### 导入统一 EventStore

```sh
praxile --project /absolute/control-project eval import-native \
  --result /absolute/trial/agent/praxile-result.json \
  --events /absolute/trial/agent/praxile-events.json
```

CLI 与库共用 `NativeTraceImportService`。先检查源结果摘要，再重新规范化结果并
逐项核对事件；不信任输入事件文件中的身份或内容。拒绝重复 JSON 字段、非有限
数字、超过 64 MiB 的文件和符号链接路径；macOS `/tmp` 文件请使用物理路径
`/private/tmp`。稳定 task_id/start_time 必需，避免导入时生成随机时间身份。

全部检查通过才向 EventStore 追加事件，并保存 `.praxile/trace/imports/<sha256>.json`
收据。既有 EventStore 的事件 ID 去重与冲突检查继续生效；收据写入失败后重试不会
重复追加，SQLite 索引可从日志重建。该导入只验证本地内容一致性，不认证来源、
不导入官方评分、不下载产物实体、不自动生成经验或晋升候选。

本轮 7 项隔离测试覆盖 baseline/激活/弃权、篡改拒绝、重复导入、索引重建和收据
失败恢复。对冻结 JobBench compact repair 结果离线导入：首次 41 个事件、重复 0 个、
重建后 41 个；源 SHA-256 为
`2a3049dc84e2eb9582867211816e1674a88031a4050367412ad772c6df0ccece`。
0 模型调用，不是新的任务执行或性能结果。

### 原生开发候选通道

`python -m praxile.eval.native_worker` 现可额外接收以下参数（其余 workspace、
instruction、result、model、base-url、max-steps 参数保持原样）：

```text
--development-only
--repository public/docs
--development-candidate /control/candidate.json
--development-candidate-sha256 <sha256-of-candidate-file>
```

仅用于明确的 development 实验，不是 held-out 模式。候选文件须为已有
ContextCandidate 格式，具备 repository/task_signals/anti-scope；文件摘要、大小、
重复 JSON 字段、能力 profile、单一上下文表示和 6000 字符上限在执行前校验。
结构化任务仅用公开 task body 做激活判断，不把环境说明当成任务意图。
未命中保持 abstained，命中后以不可信建议注入；不赋予工具权限、不接受提案，
不写成 memory/skill。fresh baseline 默认行为不变。

结果单独记录 development_context.activation 与 injected，候选摘要及任务正文摘要；
轨迹记录 experimental_context 的实际文本/摘要。若架构门禁提前终止，activation
可以为真而 injected 为假，二者不能混为一谈。一般 runtime API 只允许 fresh、
use_experience=False 的显式实验文本，不能覆盖 resume。

本机真实 worker 子进程 + HTTP fixture 的 baseline/激活/弃权、结构化输入、
交付物门禁组合测试 16 passed；无付费模型调用。标准化 CONTEXT 事件与完整隔离
双 arm 编排尚未接入，不能声称已完成 Harbor 文档 A/B。
该通道本身不认证候选证据来源或隔离私有评分材料，仍由控制面/实验环境负责。

Harbor 插件现在支持同名构造参数 `development_candidate_file`、
`development_candidate_sha256`、`development_only=true`、`repository`，必须一起
显式提供。构造时读取并验证候选，冻结规范化 JSON；实际运行不重读可变宿主文件。
上传冻结内容后传递其摘要给 worker 校验，实际激活仍使用 worker 读取的 task body。
`praxile-execution.json` 及 Harbor metadata 保存源文件摘要、传输摘要、规范化候选
摘要和 repository，三种摘要不可混淆（例如数值规范化可改变候选摘要）。
不在公开执行记录中保存宿主候选路径；缺失开发模式/身份/摘要时拒绝构造。
Harbor 0.18/0.22 各 31 passed、9 skipped，包括冻结与参数转发；本阶段尚未
执行真实 Docker 候选注入，完整 A/B 收益仍未验收。

后续真实验收补充：Harbor 0.18 / 0.22 各 4 passed，覆盖普通/dev 用户与激活/弃权。
测试直接检查模拟模型收到的请求，而不只检查配置或 injected 标记。首轮发现宿主
回收器仍仅接受 baseline 模式，已按配置校验 development 模式及候选摘要、repository、
激活/注入布尔一致性、权限/晋升否定标记；错误身份的结果不发布到正式文件。
宿主修复后复用相同 worker wheel 重跑通过；完整本地适配器回归各 31 passed、13 skipped。
证据见 `results/native-candidate-docker-20261019.json`。该测试不证明模型任务能力、
候选收益或完整 benchmark 双臂实验已完成。

### 规范化上下文事件

宿主回收结果时调用 `native_run_to_events`，生成 `praxile-events.json`，绑定原始
结果文件 SHA-256。开发候选总有独立 CONTEXT_ACTIVATION；只有 result 与轨迹中的
实际 injected 标记一致且文本摘要正确时，才产生 CONTEXT_INJECT，并引用激活事件。
激活但被门禁提前终止不伪造注入。上下文事件仅保存候选/正文摘要，不复制建议正文。
baseline 沿用原 V1 转换，事件 ID 不改变。事件是结果回收时的确定性投影，不是
实时流；投影时间沿用轨迹起始时间，不声称逐事件实测时间或独立来源认证。
原始 result 仍是完整源记录，现有 `events_to_v1_trajectory` 不是新开发元数据的
无损往返接口；消费端应读取规范化事件或原始 result。统一 EventStore 导入与
双 arm 编排仍需后续接入。
本轮验证：772 项单元测试通过；两版 Harbor 本地回归各 31 passed；Harbor 0.22
四种真实 Docker 候选路径 4 passed（44.16 秒），检查激活/注入事件数量及源文件
摘要，0 付费调用。复用前轮 worker wheel，变更仅在宿主规范化/回收代码。

### 固定 worker 依赖

适配器接受可选 `dependency_wheels`，每项严格为本机 `path` 和完整 `sha256`：

```json
{"dependency_wheels":[{"path":"/absolute/wheels/pypdf-6.21.1-py3-none-any.whl","sha256":"<replace-with-verified-64-character-sha256>"}]}
```

调用者准备受信任且适用于任务容器 Python/平台的完整 wheel 集合；这里不联网
解析或安装间接依赖。宿主初始化、上传前以及容器端分别校验摘要，拒绝重复文件名。
安装只在 worker venv 中执行 `pip install --no-index --no-deps`，之后 `pip check`；
缺失或冲突依赖阻止 setup，不启动模型任务。摘要校验是完整性检查，不是软件来源信任认证。

清单的文件名和摘要进入 worker environment digest、适配器 version、独立安装目录、
`praxile-worker-environment.json` 和执行记录，不公开宿主文件路径。无依赖清单保持
原 version 兼容；指定额外依赖须按不同环境报告，不能直接声称与原环境严格 A/B。
`inherit_task_packages` 默认仍关闭；启用后清单摘要并不代表继承包已经完整锁定。

Harbor 0.18 / 0.22 的适配器回归各 30 passed、8 skipped，覆盖离线安装命令、
摘要变更、失败阻断和版本区分。后续真实 Docker 验收在两版 Harbor 各 1 passed：
系统 Python 安装前后均无法导入 pypdf，worker venv 安装后可导入 PdfReader，
pip check 通过，记录明确标为额外依赖环境。首次因测试缺少工作目录失败，补齐
初始化后重跑通过。未调用模型，不代表渲染器或任务验收通过；摘要及边界见
`results/worker-dependency-docker-20261019.json`。

复验需显式设置 `PRAXILE_TEST_WHEEL` 和 `PRAXILE_TEST_DEPENDENCY_WHEEL`（pypdf wheel），
再运行 `tests/resource/test_harbor_native.py::test_offline_dependency_installs_only_in_worker_venv`。
该测试具有 slow/integration/resource 标记，默认快速测试不会启动 Docker。
原始任务镜像、材料和评分器不由此接口修改。

### 容器内执行期限

原生 worker 现在通过 `praxile.eval.process_supervisor` 在独立 POSIX 进程组中启动。
supervisor 以内层 wall timeout 控制 worker，宿主 Harbor exec 留出额外 10 秒用于退出收尾。
超时返回 124；收到 SIGTERM/SIGINT 时回收进程组；正常退出也回收仍留在同组的后代。
宿主日志尽力收集 supervision.json 的白名单状态，区分 worker timed_out 与宿主 exec 失败，
收集不可用不补成成功；原始异常保留。部署需要重新构建包含 supervisor 的 wheel。

4 项本地真实进程测试通过，覆盖退出码、超时后代管道和终止信号回收；
Harbor 0.18/0.22 独立环境各 9 项适配器测试通过，包含 supervisor 命令与状态收集。
以上单元/契约测试不等于真实模型验收，完整容器取消仍未完成：
宿主协程取消不保证立即向容器传递信号，内层期限提供后续停止上限；
SIGKILL 杀死 supervisor 或主动脱离进程组的后代仍需 Harbor 容器级销毁兜底。
supervisor 不是恶意代码沙箱，不改变容器所有权，也不授予 Agent 额外权限。

2026-10-19 容器复验：Harbor 0.18 与 0.22 分别运行真实 Docker 测试，各
2 passed / 9 deselected（22.17s / 22.20s）。两种普通/WorkBuddy dev 用户模式均验证
native worker 正常完成、supervision 状态回收，以及独立超时任务返回 124、
同组后代输出管道关闭。测试 finally 调用 Harbor stop(delete=True)。
模型为容器内 HTTP fixture，不使用 MiniMax、不消费 API、不构成官方 benchmark 成绩。

- 执行环境：Docker Desktop 4.91.0，Engine 29.8.0，Linux ARM64。
- wheel SHA-256：`3c1b2677098e286ed3bd66dba0bc91c15f8cb307e75d79b0f458ef3eb4139058`。
- 基础镜像：`python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`。
- 本机镜像 ID：`sha256:ce9a404c2c0138e747a43e6ea022d2f7e670ed868df35d627a663ba7fb940ea9`。

复验使用新构建 wheel；源码包含 b7c793f 的 supervisor，额外未提交的 README
编辑未作为源码功能证据。上述摘要标识实际构建产物，不承诺从同一 commit 重建字节一致。
Linux 原生宿主、官方任务镜像、真实模型、即时取消及强杀恢复仍需单独验收。

### 取消握手（后续实现）

每次 run 分配新的取消标记路径，supervisor 在启动前和运行中（最多约 100ms
轮询间隔，不含系统调度延迟）检查标记。宿主 run 收到 CancelledError 后，通过
既有 Harbor exec 写入标记，再限时查询 supervisor 的终态。请求最多等待 6 秒，
确认最多等待 6 秒，确认超时不会无限等待或覆盖原取消异常。
`worker_cancel.requested` 和 `termination_confirmed` 分开记录；终态由 supervisor
完成进程组回收后写入。它是运行状态观察，不是防篡改安全证明。

通道不可用时记录 error_type 和未确认状态，不宣称已停止，也不接管 Harbor 的
容器所有权。脱离进程组的恶意后代、supervisor 被 SIGKILL 和容器强杀恢复仍未覆盖。

验证：6 项本地进程测试；Harbor 0.18/0.22 各 11 项契约测试和各 2 项真实
Docker 测试通过。契约测试验证 run 取消进入握手与原异常保留；容器测试直接调用
同一取消 helper，确认 supervisor/后代退出、返回 143、终态回收。尚未注入 Harbor
trial 顶层取消、Docker daemon 失联或宿主进程强杀，不能把它算作完整灾备验收。
本次 wheel SHA-256：`01a076ca0221f530a5bdab8a9165e68985d3c773c0631d6cd0f305a9ba78ac0b`，
环境/镜像同上，两个真实容器测试耗时分别为 23.19s / 23.30s。

### Agent 入口取消验收

后续增加真正的 `PraxileAgent.run()` 取消测试，不再只调用取消 helper。
容器内 fixture 模型收到请求后写入观察标记并阻塞；宿主观察到标记后才取消
正在运行的 Agent coroutine，确保 worker 已经开始实际模型调用。验证原
CancelledError 传播、取消日志中的 requested/termination_confirmed 为 true、
supervisor 返回 cancelled/143，以及未生成的结果不被报告为可用。

Harbor 0.18 和 0.22 各 **4 passed / 11 deselected**，耗时分别 44.26s / 44.08s。
每个版本覆盖正常执行与 Agent 取消，且分别运行普通容器用户和 WorkBuddy dev 用户。
沿用上一节取消版本 wheel 和镜像，所有测试 finally 委托 Harbor 删除容器。
这轮只修改测试，未产生新的运行时 wheel。

该证据闭合 Agent.run -> 取消请求 -> 容器进程回收 -> 终态日志链路。
没有启动完整 Harbor Trial、调用官方 verifier 或付费模型，也没有注入 daemon
失联和宿主 SIGKILL；这些仍属于未完成的容器租约/灾备验收。

- 基础 Praxile 不依赖 Harbor。WorkBuddy 的 Harbor 0.18.0 与 JobBench 的 0.22.0 必须使用独立 Python 环境，不能在同一环境互相升级。
- 环境必须提供 Python >=3.11、venv 和任务工具。宿主 Harbor 环境要求 Python >=3.12。
- `workspace` 必须是明确存在的绝对路径。执行前存在 `.praxile`（含符号链接）会被拒绝，防止跨 arm 经验、规则或配置污染。
- worker 使用白名单参数构建配置，保留默认安全与架构门禁，不自动接受 proposal，不加载已有经验。这个 frozen executor 模式还不能代替后续带经验的实验组。
- 安装前宿主与容器各验证 wheel 的 SHA-256；容器 pip 使用 `--no-index --no-deps`，不会临时下载另一个 Praxile 版本。
- Harbor 负责容器生命周期、网络权限、输入挂载和最终 verifier。插件既不启动第二个容器，也不运行最终评分器。**插件本身不保证评分材料不可见**，必须由 benchmark 环境分别注入。
- 模型 endpoint 是 OpenAI-compatible，`model_name` 是 endpoint 接受的原始模型名。优先使用 Harbor 外部凭据代理；不要在 URL、配置文件或命令行放 API key。直接环境变量模式的 `PRAXILE_BENCHMARK_API_KEY` 不是凭据隔离代理。
- 暂不支持 Harbor MCP/skills 注入，传入即报错；不声称 ATIF 兼容。原始 Praxile JSON 保存在 `agent/praxile-result.json`。
- usage 缺失保持 unknown；不把 Praxile reward 当成官方得分，不伪造成本为零。
- Harbor token 导出从原始调用重算，复用 `praxile.trajectory_usage.v1`，并在
  `AgentContext.metadata.praxile.usage_evidence` 保存完整性和角色覆盖证据。
  调用数大于实际记录、非法计数、冲突的 token 别名均不产生完整总量；不信任
  worker 中缓存的 usage 摘要。旧轨迹没有 model_calls 时按现有记录计算，明确
  标记 `call_count_source=recorded_calls_only`，不能当作全实验成本。空记录保持
  unknown；明确报告的零 token 则保留为零。独立 judge 与 broker 账本尚未合并。

## 安装与验证

### MiniMax 凭据预检（2026-10-19）

用户授权的本地凭据文件已设为 0600；仓库的 `API-key/` 目录同时被 Git 和
源码发布清单排除。不要把密钥放进 agent JSON、容器环境、命令行或运行报告。
预检直接向中国区官方端点 `https://api.minimaxi.com/v1/chat/completions`
发送一次 MiniMax-M3 请求：HTTP 200、provider status 0，输入 167 token、
输出 2 token（其中缓存输入 128 token）。请求关闭 thinking、限制输出 32 token；
这只验证凭据与模型可调用，不是 benchmark、AgentRuntime 或 judge 验收。

该记录不包含凭据内容或摘要，不确认密钥是否已轮换。后续真实任务仍需要宿主侧
有范围、期限和调用上限的凭据代理，不能让任务 shell 继承订阅 Key。
后续已增加宿主代理及 `credential_file` 入口，参见
`MODEL_CREDENTIAL_BROKER.zh-CN.md` 的容器验证与限制。预检成功和 fixture 容器链路
均不足以勾选完整最小权限凭据或真实任务验收。

在 Praxile 仓库中构建 wheel，再在单独环境安装测试依赖：

```sh
python3.12 -m venv .venv-harbor18
.venv-harbor18/bin/python -m pip install 'harbor==0.18.0' pytest pytest-forked pytest-timeout build
.venv-harbor18/bin/python -m build --wheel
PRAXILE_TEST_WHEEL="$PWD/dist/praxile-0.1.0-py3-none-any.whl" \
  .venv-harbor18/bin/python -B -m pytest tests/resource/test_harbor_native.py -q --forked
```

实际版本以构建产物为准。Docker 测试显式 opt-in，使用一次性 `python:3.12-slim` 环境和容器内模拟模型，不调用付费 API；测试退出时由 Harbor 删除容器。该测试证明安装/执行/回收链路，不证明真实 benchmark 任务完成率。

2026-10-19 实测矩阵（macOS ARM64、Docker Desktop 29.8.0、宿主 Python 3.12.10）：

| Harbor | 测试 | 结果 |
|---|---|---|
| 0.18.0 | 插件契约 + Harbor 管理的 Docker 原生执行 | 2 passed |
| 0.22.0 | 同上，独立依赖环境 | 2 passed |
| 无 Harbor 基础环境 | task 契约、旧 schema、本地租约、原生 worker | 34 passed |

Linux 宿主尚未实测；容器内运行 Linux 不等于验证了 Linux 宿主兼容性。镜像 tag 用于集成冒烟，正式实验仍需冻结 image digest。两个 benchmark 的正式数据与官方评分未包含在以上测试中。

Harbor agent 配置参数：

```json
{
  "import_path": "praxile.adapters.harbor_native:PraxileAgent",
  "model_name": "YOUR_ENDPOINT_MODEL_NAME",
  "kwargs": {
    "wheel_path": "/absolute/path/to/praxile.whl",
    "wheel_sha256": "REPLACE_WITH_ACTUAL_SHA256",
    "workspace": "/workspace",
    "base_url": "http://MODEL_PROXY_HOST:PORT/v1",
    "max_steps": 50,
    "timeout_seconds": 900
  }
}
```

这是 agent 参数片段，不是完整 WorkBuddy/JobBench job 配置。任务发现、官方评分配置、私有材料隔离、失败时增量轨迹回收与恢复仍需继续接入；当前不能据此宣称两个 benchmark 已完整支持。

## JobBench 官方 generation 接入

`praxile.eval.jobbench` 读取上游 `jobbench_harbor.prepare` 已生成的不可变目录，不重新实现数据下载、任务渲染或 judge。受测上游提交为 `660b33a86ed032559df0b2fdc1ad38cf7ceb83f3`。

先按官方 setup 准备冻结数据 revision 的 generation，保存 `manifest.json` 的 SHA-256。然后在安装了 Praxile 与 Harbor 0.22.0 的独立环境执行：

```sh
python -m praxile.eval.jobbench \
  --generation /absolute/jobbench/harbor/current \
  --manifest-sha256 YOUR_RECORDED_MANIFEST_SHA256 \
  --split easy
```

可用 `--task-id easy--OCCUPATION--task1` 精确选择；未知 ID、重复 ID、与 split 冲突均报错，不静默跳过。main/easy 是官方的两套难度数据，不自动解释为 train/test。

模块检查完整文件清单与摘要、拒绝符号链接/额外文件、要求 verifier 在独立环境，并拒绝把完整 rubric 原样复制到 agent environment 的产物。执行视图只投影公开说明与 `task_folder` 文件，私有 tests 路径只留在控制面。它不是对恶意 Dockerfile、网络泄漏或重写后的 rubric 的完整安全审计，正式运行仍需审计并固定官方生成器和容器策略。

将上文 agent 对象存为受控的 `agent.json` 后，可生成完整 Harbor job：

```sh
python -m praxile.eval.jobbench \
  --generation /absolute/jobbench/harbor/current \
  --manifest-sha256 YOUR_RECORDED_MANIFEST_SHA256 \
  --split easy \
  --agent-config /absolute/agent.json \
  --job-name praxile-jobbench-easy-001 \
  --jobs-dir /absolute/jobs \
  --write-job /absolute/job.json
```

job 生成前重新校验材料，使用 Harbor 官方 schema 验证；配置文件仅允许新建，权限 `0600`。命令不会执行任务。人工核对模型、费用、judge、网络与材料隔离后，才能在同一 0.22.0 环境使用 `harbor run --config /absolute/job.json`。

验收证据：7 项本地清单/隔离测试；上游真实 renderer + Harbor JobConfig + CLI 生成测试（含 main/easy）。生成协议测试使用 fixture，不代表真实任务执行。结果导入进展见下节，恢复与真实任务覆盖仍未完成。

## 官方结果导入

在同一隔离环境中运行：

```sh
python -m praxile.eval.jobbench_results \
  --generation /absolute/jobbench/harbor/current \
  --manifest-sha256 YOUR_RECORDED_MANIFEST_SHA256 \
  --task-id easy--OCCUPATION--task1 \
  --trial /absolute/jobs/JOB/TRIAL \
  --output /absolute/private-results/trial.json
```

导入时重新验证 generation，并从冻结任务计算 Harbor checksum、读取官方 rubric 权重；不从待导入报告自身取“预期值”。验证 `result.json`、`verification.json`、`reward.json`、`judge-details.json` 的身份与分数一致性，保留原始总分/分母、官方四位小数归一化分数、逐项判断、judge 身份及文件摘要。

有效零分是 completed evaluation，不是基础设施失败；评分错误不补零分。执行状态与评分状态分开，缺失 usage 保持 unknown。文件不一致直接拒绝，输出文件仅新建且权限为 `0600`。**报告内可能含隐藏 rubric，必须保存在控制面私有目录，不能作为演化主体的上下文或直接公开发布。** 摘要与一致性校验不是官方结果的数字签名；仍需可信 verifier 与隔离存储。

2026-10-19 验证：13 项结果导入测试，与已有契约/清单测试合计 45 项通过；上游 renderer、CLI 与官方 verifier 无产物路径测试共 2 项通过。尚未验证非空交付物的付费 LLM judge 路径。

## 真实材料准备记录

### JobBench easy 环境构建验收（2026-10-19，隔离传输修复版已构建）

目标 `easy--technical_writers--task1`，沿用下列冻结 generation。原始 Dockerfile
第一阶段在 `registry.docker-cn.com` TLS 握手超时，未进入模型执行。
随后直接拉取 `registry-1.docker.io/library/python` 的相同摘要
`sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254` 成功。
使用 Docker `--build-context` 将原 FROM 引用映射到官方 registry 的同摘要镜像，
未修改生成目录、Dockerfile、依赖版本或 Docker 全局镜像源。

映射后的 build 已通过基础镜像解析，但约 243 秒后在 APT 安装阶段失败：
`deb.debian.org:80` 多个归档下载连接失败，退出码 100。没有生成最终任务镜像，
也没有启动 Agent、MiniMax benchmark 请求或官方 judge；不能记作 0 分任务。
这次记录是基础设施失败证据，不是成功环境复现或正式支持声明。

随后原样重试仍在约 447 秒后因 HTTP 包下载失败退出。用户明确批准仅在隔离
构建副本修复 Debian 下载协议后，添加如下补丁（位于 apt-get update 之前）：

```diff
+RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources
+
```

隔离副本 `/tmp/praxile-jobbench-easy-https-20261019` 构建成功；递归 diff 确认
相对于冻结 environment 目录只有上述 Dockerfile 改动。未修改原始 generation、
依赖列表、任务材料、评分规则或 Docker 全局配置。基础镜像仍通过上述同摘要
build-context 映射获取。构建证据：

- 原 Dockerfile SHA-256：`24466ac896b59145e688bea0285dcca0751b375c5b43810d499049ab5d59cf85`。
- 修复副本 Dockerfile SHA-256：`63ed77bb0f29ea1f20bbd0c5ae0460c8781923e755f187b333811c8c5fa0d29c`。
- requirements.txt SHA-256：`64aa0ed2ab5e6fe1b0102b28508c6b378b0fbe18217d9692679ed10bd99b440a`。
- 本地 tag：`praxile-jobbench-easy-technical-writers:https-20261019`。
- 本地 image ID：`sha256:ab091d47aad612a3d2d6be3ec102e8870e0d44a1d09de7b9cab5740c7601e55c`，`linux/arm64`，2,506,973,637 字节；不是已发布 registry digest。
- 原始 manifest SHA-256 再次核对未变，仍为下方记录值。
- `docker run --rm --network none` 检查通过：python-docx 1.2.0、openpyxl 3.1.5、
  pdfplumber 0.11.7 可导入，TASK_INSTRUCTIONS.txt 存在，output 目录为空。

这是**带传输修复的集成环境**，不是未修改的官方环境复现。APT 与传递 Python
依赖仍由原配方在线解析，不能仅凭顶层 requirements 推断依赖树逐字节可复现。
此次构建和离线 smoke check 没有调用模型；真实 Agent 执行、独立官方 judge
以及非空交付物验收仍未完成，不能宣称 benchmark 已通过或演化有效。

#### 真实执行入口复验：在模型调用前被门禁拦截

同日在 Harbor 0.22 DockerEnvironment 中，以该镜像启动 PraxileAgent，
原样传入冻结的公开 instruction.md，使用 MiniMax-M3 配置、宿主凭据代理、
30 步和 900 秒上限。未挂载隐藏 tests/rubric。这是适配器入口运行，
**不是完整 Harbor Trial 或官方评测**。

- 执行代码基线：`4764b3b6b574c51838c129eb53de6ba35127ff94`；工作树另有文档变更。
- 新 wheel SHA-256：`8c4f83500350290bc515e67391d45d16b9393af7f950a91b756c6e1fafc9731b`。
- 原始证据目录：`/tmp/praxile-jobbench-executor-https-20261019`（本地临时目录，不承诺长期保存）。
- `trial/agent/praxile-result.json` SHA-256：`d764ebfef4671be87b492a20dd27aba4c411c4797b1584eddf04e60c7409afd5`。
- `trial/agent/praxile-execution.json` SHA-256：`b17ae0d244693a419ab6a0b0fadd997be30ce98687189bf7ae7fa94b3a46eb23`。
- 进程正常返回，任务结果 `needs_human`，唯一 action 为 `architecture_gate`。
- 原因：通用说明 “If the task asks you to modify an existing database or workbook”
  命中 `database`。分析器没有区分环境说明与任务改动意图，还未读取实际任务文件。
- model_calls / prompt_tokens / completion_tokens 均为 0；输出目录为空。
- 输出回收与容器清理成功，并以 Docker project label 查询确认无遗留容器。
- 官方评分 `not_run`，不填 0 分，不声明 MiniMax 真实推理或任务成功。

下一项研发约束：中立 benchmark 入口需要可审计地区分任务正文、环境操作说明与
安全边界。不能删除官方说明、设置默认 gate bypass，或把条件句简单视为可信授权。
真正的架构变更、命中的 frozen boundary 和文件/命令权限仍须生效；修复后重跑时
使用新的 run 身份，保留本次失败证据。

#### 显式任务正文入口

适配器现在接受成对的 `task_body_file`（workspace-relative）和
`task_body_sha256` 参数。调用方必须从冻结的公开材料计算摘要，例如 JobBench
的 `task_folder/TASK_INSTRUCTIONS.txt`，不能从模型响应中自动挑选正文。
worker 在运行前验证文件大小（最多 256 KiB）、真实路径及摘要，拒绝符号链接
组件和越界路径；不读取隐藏评分材料。路径/摘要未配置时，原入口行为保持不变。

`TaskInput V1` 把正文、原始完整环境说明、来源与两个文本摘要写入 trajectory。
正文用于意图分析和初始经验检索，完整说明仍进入模型提示词；环境说明中的隐私
信号仍参与风险判断。正文里的架构信号与命中的 frozen boundary 不降级，
工具权限不改变，也不新增模型可调用的 gate bypass。结构化输入不能覆盖 resume；
恢复沿用原 checkpoint。正文文件的字节摘要由 worker 验证，trajectory 的正文
摘要按 UTF-8 编码计算；两者对合法 UTF-8 文件一致。

这是**可信控制面 API**，不是自动语义分类或授权认证：把真正的用户改动指令错误
放进 environment_instructions 会影响分类，因此不能把该选项直接交给执行模型。
它也不抵御同权限宿主进程在校验期间并发替换文件。当前通过本地 fixture 完整
worker 测试，新的真实 MiniMax 运行和官方评分尚待复验。

#### 新入口真实 MiniMax 复验（未完成任务）

基于 `c82541c`，在独立目录 `/tmp/praxile-jobbench-executor-intent-20261019`
再次运行同一公开任务，原始环境说明未改动，显式绑定 TASK_INSTRUCTIONS.txt 摘要。
架构 gate 为 false，发生 30 次 MiniMax-M3 调用；输入 336,906 tokens、输出
3,564 tokens，记录 19 次工具调用。到达 30 步上限，任务为 needs_human。
13 个 action success、3 blocked、14 failure；这些动作状态不等于任务成功率。
输出目录为空，容器结束清理，官方 judge 未调用，score 保持 null。

新 wheel、镜像、manifest 和结果摘要详见
[`results/jobbench-intent-smoke-20261019.json`](results/jobbench-intent-smoke-20261019.json)。
原始轨迹位于上述临时目录的 trial/agent/praxile-result.json；未公开原始请求、
模型思考文本或凭据，也没有将自评分代替官方分数。

观察到的缺口：PDF 经 read_file 返回二进制内容；通用 Python 提取请求受到
Coding shell 策略拦截；随后模型通过 unittest 执行临时提取脚本，并产生多次非
JSON action 回复。后期虽能导入任务镜像中的 pdfplumber/openpyxl 等库，仍未
生成交付物。不能把缺少依赖当作已证实的根因，也不能把全部失败归咎模型。
下一步应提供受限、分页、限时的正式文档提取及产物生成工具，并对接 Action
Schema 和运行配置，避免依靠 unittest 绕行；不默认放开宿主 shell 或提高步数。
本次只验证任务正文分离进入真实模型链路，不是性能提升或完整 benchmark 验收。

#### 正式文档工具复验（模型请求超时）

`6050fbe` 在新目录 `/tmp/praxile-jobbench-executor-docs-20261019` 重跑，
显式 inherit_task_packages=true，原任务、30 步/900 秒上限不变。多次
read_document 成功，最后模型请求超时，未生成交付物。成功响应累计 28 次、
558,536 input / 1,826 output tokens，25 次工具调用；失败请求用量未知，
这些数字不能作为完整账单。容器已清理，空产物未启动付费 judge。
摘要见 `results/jobbench-documents-smoke-20261019.json`。工具与依赖可见性发生
变化，这不是受控收益对照，不能声称提升或回退的因果效果。

定位发现 worker 单请求沿用 runtime 默认 30 秒，与 900 秒任务预算独立。
现新增显式 `model_timeout_seconds`，默认仍为 30，范围 1–110 秒；worker
与宿主代理分别按该值和该值+10 秒配置，整体 supervisor 上限不变。该值写入
execution record，不自动重试失败调用或调整运行中的冻结实验。
超时返回文字不再声称“没有尝试修改”，要求检查此前动作和部分输出。
更高超时设置是否能完成交付仍未验证，不能将超时配置调整视为任务修复成功。

后续 90 秒单请求复验：`77625c8`、目录
`/tmp/praxile-jobbench-executor-timeout90-20261019`，10 次响应累计 149,009 input /
7,054 output tokens，未出现超时，但因连续三次非法动作停止，输出为空，未运行
官方 judge。最后 DOCX action 含未支持的非 blocks 字段；之前回复混入前置
think 文本。详见 `results/jobbench-timeout90-smoke-20261019.json`。单次随机运行
不能证明延长超时的收益，也不是任务成功。

协议修复：初始/repair 提示增加嵌套 DOCX 结构、支持的 block、禁止额外字段和
分页范围；非法字段仍拒绝，不自动删除或放宽 schema。动作解析仅跳过完整闭合
的前置 `<think>...</think>`，拒绝未闭合/嵌套不明的前缀；不会执行该区域中的
JSON 示例，最终 JSON 字符串里的 literal 标签保留。此项不修改通用 JSON 配置
解析，也不宣称能可靠解析任意自然语言响应。4 项回归及 536 项快速测试通过，
新协议的真实模型交付效果仍需复验。

后续 `fb2ceae` 复验使用目录 `/tmp/praxile-jobbench-executor-protocol-20261019`：
29 次模型响应，561,432 input / 8,419 output tokens，仍因连续非法动作停止。
留下的两个 .docx 分别 0 与 4 字节，由 edit_file 写出，不是有效 Word 文件。
原始官方 judge 的无网络 extract-only 命令退出 0、status=complete，但其 text
明确包含两个 “File is not a zip file” 错误；characters 统计的是错误文本，
不能据 complete 或字符数宣称提取成功。未调用付费评分，不推断零分。
详细摘要见 `results/jobbench-protocol-smoke-20261019.json`。

据此修复通用文本写入器，拒绝四种二进制文档扩展名且保留原文件不动；同时
将 create_docx 表格错误细化到 block/row/cell 路径。11 项二进制写入边界测试
及表格诊断测试通过。修复没有应用到正在运行的冻结实验，也未再次付费重试。

#### 独立官方评分镜像已构建

原始 tests/Dockerfile 构建成功，未改写评分材料或依赖；仅通过 build-context
将 Ubuntu 与 Python 的原固定摘要映射到 registry-1.docker.io 同摘要镜像，
避免本机镜像源 TLS 故障。此处没有使用 Agent 环境的 Debian HTTPS 补丁。

- Dockerfile SHA-256：`baaf2f58ee59a8331f2de84386d0a48b6a23aeef16df2afddd64b399e82d61d0`。
- 本地 tag：`praxile-jobbench-judge:original-20261019`。
- 本地 image ID：`sha256:021aae679c3db6f96dad2826b8daecaa6a7ada69a42728e373c9590f1db28f30`。
- 无网络容器检查：计算环境标识 `libreoffice-24.2.7.2-ubuntu24.04.6-snapshot20260911`，
  openai SDK 2.54.0，官方 verify.py 存在。
- 原始 generation manifest SHA-256 再次核对未变。

这仅验证镜像构建与入口，不验证公式重算的结果或非空产物的官方 LLM 评分。
镜像内含私有评分材料，只用于控制面，不作为执行器镜像或公开推送。

另检查官方 judge 使用 `max_completion_tokens=200000`。宿主代理新增该字段的
保真转发和同一预算校验（不能同时携带 max_tokens），未改写官方 judge。
只有显式配置足够输出预留时才允许请求；provider 实际是否支持此上限、
多模态评分与独立评分容器仍待实际验证。

- 数据 revision：`d43a66575635da0d3f6cb9a20976dc1a60f3ffc9`，公开清单 main 65、easy 63，共 128 条任务。
- 下载 main/easy 的 `technical_writers/task1` 两条集成任务，共 15 个文件、2,694,463 字节；不是全量覆盖。
- 上游 renderer 生成的两任务 manifest SHA-256：`246cdeca67266141ddb35172279e5d1c9be5ceb8d5bca1f68102d276ea6a0b18`。main 任务含一个待搜索参考文件，受限检索接口仍待实现，不能声称材料访问能力已完整。
- 使用真实 main rubric 对空输出运行官方 verifier，获得 10 个评审项、`0/66`、reward `0.0`。此分支不调用模型；报告的默认 judge 名称不代表实际发起了模型请求。
- 以上均不构成 Praxile 真实任务完成率或演化收益证据。执行模型、独立 judge 与费用上限确认后再进行正式任务运行。

## WorkBuddy 数据冻结与准备

`praxile.eval.archives.extract_dataset` 校验下载包 SHA-256 后，在临时目录中解包并整体发布；拒绝路径穿越、链接、特殊文件、重复成员和超限文件。只展开外层数据包，`workspace.tar.gz` 不在宿主机展开。此检查不代表 Dockerfile 或脚本安全审计。

`praxile.eval.workbuddy` 校验数据集契约、文件清单摘要、任务身份与官方评分器引用。私有 tests/plugin 参与冻结摘要，不进入 AgentTask 投影。命令只检查或准备材料，不运行模型：

```sh
python -m praxile.eval.workbuddy \
  --dataset /absolute/wb-bench-office-v1.0 \
  --revision DATASET_COMMIT

# 需要已安装受信任、固定版本的上游 workbuddy_bench。
# 使用第一次检查获得的摘要；原始目录保持不变。
python -m praxile.eval.workbuddy \
  --dataset /absolute/wb-bench-office-v1.0 \
  --revision DATASET_COMMIT \
  --inventory-sha256 RECORDED_DIGEST \
  --prepare-copy /absolute/office-prepared
```

准备调用上游 `ensure_composite_verifier_contract` 和 `ensure_task_user`，在新副本绑定官方 CompositeVerifier，并在每个 task.toml 中设置 agent/verifier 用户为 `dev`。Harbor 不从 job 的 agents 配置读取该用户。副本包含源清单摘要、准备模块 SHA-256 和变更数量；准备失败不发布副本。重新运行时必须选择新目录，不覆盖已有材料。

`WorkBuddyPraxileAgent` 只在固定 setup 阶段以 root 创建 dev 并移交 `/workspace` 所有权，随后复用原生执行器。模型不能通过此入口传入 root 命令。此能力不等于完整 WorkBuddy job 集成，也不提供 Security 子集所需的网络隔离。

2026-10-19 验证记录：

- HF revision `3e8ad839685c589d83a1dd60fe2d15eabb75f27a`；已下载 Code 80 条、Office 50 条，未下载 Web/Security。
- 原始 Code 清单 SHA-256：`cabdc886431828bab24a96c2af197b463a83f9f2d56526ea4ca89f4d6adeb720`。
- 原始 Office 清单 SHA-256：`6ff1386c35a9fef611b426096e2c26f2c9912965f82f85e6d0109c2cba9bf20a`。
- 真实 Office 50 条任务副本准备成功，清单 SHA-256：`769487c35f09022ff682d7be0789cbb35dc42da512d5808e14ca92c83bbbaf0f`。
- 解包/清单/官方准备测试 19 项通过；未运行真实 WorkBuddy 任务或付费 judge，不报告完成率。

### WorkBuddy Job 生成

在隔离的 Harbor 0.18.0 环境中，准备不含密钥的 agent JSON，包含以下字段：

```json
{
  "import_path": "praxile.adapters.harbor_native:WorkBuddyPraxileAgent",
  "model_name": "MiniMax-M3",
  "kwargs": {
    "wheel_path": "/absolute/praxile-0.1.0-py3-none-any.whl",
    "wheel_sha256": "YOUR_WHEEL_SHA256",
    "workspace": "/workspace",
    "base_url": "YOUR_VERIFIED_OPENAI_COMPATIBLE_ENDPOINT",
    "max_steps": 50,
    "timeout_seconds": 900
  }
}
```

模型和 endpoint 可替换；此配置不设置 judge 模型，也不自动传递宿主机凭据。真实运行前仍需完成凭据路由和评分配置检查。

```sh
python -m praxile.eval.workbuddy \
  --dataset /absolute/office-prepared \
  --revision DATASET_COMMIT \
  --inventory-sha256 PREPARED_INVENTORY_SHA256 \
  --task-id EXACT_TASK_ID \
  --agent-config /absolute/agent.json \
  --job-name praxile-workbuddy-office-001 \
  --jobs-dir /absolute/jobs \
  --write-job /absolute/workbuddy-job.json
```

命令只生成新文件（权限 0600），不启动任务；必须明确选择 task ID。重新验证数据摘要和准备状态，单次尝试、单并发、无自动重试，容器结束后清理。Security 子集在受限网络策略验证前明确拒绝。

兼容性注意：Harbor 0.18.0 不从任务 TOML 读取自定义 verifier import_path。生成器在 **job 的 verifier.import_path** 显式设置官方 `workbuddy_bench.judge:CompositeVerifier`，避免落入默认脚本评分器。`dev` 用户仍由 task.toml 控制。官方各任务评分材料和参数不改写；LLM/VLM 评分是否实际启用必须另行验证并报告。

增补验证：31 项解包/清单/job/CLI/上游准备测试通过；真实 Office 50 条通过 Harbor Task/JobConfig 解析，VerifierFactory 实例化类型确认为 CompositeVerifier。尚未完成完整 Harbor job 的真实模型执行与结果归档，不能据此宣称正式评测通过。

## 执行异常证据

原生执行器在 worker 启动前写入 `praxile-execution.json`，分别记录 completed、failed、cancelled、collection_failed。记录包含 wheel 摘要、耗时、异常类型和结果可用性，不复制异常消息或进程输出中的潜在凭据。

worker 超时、取消或非零退出后，仍在最多 15 秒内尝试回收已经存在的最终结果，并保留原始执行异常。下载先进入临时文件，验证版本后才发布为 `praxile-result.json`；残缺下载会被删除。正常进程退出但回收失败也会明确标记，不能当作成功运行。

这不是完整 checkpoint 恢复：进程被强杀而未写出最终结果时，结果保持 unavailable，不把缺失轨迹补成成功证据。setup/upload 阶段错误仍由 Harbor trial 日志记录。容器生命周期继续由 Harbor 管理。

## WorkBuddy 官方结果导入

### 启动前自检

```sh
praxile doctor --benchmark workbuddy --online
praxile doctor --benchmark jobbench --online
```

分别在隔离 Harbor 0.18.0 / 0.22.0 环境运行，不需要初始化项目，不创建 `.praxile/`。检查固定 Harbor 版本、插件导入、Docker CLI；`--online` 在这个模式下只探测 Docker daemon，不调用模型或消费额度。插件导入 15 秒、daemon 查询 10 秒超时。Key 仅检查环境变量是否存在，不输出值，也不证明容器能访问 Key 或套餐支持该 endpoint。

`infrastructure_available` 仅在 Harbor、插件导入、Docker CLI、在线 daemon 与主机磁盘访问都检查通过时为 true；离线 daemon 未检查时不会声称基础设施已可用。`execution_verified` 始终为 false。模型、judge、任务镜像架构兼容仍需真实验收；Security 明确 unsupported。

`--online` 通过有超时的 Docker JSON 查询观察 daemon OS/架构；不以 macOS ARM64 主机架构推断 Linux 容器架构。`disk_access` 只报告项目所在主机文件系统的总量/剩余字节，不创建目录，也不代表 Docker VM 内磁盘可用量。任务最低空间尚未知，required_bytes 保持 null，不臆造通过阈值。10 项自检测试通过；本轮主虚拟环境的离线实测正确报告 Harbor 未安装、daemon 未检查及凭据未配置，未调用模型。此前独立 WorkBuddy 环境的 Harbor/官方插件/Docker 查询记录不等于本轮真实任务验收。

### 导入命令

```sh
python -m praxile.eval.workbuddy_results \
  --dataset /absolute/office-prepared \
  --revision DATASET_COMMIT \
  --inventory-sha256 PREPARED_INVENTORY_SHA256 \
  --task-id EXACT_TASK_ID \
  --trial /absolute/jobs/JOB/TRIAL \
  --output /absolute/private-results/workbuddy-trial.json
```

重新校验冻结数据（包括共享 verifier），从任务目录计算 Harbor checksum，与 trial 身份比较。核对 `result.json`、`verifier/reward.json`、`verifier/score.json` 的数值和评分器配置；保留官方 reward、逐项 verdicts/judges、diagnostics 和元数据，不用 Praxile 的自评分替代。

`no_pass` 的零分可成为 completed evaluation；`judge_error` 标记 judge_unavailable，`build_error` 标记 infra_error，缺评分文件不补零。completed 只表示评分完成，不表示任务成功。非致命 judge error/abstain/review 单独列为 judge_issues；缺失模型身份、usage 或隔离模式保持 unknown/缺失，不推断已启用 LLM/VLM。执行器 finished/timed_out 与评分状态分开。

导入结果可能含私有 rubric、诊断信息及上游异常文本，必须保存在控制面私有目录；输出仅允许新建，权限 0600，不能直接送给演化主体或作为公开报告。摘要及配置一致性校验不提供数字签名，也不能证明评分器未被执行环境篡改。后续真实任务验收仍须验证隔离和来源可信度。

验证：16 项结果导入测试（含上游 ArtifactWriter 输出兼容），与清单/准备/job 测试合计 47 项通过；没有把 fixture 结果冒充真实 benchmark 成绩。

## 生成原生开发实验的双臂配置

先通过对应 benchmark 的生成器准备一个经过校验的基础 Harbor job，显式填写
执行模型、worker `max_steps`/`timeout_seconds`、绝对 `jobs_dir` 和任务路径。
在包含 Praxile 的环境中执行：

```sh
python -m praxile.eval.native_pair \
  --job /absolute/private/base-job.json \
  --candidate /absolute/private/development-candidate.json \
  --candidate-sha256 CANDIDATE_FILE_SHA256 \
  --repository EXACT_SCOPE_REPOSITORY \
  --freeze-local-inputs \
  --output-dir /absolute/private/new-pair
```

新目录包含 `baseline.json`、`candidate.json`、`candidate-asset.json` 和最后写入的
`manifest.json`。目录/文件权限分别为 0700/0600；已有目录不覆盖，中断留下的
不完整目录不视为可执行计划。候选文件核验原始摘要后另存规范化快照，配置引用
快照摘要；manifest 分别保留候选逻辑 digest、快照及两臂配置摘要。

两臂固定单并发、单次尝试和零自动重试，其余共用原配置，只改变 job 名称与
候选注入参数。此命令不启动 Harbor、不调用模型，也不自动评分或批准经验。
推荐使用 `--freeze-local-inputs` 生成 v2 plan：保存任务树（包括文件权限、空目录）
和显式 worker/dependency wheels 的本地摘要清单。准备时检查 wheel 声明摘要，
运行入口及每臂运行前后重新计算清单；增删文件、内容/权限变化或 wheel 变化均拒绝
继续。`execution.json.input_checks` 记录 arm、before/after、检查范围与结果；
已启动实验出现输入变化或不可读取时标记 `confounded`，包括最后一臂结束后的
变化，不再作为正常 jobs_returned 收尾。保留各臂的 job_returned 执行事实，
不因此声明任务质量或污染原因已确定。最多读取 2 GiB/50000 个文件，拒绝符号链接和非普通文件，不读取配置中的
credential_file；清单只保存摘要和元数据，不保存任务/rubric 正文，仍应私有保管。
这是运行前后检测，不是不可变挂载：执行期间修改后又恢复、镜像 tag、远程依赖、模型
端点和 judge 服务的状态尚未冻结，`controlled_experiment_verified` 继续为 false。
不带该参数的旧 v1 plan 仍兼容，执行记录明确 `local_inputs_frozen=false`。
正式执行前应在对应的隔离 Harbor 环境校验配置与材料；冻结镜像、模型端点、
judge、外部文件和运行顺序仍需进一步验证，当前 manifest 不宣称受控实验通过。
真实双臂执行验收、结果收集和恢复编排仍待完成。

准备后可通过显式入口串行启动两臂（会消耗执行模型和评分器额度）：

```sh
python -m praxile.eval.native_pair_run \
  --pair-dir /absolute/private/new-pair --execute
```

WorkBuddy 要求隔离 Harbor 0.18.0，JobBench 要求 0.22.0。入口重建并核对计划、
两臂配置与候选摘要，调用官方 `await Job.create(config)` / `await job.run()`。
`execution.json` 以独占创建方式认领运行，逐臂持久化状态；重复调用和已有 job
目录拒绝自动重跑。每臂启动前重新检查准备文件。异常或取消阻止后续臂启动，
错误记录只保存异常类型，不复制可能含凭据的异常文本。

`jobs_returned` 仅说明两个 Harbor 调用返回，不表示任务成功或官方评分通过；
仍须按上文导入官方 Trial 结果并核对 trace。中断后当前不提供自动 resume，
不要删除执行记录来强制重跑：先核实 Harbor/容器终态，再显式准备新实验。
此入口的排序/拒绝/异常处理已有 fixture 测试，两个上游版本的 API 解析已验证；
真实两臂运行、强杀后的残留资源恢复、来源认证和外部环境冻结仍待验收。

2026-10-19 完整 Job 层 Docker 复验：Harbor 0.22.0 下 active/abstain 两组各执行
baseline/candidate，共四个真实 Trial，2 项测试通过（40.65 秒）。使用已冻结
worker wheel 和当前宿主适配器；四次本地 fixture 模型请求，零付费请求。
核验了原生 completed、实际请求中的候选存在/缺失、代理撤销、fixture verifier
结果以及 Trial 到 native result/events 的摘要绑定。首次测试曾因 fixture 把 bytes
当 JSON 对象而产生 502，不能把当时固定 verifier 的分数当成功；修复后增加了
原生完成状态断言。摘要见 `results/native-pair-job-docker-20261019.json`。
此结果补齐 Job/Trial 执行通道，不代表官方任务质量、真实 A/B 收益或恢复能力。

Harbor 0.18 的对应复验同日完成：使用上游 WorkBuddy
`625b2233093ae4f23e76be28c1f341d41cc70373` 的 Docker 环境插件和
`WorkBuddyPraxileAgent`，active/abstain 两组共四个 Trial，2 项通过（50.22 秒）。
每个 Trial 在 setup 后实际执行 `id -u`，确认非 root 且默认用户为 `dev`；同时
检查候选请求差异、原生完成、代理撤销和 trace 绑定。零付费调用，仍为 fixture
评分脚本，不含官方 CompositeVerifier、image mount 或真实任务质量验收。
记录见 `results/native-pair-workbuddy-job-20261019.json`。可在安装上游插件的
Harbor 0.18 隔离环境，使用同一个带 `PRAXILE_TEST_WHEEL` 的测试命令复验。

## 官方 Trial 与 Native Trace 的绑定

新版适配器将 `source_result_sha256` 写入 Harbor `agent_result.metadata.praxile`。
JobBench/WorkBuddy 官方导入器据此验证同一 Trial 的
`agent/praxile-result.json` 与 `agent/praxile-events.json`：核对原始字节摘要、
trajectory task ID、experience mode，并重算完整事件投影。错配或篡改拒绝导入，
通过后输出 `native_trace_binding`，包含 trace/run identity 和事件数量。
这使控制面可用摘要和 trace ID 关联 `eval import-native` 的收据与官方评分报告，
不需要把 rubric 或完整评审记录写进执行轨迹。

旧 Trial 缺少摘要时仍支持原有评分导入，但明确标记 `unbound`，不会从邻近文件
自动推断关联。`bound` 只证明本地证据一致性，不证明来源可信、环境隔离或因果效果。
没有官方评分时，即使绑定成功，也不补零分、不改为 completed。此功能不自动
晋升候选，也尚未完成原生双 arm 编排及真实官方 Trial 端到端验收。

## 首次真实 Office 镜像构建记录

2026-10-19，任务 `api-usage-explain-cli-l3-001`，使用已冻结 Office 副本中的原始 Dockerfile，未改写任务环境。该 Dockerfile 固定下载 `node-v20.18.1-linux-x64.tar.xz`，因此在 ARM64 宿主机上先显式检查 amd64 构建：

```sh
docker build --platform linux/amd64 \
  --tag praxile-workbuddy-office-smoke:3e8ad83 \
  /absolute/office-prepared/tasks/api-usage-explain-cli-l3-001/environment
```

实际结果：构建在解析 `python:3.12-slim` 基础镜像时失败，Docker 配置的 `registry.docker-cn.com` 返回 TLS handshake timeout。未进入 apt/pip 安装、Agent 执行或官方评分，不能计为任务零分。没有调用模型、没有修改 Docker 全局镜像源。

另一个尚未解决的兼容点：Harbor 0.18.0 的 `default_docker_platform()` 查询 daemon 的 OS/Arch，构建流程据此选择平台；不能假设设置 `DOCKER_DEFAULT_PLATFORM` 就能覆盖它。因此通用 Python 容器 smoke test 通过不代表这一 x64 任务镜像在 ARM daemon 上可直接运行。完整 trial 启动前需要验证明确的 amd64 环境路径或任务官方多架构支持，不能只靠改变 job 标签宣称兼容。

下一步：确认并修复本机镜像源连通性（变更全局配置和重启需用户确认），然后完成真实镜像构建及平台一致性验收，再进行模型任务执行与官方评分。
