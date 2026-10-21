# Runtime 动作恢复边界

启用 checkpoint 时，合法动作经 schema 校验和取消检查后、分发执行前，Runtime
先原子替换 checkpoint，保存 trajectory.pending_action：版本、步骤、动作类型、
动作 SHA-256 和 outcome_unconfirmed 状态。标记不重复保存命令正文或文件内容。
写入失败时不调用工具。该写入不受 checkpoint.every_steps 采样影响。

工具返回后，动作、observation、上下文消息与清除标记后的轨迹再次一起保存。
保存失败时终止循环；磁盘上的旧标记使恢复无法把未确认结果当成可重试动作。
进程在工具执行前或执行后退出均可能留下标记，因此标记表示“不确定”，不是
“工具一定执行了”或“一定失败了”。

`praxile run --resume TASK_ID` 发现 pending_action 字段时，在调用模型前拒绝恢复，
要求检查副作用后显式发起新任务。不要手工删除标记来绕过检查。此版本不提供
自动重放、幂等键下游协商、自动撤销或人工确认后继续原动作的接口。

## 工作区执行锁

正常 run 和 resume 在初始化项目状态前，共用 `.praxile/runtime-execution.lock`。
锁冲突立即抛出 TimeoutError，不排队、不调用模型，也不关闭正在运行实例的工具。
持锁范围包含任务执行、收尾验证和工具关闭；即使这些步骤抛异常也释放锁。
不同工作区可以并行。同一 Runtime 实例不应由多个调用者共享；锁不是线程安全
对象的替代品，失败获取锁的调用不会负责清理先前由调用者打开的资源。

锁复用现有 file_lock：POSIX 使用 flock，Windows 使用 msvcrt，其他平台走现有
fallback。本轮真实进程互斥和 SIGKILL 自动释放仅在 macOS/POSIX 验证，不声称
完成 Windows、NFS 或跨主机支持矩阵。不要删除活跃锁文件，否则可能产生两个
不同 inode 的锁。锁文件留存本身不代表持锁，不能按文件存在与否判断忙碌。

该锁只覆盖 AgentRuntime.run 入口，不能阻止其他程序、直接工具调用或直接
ExperienceStore 更新；它也不认证保留工作区内容或为外部副作用提供 exactly-once。

## 验证和限制

- 故障注入覆盖意图写失败、工具写文件后中断、完成 checkpoint 写失败。
- every_steps=100 时仍保存单次动作结果和对应 observation。
- POSIX 子进程在写入文件后真实 SIGKILL；新 Runtime 读取 checkpoint 拒绝续跑。
- 子任务边界与普通工具共用这段动作分发保护。
- 关闭 checkpoint 不获得这项保障；旧 checkpoint 没有该标记，不能追溯证明
  过去未记录的动作没有发生。兼容读取旧格式不等于认证旧运行可安全续跑。
- 当前 write_json 使用临时文件加原子替换，不提供目录 fsync 或断电持久性。
- 此机制只保护 action loop 分发边界，不覆盖模型请求计费、任务前后验证、
  演化提案落盘等全部阶段，也不认证工作区从 checkpoint 起未被外部修改。
- `.praxile` 是本机可信控制面状态，不支持敌对进程篡改；多进程同时经 run 入口
  恢复同一工作区会被执行锁拒绝，绕过该入口调用内部方法不获得此保证。
- 容器持久化工作区与这项拒绝机制仍不足以开放 Native worker 中途续跑：
  Native worker 保持 fresh-state 约束，完整恢复协议仍在开发。

这是一项保守的未知副作用阻断机制，不是 exactly-once 执行或完整事务性恢复。
