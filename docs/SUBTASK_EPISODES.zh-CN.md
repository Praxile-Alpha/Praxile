# 显式子任务证据

R2 增量：`praxile.subtask_episode.v1` 记录本次运行内明确声明的子任务边界，存于现有 trajectory 的 `subtask_episodes` 中，沿用 checkpoint 和轨迹持久化，不新增第二套长期 memory。

## Runtime Actions

```json
{"type":"subtask_begin","goal":"Create the requested report"}
{"type":"subtask_end","claimed_outcome":"completed","summary":"Report created; acceptance pending"}
```

开始和结束时 runtime 刷新项目 snapshot；两者之间的动作获得稳定 `action_id`，Episode 使用 action_refs 引用原始 action/observation，记录观察状态计数与 artifact 路径。边界操作通过工具策略检查，进入正常轨迹和 checkpoint，不计作外部工具调用，但仍占 action-loop 步数预算。

模型声明的 claimed_outcome 与观察状态分开：completed 不会覆盖失败观察，不代表独立验收通过。status=closed 只表示边界关闭；整次运行结束时仍开放的子任务标记 interrupted，state_after 保持未知，不编造完成状态。checkpoint 恢复后可延续已记录的子任务。

限制：当前只有单一顺序子任务，不支持嵌套或跨 executor 子任务；未声明边界的历史轨迹不会靠关键词强行切段。快照只覆盖现有项目状态，不等于完整浏览器/外部服务状态。每条 Episode 保留“不独立验证、不做因果归因”的 uncertainty。

这一步只建立证据基础，不自动创建 memory/skill，不自动反思重规划，不授予 promotion。战略/程序/工具分层消费、带 TTL 的临时经验、状态激活及因果对比仍需后续实现。

验证：日志复制/序列化恢复、失败观察与成功声明并存、未关闭状态、实际 action-loop 边界/工具分发/checkpoint 测试通过。完整 R2-A 总项尚未完成，不提前勾选。
