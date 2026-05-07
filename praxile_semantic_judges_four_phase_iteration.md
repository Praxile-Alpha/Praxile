# Praxile 本地 Cheap Model 语义判断能力优化迭代方案

> 适用范围：Praxile Model Roles、Feedback、Pattern Mining、Asset Attribution、Counterexample Checking、Project Experience Evolution  
> 目标：将当前启发式判断增强为“启发式候选召回 + 本地 cheap_model 语义判断 + proposal 治理”的混合架构，提升 Praxile 深度项目经验归纳能力。  
> 核心原则：本地模型只做语义判断和建议，不静默写入长期经验；所有影响长期资产的变化仍必须走 proposal review。

---

## 一、总体设计目标

当前 Praxile 已经具备：

```text
model_roles
hybrid reward
user feedback reward
pattern mining
counterexample checking
asset attribution
project pattern proposal
```

但部分能力仍偏启发式：

```text
1. Pattern Mining 主要依赖 signature / file / command / fix action overlap；
2. Feedback 理解主要依赖关键词规则；
3. Asset Attribution 主要依赖 loaded / referenced / used_explicitly / outcome；
4. Counterexample Checking 主要依赖结构化字段匹配；
5. Project Pattern Composition 部分内容仍依赖模板。
```

下一轮建议通过本地 cheap_model / Ollama 小模型增强这些语义判断任务。

目标不是用模型替代所有规则，而是形成双层判断：

```text
Heuristic Layer
  - 快速、确定、低成本
  - 负责候选召回、硬规则、安全边界

Cheap Model Semantic Layer
  - 本地模型
  - 负责语义判断、复杂反馈、多意图拆解、归因判断、反例判断

Governance Layer
  - proposal review
  - confidence / risk / evidence
  - 用户确认
```

最终链路：

```text
结构化证据 / 用户反馈 / 历史经验
        ↓
启发式候选筛选
        ↓
cheap_model 语义判断
        ↓
结构化结果
        ↓
confidence / recommendation / attribution 更新
        ↓
proposal review / explain / governance
```

---

## 二、建议新增或明确的 Model Roles

建议在 `model_roles` 中新增或强化以下角色：

```yaml
model_roles:
  cheap_reasoner:
    provider: ollama
    model: qwen2.5-coder:7b

  feedback_classifier:
    provider: ollama
    model: llama3.1:8b
    fallback_role: cheap_reasoner

  attribution_judge:
    provider: ollama
    model: qwen2.5-coder:7b
    fallback_role: cheap_reasoner

  pattern_mining:
    provider: ollama
    model: qwen2.5-coder:7b
    fallback_role: cheap_reasoner

  counterexample_checker:
    provider: ollama
    model: qwen2.5-coder:7b
    fallback_role: cheap_reasoner

  project_pattern_composer:
    provider: ollama
    model: qwen2.5-coder:7b
    fallback_role: cheap_reasoner
```

也可配置语义判断开关：

```yaml
semantic_judges:
  enabled: true
  local_first: true
  max_calls_per_run: 5
  max_calls_per_mine_patterns: 20

  feedback_classifier:
    enabled: true
    role: feedback_classifier
    use_for_complex_feedback_only: true

  attribution_judge:
    enabled: true
    role: attribution_judge
    only_for_loaded_assets_with_score_above: 0.5

  pattern_mining:
    enabled: true
    role: pattern_mining
    only_after_heuristic_score: 0.45

  counterexample_checker:
    enabled: true
    role: counterexample_checker
```

---

## 三、总体实现原则

### 3.1 不全量调用模型

不要让 cheap_model 对所有历史 episode 做全量 pairwise 判断。

应采用：

```text
启发式召回候选
→ cheap_model 语义精排 / 判断
```

例如 Pattern Mining：

```text
Episode Pool
  ↓
Heuristic Candidate Retrieval
  ↓
cheap_model Semantic Pattern Judge
  ↓
Pattern Score Fusion
  ↓
Project Pattern Hypothesis
```

---

### 3.2 模型输出必须结构化

所有 cheap_model 判断都必须输出 JSON schema，不能只返回自然语言。

建议统一返回：

```json
{
  "decision": "yes|no|uncertain",
  "confidence": 0.82,
  "reason": "...",
  "signals": {}
}
```

