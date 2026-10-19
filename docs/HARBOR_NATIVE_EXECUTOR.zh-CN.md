# Harbor 原生 Praxile 执行器（实验性）

入口：`praxile.adapters.harbor_native:PraxileAgent`。它在 Harbor 拥有的环境中安装固定 wheel，并运行真正的 `AgentRuntime`，不是导入外部 Agent 的成绩。

## 边界

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

### JobBench easy 环境构建验收（2026-10-19，未完成）

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
