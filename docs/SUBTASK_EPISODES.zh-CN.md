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

## 已有资产的分层消费

已有资产的 `.meta.json` 可声明 `experience_layer` 为 strategic、procedural 或 tool。字段应随正常审批的资产更新一起写入；不因子任务关闭自动添加长期资产。没有显式分层的旧资产保持原表示路由，不按文件名猜测分类。

`context.experience_layer_budget_units` 为三层分别设置非负整数预算，默认 150/300/150，仍受总表示预算 600 约束；它是既有 context-unit 估计，不是模型 tokenizer 的精确 token 数。先通过资产状态/作用域约束，再由原有语义激活与表示路由决定是否注入。分层不授予激活资格。

预算用尽或元数据非法会记录 abstain；被选择的资产在实际提示词和 trajectory 中保留层标签。冻结边界、architecture gate 和强制 harness rule 继续原控制规则路径，不受可选经验分层预算削弱。关闭表示路由仍使用原 legacy 行为。

此阶段没有新增 SQL 表或另一套 memory，复用资产索引及侧车元数据；尚不支持按层的 SQL 预过滤、自动分层提案。带 TTL 的任务内临时经验和反思重规划仍待完成。

## 初次激活的观测条件

可选经验侧车元数据可增加以下约束，和 `applies_to` 语义条件共同限制激活：

```json
{
  "activation_requirements": {
    "tool_versions": {"python": "3.12.0"},
    "state_equals": {"git.is_repo": true}
  }
}
```

版本为精确字符串匹配，不支持范围。runtime 从本机 Python 和已安装的 playwright/openpyxl/python-docx/python-pptx/pypdf 包元数据观察版本；包名键使用 `python_package:playwright` 等形式。状态来自初始项目 snapshot，点路径最多五层，仅允许字符串、布尔值或整数精确匹配，不读取任意环境变量，也不执行模型提供的探测命令。

未声明条件的历史资产保持兼容；显式条件非法、实际值缺失或不匹配均 abstain，关闭表示路由也不能跳过这些条件。强制控制规则不因可选经验条件而被屏蔽。观测值存于 trajectory.activation_observations，选择记录的 condition_check 保存逐项 matched/mismatched/unknown，不复制原始值。

已选择经验的条件深复制到 checkpoint context。恢复时重新观察项目状态和工具版本，检查冻结的原条件；若未知或不匹配，在进入 action loop 前拒绝恢复，要求新建任务，避免旧 messages 继续携带失效经验。拒绝原因写入 trace，成功检查写入 trajectory.activation_revalidations。未携带条件的旧 checkpoint 保持兼容，不追溯猜测条件。

运行中的 action loop 对携带条件的经验，在每次模型请求前刷新观测。失效或未知时记录 activation_revalidations 和 trace，保存 checkpoint，返回 needs_human，要求以新上下文开始任务；不会尝试从旧消息中删除片段后继续，也不把检查计入 tool_calls。没有条件的旧资产不触发额外快照。

这不是原子授权检查：观测之后到工具执行之间仍可能发生外部变化，且不可能撤回已经发送给模型的信息；文件/Shell/浏览器工具的权限检查继续独立生效。外部应用版本、浏览器状态和跨进程版本观测尚未覆盖，因此完整状态约束能力仍不勾选。10 项表示路由资源测试、13 项纯条件测试及 2 项 action-loop 失效测试通过，后者验证执行一次工具后状态改变或未知时不再调用模型。

验证：日志复制/序列化恢复、失败观察与成功声明并存、未关闭状态、实际 action-loop 边界/工具分发/checkpoint 测试通过。完整 R2-A 总项尚未完成，不提前勾选。