---

### 3.3 模型判断不能直接写长期资产

cheap_model 可以影响：

```text
confidence
recommended_action
proposal content
attribution_level
pattern_score
counterexample list
```

但不能直接：

```text
静默写入 memory
静默修改 skill
静默 archive asset
静默提升为 rule
```

所有长期变化仍必须走 proposal review。

---

# 四、第一阶段：FeedbackSemanticClassifier

## 4.1 目标

将用户反馈理解从关键词规则升级为“规则优先 + cheap_model 复杂反馈解析”。

解决当前问题：

```text
1. 简单词表只能识别“干得好 / 不对 / 太泛了”；
2. 无法处理多意图反馈；
3. 无法稳定解析“第二条不要记”；
4. 无法判断反馈目标是 run / proposal / asset / pattern；
5. 无法处理“整体不错，但某条经验不要写入”这类复合反馈。
```

---

## 4.2 适用场景

### 规则可直接处理

```text
干得好
不错
有用
不对
错了
太泛了
误导了
```

### 需要 cheap_model 处理

```text
这次整体不错，但第二条经验不要记，第三条可以保留，不过别升成规则。
```

```text
刚才那个方案可以，但不要把它写成长期规则，只作为低置信 memory。
```

```text
这条经验有用，但只适用于 parser 测试，不要推广到 ShellEnv。
```

---

## 4.3 输入建议

```json
{
  "user_text": "这次整体不错，但第二条经验不要记，第三条可以保留，不过别升成规则。",
  "conversation_context": {
    "latest_run_id": "task_123",
    "latest_proposals": [
      {"index": 1, "proposal_id": "prop_1", "type": "memory_update", "title": "..."},
      {"index": 2, "proposal_id": "prop_2", "type": "skill_create", "title": "..."},
      {"index": 3, "proposal_id": "prop_3", "type": "failure_pattern", "title": "..."}
    ],
    "latest_loaded_assets": []
  }
}
```

---

## 4.4 输出 Schema

```json
{
  "feedback_events": [
    {
      "target_type": "run",
      "target_ref": "latest",
      "sentiment": "positive",
      "feedback_type": "satisfaction",
      "strength": 0.7,
      "requires_confirmation": false,
      "reason": "User said the overall result was good."
    },
    {
      "target_type": "proposal",
      "target_ref": "second",
      "sentiment": "negative",
      "feedback_type": "do_not_persist",
      "strength": 0.9,
      "requires_confirmation": true,
      "reason": "User said the second experience should not be remembered."
    },
    {
      "target_type": "proposal",
      "target_ref": "third",
      "sentiment": "positive",
      "feedback_type": "keep_as_low_confidence_memory",
      "strength": 0.6,
      "requires_confirmation": true,
      "reason": "User wants to keep the third item but not promote it to a rule."
    }
  ]
}
```

---

## 4.5 实现步骤

```text
1. 在 feedback.py 中新增 FeedbackSemanticClassifier；
2. 扩展 model_roles，支持 feedback_classifier；
3. 规则识别先执行；
4. 如果输入包含多意图、序号指代、转折词、长期资产约束，则调用 feedback_classifier；
5. 输出结构化 FeedbackEvent；
6. 交给 FeedbackTargetResolver 绑定具体 target；
7. 影响长期资产的负反馈必须 require confirmation。
```

---

## 4.6 验收标准

```text
1. “干得好”能绑定 latest run；
2. “这个 proposal 太泛了”能绑定 latest proposal；
3. “第二条不要记”能绑定第 2 条 proposal；
4. “这条规则误导了你”能绑定 recent loaded asset 或请求确认；
5. 多意图反馈能拆成多个 FeedbackEvent；
6. 影响长期 asset 的负反馈必须 require confirmation；
7. feedback_classifier 可配置为 Ollama 本地模型；
8. feedback_classifier 不可用时回退到规则识别。
```

---

## 4.7 推荐测试

```text
tests/unit/test_feedback_semantic_classifier.py
tests/unit/test_feedback_target_resolver_semantic.py
tests/integration/test_semantic_feedback_flow.py
```

---

# 五、第二阶段：AttributionJudge

## 5.1 目标

