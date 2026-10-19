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

这不是原子授权检查：观测之后到工具执行之间仍可能发生外部变化，且不可能撤回已经发送给模型的信息；文件/Shell/浏览器工具的权限检查继续独立生效。完整外部应用状态和跨进程版本观测尚未覆盖，因此完整状态约束能力仍不勾选。10 项表示路由资源测试、13 项纯条件测试及 2 项 action-loop 失效测试通过，后者验证执行一次工具后状态改变或未知时不再调用模型。

## 浏览器观测增补

三处检查（初始、恢复、每次模型请求前）共用观测入口，增加 `state.browser.session_active`、存在时的 `state.browser.origin`，以及 `tool_versions.chromium`。origin 规范化为 `scheme://host:port`，不含用户信息、路径、query 或 fragment。只观察已建立的本进程浏览器，不自动启动或导航；观测异常返回未知。尚未建立会话时，要求 origin/Chromium 版本的经验不能激活。

此增补只覆盖会话存活和 origin，不断言 DOM、登录状态或远端业务状态；也没有实现子任务中途重新检索/注入。22 项针对性测试通过，真实 Chromium 测试已增加对应断言，但本轮执行被沙箱 localhost 绑定权限阻止，沙箱外审批服务不可用，不能记为实测通过。完整应用状态门控仍待后续验收。

## 任务内临时笔记

`subtask_note` 是 runtime 管理的有界假设，不是长期 memory，也不是独立验证过的结论：

```json
{
  "type": "subtask_note",
  "source_episode_id": "本任务已关闭且有动作证据的 episode_id",
  "note_text": "本次观察支持的工作假设或重规划建议",
  "ttl_steps": 2,
  "requirements": {"state_equals": {"git.is_repo": true}}
}
```

必须通过工具策略、来源检查和当前状态条件。每任务最多 16 条，每条 1..2000 字符，TTL 为 1..20 个已记录动作（不是墙钟时间，恢复执行不重置）。来源自动引用子任务中的动作证据；无证据、跨任务或未关闭子任务不能创建笔记。模型声明仍标记 independently_verified=false。

账本保存在原 trajectory/checkpoint 的 task_notes 中，沿用原持久化。每次请求重新检查状态和 TTL，只将活动笔记作为低信任数据临时附加；消息历史不保存 subtask_note 正文。过期标 expired，条件未知或失配标 invalidated，二者不自动复活。task_note_usage 记录实际请求采用的 note IDs；内部登记不计作工具调用，但占 action-loop 步数。

不会直接写长期 memory/skill，不赋予权限，也不改变 Architecture Gate。TTL 只控制后续主动注入，无法保证模型不在其他动作中复述此前见过的信息；不能把它当安全擦除。自动重规划执行或长期资产晋升未实现。笔记测试包含真实 action-loop 的一次注入/过期、状态失效、来源与边界拒绝以及 checkpoint 数据恢复。

## 显式反思调用

新增 `subtask_reflect`，字段与 subtask_note 相同但不传 note_text：它调用可配置的 `experience_reflection` 模型角色，沿用 private/high_risk 路由和取消信号。不是每个子任务自动调用；单个子任务最多尝试一次，每任务最多 16 次，固定本次调用上限 1200 output tokens/30 秒。模型可建议下一步调查或重规划，也可 abstain。

输入只包含该子任务目标、明确标注的 claimed_outcome、末尾最多 8 个动作的 ID/类型/状态及各最多 1200 字符观察。返回 note 时必须引用提供的动作 ID；未知引用或无效 JSON 拒绝，abstain 不创建笔记。有效结果进入同一任务内 TTL 账本，不更新活动 plan，不直接运行建议，也不影响最终 evaluator。模型调用 usage/route 进入原成本账本，解析失败也保留已返回的消耗，反思尝试状态存于 subtask_reflections。

独立调用不等于跨模型独立验证，同一模型可承担执行和反思，证据引用有效也不能证明推理正确。这里实现的是建议生成，不是自动重规划或因果评审。12 项测试通过（含真实 action-loop 分发、假模型输出、证据拒绝、abstain、TTL 和成本）；尚无本轮付费模型效果验收。

验证：日志复制/序列化恢复、失败观察与成功声明并存、未关闭状态、实际 action-loop 边界/工具分发/checkpoint 测试通过。完整 R2-A 总项尚未完成，不提前勾选。
