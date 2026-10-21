# 项目内偏好提案

用户偏好是个性化建议，不是项目事实，也不是经验证的能力提升。新增独立目录
`.praxile/memory/preferences/<key>.md`，复用原有提案、SQLite 资产索引、检索和
回滚，不复制第二套 memory store，不写入 Hermes 或系统全局记忆。

```sh
praxile memory propose-preference concise-summary "最终总结尽量简短" \
  --applies-when "普通任务结束总结，不包含必须详述的审计报告" \
  --source "用户明确提出的输出偏好"
praxile review
praxile accept <PROPOSAL_ID>
```

首条命令只创建 pending memory_update 提案，带 personalization 目的、项目范围、
来源、适用条件及 diff。必须通过现有人工 accept 才写入目录；ProposalService
入口仍要求 confirm。可通过已有 rollback 恢复。key 不能含路径、大小写混用或
目录跳转。提案内容明确禁止覆盖任务指令或安全策略，不会生成强制 harness rule。
来源是调用者提供的说明，不等于身份认证或已验证的事实；confidence=0 不伪造
自动推断置信度。修改同名偏好也是新的待审批提案。

接受后的偏好仍作为 memory 候选参与现有检索和激活，不保证每次都加载。运行
evolution_summary.experience_purpose_usage 单独统计已加载的 preference 资产，
旧 memory/user.md 标为 legacy_user_memory_unclassified，其他资产另计。
personalization_effect 与 capability_improvement 均保持 not_measured；加载次数
不代表用户满意、因果贡献或能力收益。

能力语义归因 judge 现在跳过该 namespace，不为它消费模型调用。存储层也独立
拦截外部传入的偏好正负归因：保留 referenced/used_explicitly 观察，但强制
should_update_asset_outcome=false、attribution_level=uncertain，并记录
capability_credit_excluded。不会生成 outcome_attributed 事件，也不增加资产的
positive_outcome_count/negative_outcome_count。任务自身 outcome 仍如实保留，
不能把它当成偏好效果。旧计数/历史事件不被静默改写。

离线 Reflect 的资产分析排除 preference namespace，并在 inputs 中列出
excluded_preference_assets；被拒偏好提案也不进入通用规则反思。Reflect 提案
出口拒绝携带 personalization 标记或指向偏好目录的候选。Consolidation 的
重复/过期/冲突/低价值资产扫描同样排除该目录，避免把偏好按任务计数提炼成
通用能力或建议淘汰。用户仍可通过专属提案、人工审批和回滚修改自己的偏好。

EpisodeBuilder 现在从能力证据视图中去除结构化偏好资产和偏好提案，将排除清单
另存 excluded_preference_assets，原始 RunEvidence 不变。PatternMiner 在消费
旧 episode 时重复执行该过滤，偏好路径不进入 loaded-assets overlap、聚合
来源或语义 pair judge；明确标为 personalization 的整条 episode 不参与能力
模式挖掘，accepted/rejected 偏好提案也不作为模式置信度反馈。不改写旧文件。

## 尚未完成

- 这是项目内 namespace，不是多用户访问控制。共享同一项目状态的用户仍可读到
  已批准偏好；尚不支持个人身份隔离、全局偏好或自动跨项目迁移。
- 旧 user.md 不自动迁移；已有 memory append、普通 memory_update 路径不强制
  重分类，仍需用户审阅内容。
- 当前分流覆盖能力 judge、SQLite outcome credit、Reflect 与 Consolidation 的
  直接资产输入，以及 episode/pattern 的结构化偏好字段；通用资产 usage 的任务
  结果和其他晋升入口仍需分别审计。未标记的自然语言偏好、已压缩丢失来源的
  历史摘要不能据此自动识别或清洗。独立运行摘要不能替代实验测量，也不能
  据此认定完整 R2 偏好条目完成。
- 没有自动挖掘偏好、真实模型偏好遵循率测试、偏好专属 Web 编辑器或满意度指标。