将 Asset Attribution 从弱启发式升级为“启发式归因 + cheap_model 语义归因”。

解决当前问题：

```text
被加载的 asset 不一定真正帮助了任务成功；
positive outcome 不应无条件强归因给所有 loaded assets。
```

---

## 5.2 当前弱归因信号

```text
loaded
referenced
used_explicitly
positive_outcome_count
negative_outcome_count
user_helpful_count
user_harmful_count
```

这些信号有用，但不足以判断“该 asset 是否真的影响了这次任务”。

---

## 5.3 cheap_model 判断场景

只有在以下情况调用 AttributionJudge：

```text
1. asset 被加载；
2. asset 与任务有一定启发式相关性；
3. run 有明确 success / failure；
4. asset outcome 将被更新；
5. 或用户查看 explain 时请求更强归因。
```

不要对所有 loaded assets 都调用模型。

---

## 5.4 输入示例

```json
{
  "task": "Fix parser JSON action failure.",
  "loaded_asset": {
    "path": ".praxile/experience/failures/parser-json-normalization.md",
    "summary": "When model output contains fenced JSON or trailing commas, normalize before strict schema validation."
  },
  "actions_taken": [
    "Read praxile/parser.py",
    "Edited parse_action_json",
    "Added fenced JSON extraction",
    "Ran tests/unit/test_action_schema.py successfully"
  ],
  "verification": [
    "python -m pytest tests/unit/test_action_schema.py"
  ],
  "outcome": "success"
}
```

---

## 5.5 输出 Schema

```json
{
  "attribution_level": "strong_positive",
  "used_explicitly": true,
  "confidence": 0.84,
  "evidence": [
    "The applied fix follows the asset's recommended strategy.",
    "The verification command matches the asset's expected test."
  ],
  "should_update_asset_outcome": true,
  "outcome_delta": {
    "positive_outcome_count": 1
  }
}
```

可选 attribution_level：

```text
none
loaded_only
weak_positive
weak_negative
strong_positive
strong_negative
mixed
uncertain
```

---

## 5.6 实现步骤

```text
1. 新增 AttributionJudge；
2. 扩展 model_roles，支持 attribution_judge；
3. 在 run outcome update 前调用；
4. 先用启发式筛候选 asset；
5. 对候选 asset 调用 cheap_model；
6. 根据输出更新 asset attribution；
7. explain 展示 attribution_level 与 reason；
8. asset status 展示 attribution history。
```

---

## 5.7 验收标准

```text
1. loaded asset 不会默认获得 strong_positive；
2. fix action 与 asset strategy 高度一致时，模型可判定 strong_positive；
3. asset 被加载但未参与修复时，判定 loaded_only 或 none；
4. 用户 helpful feedback 可提升 attribution；
5. 用户 harmful feedback 可降低 attribution；
6. explain 展示 attribution reason；
7. attribution_judge 不可用时回退启发式。
```

---

## 5.8 推荐测试

```text
tests/unit/test_attribution_judge_schema.py
tests/unit/test_asset_attribution_semantic.py
tests/resource/test_attribution_judge_flow.py
```

---

# 六、第三阶段：PatternSemanticJudge

## 6.1 目标

将 Pattern Mining 从“多维启发式相似度”增强为“启发式召回 + cheap_model 语义判断”。

解决当前问题：

```text
两个 episode 错误文本不同，但根因相同；
两个修复 diff 不同，但修复策略相同；
两个命令不同，但验证的是同一功能链路；
这些深层相似性启发式难以稳定识别。
```

---

## 6.2 调用策略

PatternSemanticJudge 不应全量扫描所有 pair。

建议：

```text
1. PatternMiner 先计算 heuristic_score；
2. heuristic_score >= 0.45 才调用 cheap_model；
3. 每次 mine-patterns 限制 max_calls；
4. 只对候选 episode pair / cluster 调用。
```

---

## 6.3 输入示例

