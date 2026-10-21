# 开发产物内容保持评审

`praxile.eval.content_review.review_content` 是显式调用的 Python API，不会自动
加入 runtime、官方 evaluator 或 promotion。调用方传入已抽取的原文、产物正文
和保留要求，配置独立 `content_review_judge` 角色及 ModelRouter，再提供
TrajectoryLogger。默认 `private=True`，遵守路由隐私约束；公开材料调用方可显式
指定 False。不要把官方私有 rubric 当作此接口的普通任务要求。

每条要求包含 `id`、`requirement`、`source_quote`，原文引文必须精确存在且 ID
唯一。每次最多 32 条要求，源文/产物各最多 64000 字符；超限拒绝，不静默截断。
评审逐项返回 `preserved/missing/contradicted/uncertain`、产物引文及理由。
preserved/contradicted 必须有精确存在的非空产物引文；遗漏条目、重复 ID、伪造
引文、非法 JSON 或截断响应均 abstain，不能通过只评审容易的条目取得通过。

记录保存正文和输入摘要、要求摘要、所用 role、预算与逐项引文。引文本身可能
含敏感内容，日志应维持项目私有，公开前另行脱敏。ModelRouter 的独立调用用量
进入传入的 TrajectoryLogger；请求失败时用量未知，不补零。接口本身不重试，
provider/transport 的重试仍由既有配置控制。异常正文不会写入记录。

可显式传 `response_mode="function"`，通过已有 named function submission 提交
评审对象；默认仍为 `text`，不改变其他调用。function 模式需执行模型声明工具
支持且使用 OpenAI-compatible provider；不支持时不能静默换协议。它只提交
结果，不执行工具。记录包含协议名与 contract 摘要，来源引文和完整覆盖依旧
由本地验证器检查，不因为模型用了 function 就信任输出。

验证失败返回固定 `validation_code`，区分截断、JSON、顶层形状、条目字段、
未知/重复 ID、判定值、理由、引文类型/长度、无法引用和覆盖不完整；不记录
模型提供的未知字段或错误正文，不做格式修复或自动重试。

provider 已收到响应、但 function submission 无效时，抛出兼容 ModelError 的
`InvalidStructuredResponse`，仅保留固定 submission 错误码、白名单结束标记
和 token 字段。内容评审器按失败调用计入 TrajectoryLogger，不执行补救请求。
非法 token 值以 null 保留未知，不删除后误用另一别名补成完整数据。响应正文、
请求头及未知 usage 字段不进入安全证据。其他调用方仍按 ModelError 处理；
这不代表所有 runtime 消费者已统一补记失败用量。

## 私有响应检查点

为避免诊断时重复请求，可显式传入 `response_checkpoint` 回调，默认关闭。
用量记录后、本地校验前，回调收到有大小上限的响应正文、输入/响应摘要、
协议模式与结束原因；不传递 provider 原始 envelope、请求头、异常消息或路由。
回调收到独立字典，修改它不能改变后续校验使用的正文。检查点失败会弃权，
不会继续给出通过结果或重试。模型调用费用仍计入日志。

回调可能收到源文引文及不可信模型文本。调用方负责使用权限受限的本地目录、
原子写入及清理策略；不要上传到公开结果或提交 Git。回调不是上传授权，也
不负责替调用方证明正文不含秘密。常规评审日志仅标记检查点完成和响应摘要，
不嵌入完整响应。可用检查点做本地重放；重放不得伪装成新的真实模型调用。

## 固定预算分组

`praxile.eval.content_review_batch.review_content_batched` 提供显式 Python API：

```python
report = review_content_batched(
    router, logger, source_text=source, output_text=output,
    requirements=requirements, total_output_tokens=8000, batch_size=4,
    timeout_per_call=90, private=False, response_mode="function",
)
```

必须先对全部要求完成引用、唯一 ID 和边界校验，再按输入顺序冻结分组。
总输出预算平均分配，余数给前面的组；每组额度在调用前确定，不回收、转移
剩余预算或自动重试。完整正文每组相同，不通过丢弃上下文换取通过。

报告记录计划和摘要、每组额度、实际逻辑请求数及未完成 ID。任一组弃权即
停止，保留已完成组的局部结果，但顶层不返回完整 items 或整体保留结论。
只有所有组经过同一引用/覆盖验证，才汇总 LLM 判断；依旧不能自动晋升。
检查点额外绑定计划摘要和组序号，保存失败同样停止。

总额度限制的是向模型请求的输出 token，不是美元费用、输入 token 或硬性
总耗时。重复发送正文会增加输入成本；provider 的计费与重试不能凭这个额度
认证。传输重试、fallback 需单独配置。与单次评审比较必须冻结相同总预算；
扩大到 8000 的验收不能冒充优于此前 3000 的公平效率结果。

## 来源绑定的多片段引用

单次和分组 API 都可显式设置 `citation_mode="units"`；默认 `quote` 不变。
控制面按 512 个 Unicode 码点无损划分产物正文，每段携带完整正文 SHA-256、
起止偏移和原文。模型收到有序片段，返回 `output_refs` ID 数组而非重写引文。
function 模式使用独立 `submit_content_review_units_v1` contract。

每条要求最多引用 8 段；preserved/contradicted 至少引用一段，missing/uncertain
可空。未知 ID、其他正文的 ID、重复引用、非法类型均弃权。最终 `output_spans`
由控制面从本次快照还原，允许不相邻片段，但不把它们拼成伪造的连续原文。
计划、输入摘要、检查点及记录均绑定引用模式，不混用旧响应。

固定分段可能切断句子，提示词要求阅读相邻段。引用粒度较粗，片段存在不代表
与判断相关，更不证明保留要求成立；语义校准与人工复核依旧需要。该模式不做
OCR、原始 DOCX 来源认证、完整抽取证明，也不改变晋升门禁。

## 证据边界

- 引文校验只证明引用存在，不证明模型理解正确或要求列表完整。
- `all_declared_requirements_preserved` 是 LLM 对已声明要求的判断，不是环境验证。
- `content_preservation_verified`、`requirement_coverage_verified`、
  `official_evaluation`、`promotion_eligible` 均保持 False。
- 本接口不认证文本来源，不检查抽取完整性，也不做视觉检查。调用方须关联
  原始产物哈希、抽取工具/版本及覆盖限制，不能用截断正文证明完整文档通过。
- 用于正式比较时，要求列表须在执行前冻结。对已有六次 DOCX 结果的补充评审
  只能标记为事后诊断，不能追认为预注册官方评分。

请求记录包含逻辑调用数、用量是否可用、有限错误分类及可用时的响应摘要和
结束原因；逻辑请求数不等于实际传输尝试数，用量可用也不等于完整。
若异常链含传输证据，仅投影白名单状态、HTTP 状态码、耗时和最近传输调用的
尝试次数；不复制异常消息、URL、响应正文或请求头。该局部证据不代表所有
fallback 路由的完整尝试账本；缺失/非法证据继续保留未知。

单元测试验证调用、用量记录、引用拒绝与弃权行为。六个真实产物的事后评审
已有两份有效响应、四次请求阶段弃权；没有完整成功配对，不用于排名或晋升。
详见 [真实评审记录](results/docx-content-review-20261021.md)。模型准确性、校准、
完整文档抽取流水线仍未验收。该接口不是 R2 完成证明。
