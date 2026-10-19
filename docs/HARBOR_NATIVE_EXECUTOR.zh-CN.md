# Harbor 原生 Praxile 执行器（实验性）

入口：`praxile.adapters.harbor_native:PraxileAgent`。它在 Harbor 拥有的环境中安装固定 wheel，并运行真正的 `AgentRuntime`，不是导入外部 Agent 的成绩。

## 边界

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

验收证据：7 项本地清单/隔离测试；1 项上游真实 renderer + Harbor JobConfig + CLI 生成测试（含 main/easy）。数据内容为测试 fixture，没有执行官方真实任务、没有调用官方 judge，也没有产生 benchmark 分数。官方结果导入、恢复与真实任务覆盖仍未完成。
