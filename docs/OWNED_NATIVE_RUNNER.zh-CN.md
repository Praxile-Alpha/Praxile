# Owned Native Runner

`praxile.eval.owned_native_run.run_owned_native` 是通用 BenchmarkTask 到独立
HarborDockerLease 的首条执行通路。现有 Harbor Job/Trial 与 mini-SWE runner
保持不变；此接口不会接管它们的容器，也不运行官方 evaluator。

## 调用与状态

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

这是 Python API 的 one-shot 执行入口：没有 CLI/resume/崩溃恢复，每次重复调用
都会创建新实验并可能再次产生费用。输入公开性由调用方分类；完整评分隔离、
网络策略、启动硬期限以及高级 worker 参数透传仍待补齐。调用方必须使用兼容
当前宿主 adapter 的 wheel；仅有正确文件 SHA-256 不证明协议兼容。
