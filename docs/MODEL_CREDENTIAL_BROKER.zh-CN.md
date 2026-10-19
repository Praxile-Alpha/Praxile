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

尚未把 capability 的安全传递及宿主网络入口接入 Harbor；Docker 可达性需要实测，
不能把 `127.0.0.1` 当成容器宿主地址直接使用。尚无 connector 凭据代理、TLS 服务端、
恶意多租户抗 DoS、跨进程租约恢复或精确总费用限制。socket timeout 不是严格系统级
wall timeout，DNS/连接/读取和已接收请求均可能延迟 shutdown。

有本机同用户权限的进程可能访问宿主文件/内存；这个组件不替代 OS 用户隔离。
HTTP capability 只能限制此入口的用途与预算，不能约束拥有 capability 的主体的意图。
因此路线图 R1 的完整最小权限凭据与副作用授权项仍未勾选。
