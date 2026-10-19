# 宿主模型凭据代理（实验性）

`praxile.eval.credential_broker.ChatCredentialBroker` 是可启动的本机 HTTP 服务，
用于把真实 provider Key 留在宿主控制面。它不是任意网络代理，也不是新的执行器。

## 已实现的边界

- Key 从宿主私有普通文件读取，POSIX final-component no-follow、权限与大小检查；
  不支持的平台拒绝加载，不用无效权限检查冒充保护。
- 只监听 `127.0.0.1`，只接受 `POST /v1/chat/completions`，不记录 HTTP 请求日志。
- 每个实例签发随机临时 Bearer capability；它仍然是秘密，不得公开或持久化到报告。
- 上游 HTTPS endpoint 和模型由宿主固定。客户端不能更改 URL、模型、额外 headers，
  也不能提交 tools、图片、流式请求或其他未经实现的请求字段。
- 上游跳转与环境代理禁用。真实 provider Key 只在宿主向固定 endpoint 发出请求时添加，
  不向客户端传递上游异常正文。检测到上游响应直接含 Key 时拒绝返回。
- 有效期、总调用次数、单次最大输出及累计输出预留预算；失败调用也扣预留额度，
  不自动重试。当前不是按实际输出退款，不是精确费用预算，也不限制累计输入 token。
- 兼容 `max_tokens` 与 `max_completion_tokens`，两者互斥，原样保留客户端字段；
  两种写法均受同一预算限制。JobBench 官方 judge 请求 200000 completion tokens，
  必须由宿主显式配置足够的单次/累计预算，否则拒绝，不能静默改小官方评分参数。
  broker 可配置单次上限不超过 262144；默认仍为 4096，native runtime 仍为 6000。
  这不保证实际 provider 支持该上限，且不是对 multimodal judge 的完整兼容声明。
- 单请求并发、请求体 1 MiB、响应体 8 MiB；读取带超时与分段期限检查。
  关闭服务撤销 capability、关闭 socket 并等待服务线程退出。

## 使用入口

宿主代码以 `read_private_key(Path(...))` 读取本地凭据，构造 broker 时显式传入
`endpoint/model/max_calls/output_budget/max_output/ttl_seconds`，然后调用 `start()`。
执行主体使用返回的地址作为 OpenAI-compatible base URL，并把 `broker.capability`
作为临时 API key。务必通过 `with ChatCredentialBroker(...) as broker` 管理生命周期。
配置不能包含真实 Key；传递临时 capability 时也不能写进命令行或公共日志。

`status()` 只给出剩余预留量和过期/撤销状态；`revoke()` 阻止新的请求，但不能撤回
已经被 provider 接收的调用。客户端断开也不保证 provider 停止计费。

## 验证及未完成项

16 项单元测试覆盖预算、模型/字段限制、错误/撤销/过期、单请求并发、私有文件及
上游 header/无跳转/无环境代理约束。1 项真实 loopback HTTP 测试验证鉴权、成功转发、
撤销拒绝及线程释放；上游是 fixture，不消耗付费 API，不代表真实模型或容器验收。

输出上限字段兼容增补后，针对性单元测试为 21 passed；基础快速集为
499 passed、6 skipped、10 deselected。包括字段不改写、双字段歧义拒绝、
布尔/超额拒绝、显式大额度预留；没有向付费 provider 发出 200000 token 测试请求。

Harbor 原生适配器已增加可选 `credential_file`（宿主私有文件路径，非密钥内容）。
它为单次 run 启动代理，仅上传随机 capability 文件，控制面设置 0600，worker 读取后
删除文件并使用临时 capability 作为 API key。结束/错误/取消时撤销并关闭代理，再尽力
删除残留文件；不要把宿主凭据文件挂载进容器。WorkBuddy dev 用户的文件归属由固定
控制面 chown 操作设置，不授予 Agent root 工具权限。

此模式 `base_url` 指向真实 HTTPS provider，而 worker 使用
`http://host.docker.internal:<ephemeral-port>/v1`。适配器限制 timeout <=3540 秒，
TTL 为 timeout+60；调用上限 min(max_steps+10,1000)，单次输出上限 6000，与现有
action loop 对齐，累计输出预留为 min((max_steps+10)*6000,10000000)。
没有配置 credential_file 时保留既有直接 endpoint 模式。

真实 Docker Desktop 测试已验证 loopback 服务可由该宿主别名访问。
Harbor 0.22 普通/dev 用户各一项真实 worker 用例通过，包含上传/读取/模型调用、
capability 删除、撤销和线程退出；13 项契约测试包含异常/取消撤销。
独立 Harbor 0.18 环境同样通过 13 项契约与 2 项真实容器用例（15 passed，26.99s）；
0.22 两项真实容器用例耗时 25.00s。基础快速单元集 494 passed、6 skipped、10 deselected。
worker wheel SHA-256 为 `ad972e9030b7b065474f35bcc1e6be6b05501e916084351e3058b3dbe4c77088`，
宿主 adapter 使用后续预算/归属修复；测试上游仍为 fixture，不构成真实 benchmark。
固定宿主别名仅在本机 macOS Docker Desktop 验证，Linux 原生 Docker/远程 Harbor
未验证，也不自动开放公网监听。仍无 connector 凭据代理、TLS 服务端、
恶意多租户抗 DoS、跨进程租约恢复或精确总费用限制。socket timeout 不是严格系统级
wall timeout，DNS/连接/读取和已接收请求均可能延迟 shutdown。

有本机同用户权限的进程可能访问宿主文件/内存；这个组件不替代 OS 用户隔离。
HTTP capability 只能限制此入口的用途与预算，不能约束拥有 capability 的主体的意图。
因此路线图 R1 的完整最小权限凭据与副作用授权项仍未勾选。

## 用量证据

`status().usage` 增加版本化 `praxile.broker_usage.v1`：准入/返回/失败/在途调用数、
完成调用累计耗时、已知输入输出 token 小计、各字段的有效报告次数及完整总量。
只有每次准入请求都报告了对应字段时才返回该字段总量，否则为 null；网络失败、
缺字段、布尔/负数/非整数/超限值均不能被记成零消耗。拒绝准入不计为上游调用。
这是 provider 自报用量而非独立计费证明；缓存/推理 token 分项暂未汇总，
`cost_usd` 仍为 null，输出预留预算不能冒充实际用量或金额上限。

日志不含提示词、响应正文、API key 或临时 capability。Harbor 在撤销和关闭后
写入 `praxile-broker-usage.json`，包括取消/错误路径；独立 judge 调用方可保存相同
status 快照。旧实验未保存的数据不能追溯补成已知；在途请求尚未返回时总量不完整。
