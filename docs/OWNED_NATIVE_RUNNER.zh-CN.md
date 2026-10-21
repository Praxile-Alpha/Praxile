# Owned Native Runner

`praxile.eval.owned_native_run.run_owned_native` 是通用 BenchmarkTask 到独立
HarborDockerLease 的首条执行通路。现有 Harbor Job/Trial 与 mini-SWE runner
保持不变；此接口不会接管它们的容器，也不运行官方 evaluator。

## 调用与状态

命令入口默认只校验任务 JSON、镜像格式、预算、wheel 摘要和必要模块，不加载
Harbor、不启动 Docker、不读取 Key、不访问模型；计划输出不包含私有正文或评审引用：

```bash
python -m praxile.eval.owned_native_cli \
  --task task.json --state-root .praxile/owned-runs \
  --wheel /absolute/path/to/praxile.whl --wheel-sha256 "$WHEEL_SHA256" \
  --model "$EXECUTOR_MODEL" --base-url "$EXECUTOR_ENDPOINT" \
  --input-source-root /absolute/path/to/public-inputs \
  --credential-file /absolute/path/to/private-key \
  --max-steps 50 --timeout 900 --run-id experiment-001
```

确认计划后，在同一命令末尾加 `--execute` 才会启动实际执行。镜像取自任务的
environment.revision。无输入时省略 --input-source-root。计划模式尚未校验源输入
内容、Docker/Harbor 状态或模型可用性，不能把它当作完整 doctor。
退出码：0 表示计划通过，或执行返回且声明产物回收/清理完成（不是质量评分）；
1 表示产物或清理未满足；2 表示校验/执行失败；130 表示用户中断。错误只打印
类别以避免泄露材料，执行阶段详情见独占目录中的 execution.json。

## Run ID 与完成结果恢复

建议始终提供 `--run-id`（Python API 为 run_id）。控制面用排他创建的持久化
claim 占用该 ID，同 ID 无 --resume 再执行会拒绝，不会覆盖旧记录。计划绑定
任务 fingerprint、镜像、模型、endpoint 摘要、worker 摘要、预算及是否使用凭据代理；
不存储实际 Key。首次执行前占用，发生异常时不自动释放占用以避免重复消费。

`--execute --run-id experiment-001 --resume` 仅复用已完成且清理确认的运行。
复用前校验执行报告、轨迹、输入快照和回收输出的全部已绑定字节摘要，以及
执行计划身份；参数变化、证据损坏或删除均拒绝。Python API 的 resume 不启动
Harbor、容器或模型；CLI 仍先验证所提供 wheel。没有 claim 的旧记录不自动迁移。

claim 仍为 claimed 时，表示执行/清理/完成落盘存在不确定性：必须人工核查，
不能自动重新调用模型。此实现不是中途动作 checkpoint 恢复，也不回收崩溃遗留
容器。没有 --run-id 的旧 API 调用仍为每次新实验，可能再次产生费用。摘要是本地
一致性检查，不是针对同权限恶意宿主的签名认证。

在安装固定 Harbor 0.18.0 或 0.22.0 的可选环境中调用：

```python
from pathlib import Path
from praxile.eval.owned_native_run import run_owned_native

# task 是已校验的 BenchmarkTask；在调用方 async 函数内执行。
report_path = await run_owned_native(
    task,
    state_root=Path(".praxile/owned-runs"),
    image=task.environment.revision,  # 必须为 digest-pinned 镜像
    wheel=Path("/absolute/path/to/current-praxile.whl"),
    wheel_sha256=FROZEN_WHEEL_SHA256,
    model=EXECUTOR_MODEL,
    base_url=EXECUTOR_ENDPOINT,
    input_source_root=Path("/absolute/path/to/public-inputs"),
    credential_file=Path("/absolute/path/to/private-key"),
    max_steps=50,
    timeout_seconds=900,
)
```

没有 inputs 时省略 input_source_root；有 inputs 时必须传入。source_ref 仅在该
来源根目录下解析。private evaluator reference 从不放入执行指令；只有公开的
task.instruction、容器内输入路径/摘要和输出要求被投影给 Native Agent。

每次调用建立新的独占租约目录，执行前写入 execution.json；再离线安装指定
wheel，复用现有 PraxileAgent 监督器、凭据代理、AgentRuntime 和轨迹回收。
run_adapter 在 setup/run 期间拒绝第二次 execute/reset/cleanup。失败或取消
先释放环境；外层 finally 保留最终执行状态和清理结果。

结果目录包含：

- `execution.json`：任务 fingerprint、镜像/wheel 摘要、预算、执行状态、输出摘要与清理状态。
- `agent/praxile-result.json`、`agent/praxile-events.json`：原始 Native 轨迹与事件投影。
- `outputs/`：逐个受限读取并回收的声明产物，输入挂载路径不能被声明为输出。

`executor_returned` 仅表示 Native Adapter 返回并留下轨迹；模型不可用、任务
needs_human 等结果仍须检查轨迹。`required_outputs_collected` 只验证产物回收，
不证明内容正确。官方评分始终标为 `not_run`，promotion_eligible 为 false。
不存在/不安全/过大的产物记 unavailable，不自动填零分或放宽读取边界。

## 已验证与限制

真实 Docker + 本地 fixture 模型验证了读只读输入、编辑 answer.txt、结束、
回收产物和轨迹、撤销代理凭据及容器清理。请求断言确保私有评测 sentinel 和
来源文件名不进入模型请求，轨迹必须包含成功的 read_file 和 edit_file。
这验证执行通路，不是实际模型性能或官方 benchmark 结果。

这是 Python API 和模块 CLI 的 one-shot 执行入口，增加已完成结果复用，但没有
中途动作/崩溃恢复。输入公开性由调用方分类；完整评分隔离、
网络策略、启动硬期限以及高级 worker 参数透传仍待补齐。调用方必须使用兼容
当前宿主 adapter 的 wheel；SHA-256 和必需模块预检不证明完整协议兼容。
