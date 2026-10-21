# Benchmark 自检

在对应 benchmark 的隔离依赖环境运行：

```sh
praxile doctor --benchmark workbuddy
praxile doctor --benchmark jobbench --online
praxile doctor --benchmark jobbench --online --probe-benchmark-models
```

默认检查安装、宿主磁盘与项目中显式模型角色，不初始化项目、不写入配置、不
请求模型。`--online` 在 benchmark 模式下只增加 Docker daemon 查询；只有
`--probe-benchmark-models` 明确允许可能产生费用的模型探测，且必须指定 benchmark。

模型配置读取当前项目 `.praxile/config.json` 的 `model_roles.coding_agent` 和
`model_roles.content_review_judge`，各自引用 `model_providers` 中的 provider。
judge 必须显式配置 provider/model，不能借用默认 coding 路由。API Key 仍从
provider 的 api_key_env 指定环境变量读取；不会打印密钥、endpoint 或错误正文。

每个已配置角色直接调用一次主 provider，不通过路由 fallback。请求只含固定的
“Reply with OK only.”，timeout=8 秒、max_tokens=32，保留角色 request_options；
不发送任务、文档、产物或 rubric。probe_attempted 表示调用了 provider，并不证明
网络请求已到达服务端或发生计费。失败用量可能未知，不自动重试或推断零费用。

model_health 的状态区分 not_configured、configured_unverified、unknown_provider、
reachable、invalid_response 与 unavailable，并记录观测时间、探测耗时与异常类型。
所有 provider transport 在探测结束时关闭。请求成功只证明能返回非空文本，
不验证工具协议、任务能力、judge 准确性、模型版本固定或 executor/judge 独立性。
官方 evaluator 保持未验证；请求探测时 checks.model_endpoint 标为 see_model_health，
显式角色探测结果以 model_health 为准。

未请求模型探测时，退出码维持基础设施检查语义；请求后，基础设施及两个角色
均可用才返回 0。这是即时观测，不是持续健康监控；独立 runner 命令传入的模型、
endpoint 或单独 judge 配置文件不自动合并到这里，不能据此认证其他运行计划。
