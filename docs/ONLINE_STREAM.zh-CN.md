# Online Stream 实验控制器

`praxile.eval.online_stream.OnlineStream` 是独立于 held-out 五组实验的顺序执行
Python API。任务必须声明 `split="online_stream"`，按创建时列表顺序运行，
不接受持久化工作区、resume、外部候选或实验协议覆盖参数。

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

目前是人工批准候选的执行 API，不是自动 Propose/Probe/Compose 全链路。尚缺
CLI、自动候选生成接入、独立在线评分/统计、全流总预算和真实模型多任务验收。
测试以替身执行器验证顺序、审批、源限制、证据变化、重启、取消及并发拒绝；
Harbor 兼容测试验证模式透传，不等于真实模型收益。不要把本阶段称为 R3 完成。