```json
{
  "episode_a": {
    "category": "test_failure_repair",
    "failure_signature": "JSONDecodeError",
    "affected_files": ["praxile/parser.py"],
    "fix_actions": ["strip trailing comma before json.loads"],
    "verification_commands": ["python -m pytest tests/unit/test_action_schema.py"]
  },
  "episode_b": {
    "category": "test_failure_repair",
    "failure_signature": "Invalid action schema",
    "affected_files": ["praxile/action_schema.py"],
    "fix_actions": ["normalize fenced JSON block before schema validation"],
    "verification_commands": ["python -m pytest tests/unit/test_action_schema.py"]
  }
}
```

---

## 6.4 输出 Schema

```json
{
  "same_underlying_pattern": true,
  "semantic_similarity": 0.82,
  "root_cause_similarity": 0.79,
  "fix_strategy_similarity": 0.76,
  "verification_similarity": 0.72,
  "should_merge": true,
  "recommended_pattern_claim": "Both failures are caused by non-strict model action JSON normalization.",
  "reason": "Although the error messages differ, both fixes normalize model output before schema validation."
}
```

---

## 6.5 Score 融合建议

最终 pattern_score：

```text
pattern_score =
  0.45 * heuristic_score
+ 0.40 * semantic_similarity_score
+ 0.15 * outcome_score
```

如果 semantic judge 返回 `should_merge = false`，则不应生成高置信 pattern。

---

## 6.6 实现步骤

```text
1. 新增 PatternSemanticJudge；
2. 扩展 model_roles，支持 pattern_mining；
3. PatternMiner 中先做 heuristic candidate retrieval；
4. 对候选调用 PatternSemanticJudge；
5. 将 semantic_similarity 写入 Pattern；
6. mine-patterns 输出 semantic reason；
7. ProposalComposer 使用 recommended_pattern_claim 优化 Claim。
```

---

## 6.7 验收标准

```text
1. 不同 failure_signature 但 root cause 相同时可形成 pattern；
2. 相同 failure_signature 但 fix strategy 不同时不应形成高置信 pattern；
3. semantic_similarity 能影响 pattern_score；
4. mine-patterns 展示 semantic reason；
5. project_pattern proposal 使用 semantic recommended_pattern_claim；
6. pattern_mining model_role 不可用时回退启发式。
```

---

## 6.8 推荐测试

```text
tests/unit/test_pattern_semantic_judge_schema.py
tests/unit/test_pattern_miner_semantic_score.py
tests/integration/test_semantic_pattern_mining_flow.py
```

---

# 七、第四阶段：CounterexampleSemanticChecker

## 7.1 目标

将 CounterexampleChecker 从结构化字段匹配升级为“启发式反例识别 + cheap_model 语义反例判断”。

解决当前问题：

```text
相同 signature 不一定同一根因；
相同文件不一定同一问题；
不同错误文本可能构成反例；
用户负反馈可能隐含反例；
active asset 之间可能存在语义冲突。
```

---

## 7.2 调用策略

建议在以下场景调用：

```text
1. candidate hypothesis confidence 较高，准备生成 project_pattern；
2. 存在相似但 fix_action 不同的历史 episode；
3. 存在 rejected similar proposal；
4. 存在 negative asset feedback；
5. 与 active asset 可能冲突；
6. PatternSemanticJudge 输出 uncertain。
```

---

## 7.3 输入示例

```json
{
  "hypothesis": {
    "claim": "JSON action parser failures are usually caused by non-strict model output normalization.",
    "applies_when": ["JSONDecodeError", "action schema tests"],
    "fix_strategy": ["normalize fenced JSON and trailing commas"]
  },
  "candidate_counterexample": {
    "task_id": "task_999",
    "failure_signature": "JSONDecodeError",
    "affected_files": ["praxile/model_provider.py"],
    "fix_actions": ["fixed provider config response format"],
    "outcome": "success"
  }
}
```

---

## 7.4 输出 Schema

```json
{
  "is_counterexample": true,
  "counterexample_type": "same_signature_different_root_cause",
  "confidence_delta": -0.25,
  "recommended_action": "inspect_or_edit",
  "reason": "The same JSONDecodeError was caused by provider config, not parser normalization."
}
```

---

## 7.5 实现步骤

```text
1. 新增 CounterexampleSemanticChecker；
2. 扩展 model_roles，支持 counterexample_checker；
3. CounterexampleChecker 先做启发式候选反例召回；
4. 对候选调用 cheap_model；
5. 将结果写入 hypothesis.counterexamples；
6. 调整 confidence 与 recommended_action；
7. 必要时生成 counterexample_note proposal。
```

