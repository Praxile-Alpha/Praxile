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

## 证据边界

- 引文校验只证明引用存在，不证明模型理解正确或要求列表完整。
- `all_declared_requirements_preserved` 是 LLM 对已声明要求的判断，不是环境验证。
- `content_preservation_verified`、`requirement_coverage_verified`、
  `official_evaluation`、`promotion_eligible` 均保持 False。
- 本接口不认证文本来源，不检查抽取完整性，也不做视觉检查。调用方须关联
  原始产物哈希、抽取工具/版本及覆盖限制，不能用截断正文证明完整文档通过。
- 用于正式比较时，要求列表须在执行前冻结。对已有六次 DOCX 结果的补充评审
  只能标记为事后诊断，不能追认为预注册官方评分。

目前单元测试验证调用、用量记录、引用拒绝与弃权行为；真实模型准确性、校准、
完整文档抽取流水线及六个产物的独立评审尚未验收。该接口不是 R2 完成证明。
