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

目前是显式生成、人工批准候选的执行链路，不是自动 Propose/Probe/Compose
全链路。尚缺在线 probe 后再组合、独立在线评分/统计、全流总预算和真实模型多任务验收。
测试以替身执行器验证顺序、审批、源限制、证据变化、重启、取消及并发拒绝；
Harbor 兼容测试验证模式透传，不等于真实模型收益。不要把本阶段称为 R3 完成。