---

## 7.6 验收标准

```text
1. same signature different root cause 能被识别为反例；
2. same file different issue 能被识别为反例；
3. negative user feedback 可成为反例信号；
4. active asset conflict 可触发 inspect；
5. counterexample 会降低 confidence；
6. 有强反例时 recommended_action 不得为 accept；
7. counterexample_checker model_role 不可用时回退启发式。
```

---

## 7.7 推荐测试

```text
tests/unit/test_counterexample_semantic_checker.py
tests/unit/test_counterexample_confidence_adjustment.py
tests/integration/test_counterexample_semantic_flow.py
```

---

# 八、四阶段实施优先级

## 第一优先级：FeedbackSemanticClassifier

原因：

```text
用户反馈是最直接、最有价值的 reward 信号；
实现后用户能明显感受到 Praxile “听得懂反馈”。
```

预期效果：

```text
自然语言反馈进入 reward / proposal / asset / pattern。
```

---

## 第二优先级：AttributionJudge

原因：

```text
解决 loaded asset 弱归因问题；
避免所有 loaded asset 都被错误强化。
```

预期效果：

```text
经验资产 outcome 更可信。
```

---

## 第三优先级：PatternSemanticJudge

原因：

```text
提升深度项目经验归纳质量；
让 pattern 不只依赖 signature / file / command overlap。
```

预期效果：

```text
能识别同根因、同修复策略、同验证语义的项目模式。
```

---

## 第四优先级：CounterexampleSemanticChecker

原因：

```text
防止错误经验被高置信沉淀；
提升 governed self-evolution 的可信度。
```

预期效果：

```text
更强反例约束，更少过度泛化。
```

---

# 九、总体验收标准

完成四阶段后，应满足：

```text
1. Pattern Mining 不再只是启发式相似度；
2. Feedback 能理解多意图自然语言；
3. Asset Attribution 能判断经验是否真的帮助任务；
4. Counterexample 能识别语义反例；
5. 所有 cheap_model 输出均为结构化 JSON；
6. 所有模型判断都有 fallback；
7. 所有长期资产变化仍走 proposal review；
8. explain 能展示 cheap_model semantic reasons；
9. review 能展示语义判断对 recommended_action 的影响；
10. 配置中可关闭所有 semantic_judges，回退纯启发式模式。
```

---

# 十、推荐新增测试总表

```text
tests/unit/test_feedback_semantic_classifier.py
tests/unit/test_feedback_target_resolver_semantic.py
tests/unit/test_attribution_judge_schema.py
tests/unit/test_asset_attribution_semantic.py
tests/unit/test_pattern_semantic_judge_schema.py
tests/unit/test_pattern_miner_semantic_score.py
tests/unit/test_counterexample_semantic_checker.py
tests/unit/test_counterexample_confidence_adjustment.py

tests/resource/test_attribution_judge_flow.py

tests/integration/test_semantic_feedback_flow.py
tests/integration/test_semantic_pattern_mining_flow.py
tests/integration/test_counterexample_semantic_flow.py
```

---

# 十一、最终建议

下一轮研发不建议继续单纯堆 proposal 类型，而应重点将 `model_roles` 真正下沉到核心语义判断模块：

```text
1. FeedbackSemanticClassifier；
2. AttributionJudge；
3. PatternSemanticJudge；
4. CounterexampleSemanticChecker。
```

推荐架构：

```text
启发式候选召回
→ cheap_model 语义判断
→ confidence / attribution / feedback / counterexample 更新
→ proposal review
```

这样 Praxile 可以从：

```text
规则 + 证据驱动的有限自进化
```

升级为：

```text
本地模型辅助的语义自进化
```

同时仍保持 Praxile 的核心原则：

```text
cheap_model 可以判断和建议；
不能静默写长期经验；
最终仍要 proposal review。
```

一句话总结：

```text
下一阶段的关键不是让本地模型替代规则，而是让本地 cheap_model 成为 Pattern Mining、Feedback 理解、Asset Attribution 和 Counterexample Checking 的语义判断层，从而显著提升 Praxile 的深度项目经验归纳能力。
```
