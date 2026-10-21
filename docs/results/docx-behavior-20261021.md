# DOCX 开发实验行为对比

本记录离线读取 2026-10-20 的六份原始 native result，不调用模型、不重新执行
任务、不修改旧成绩。每份输入 SHA256 与[执行收据](docx-six-arm-20261020.json)
逐项核对；[行为收据](docx-behavior-20261021.json)保存事件顺序、来源事件摘要、
结构化事实及覆盖缺口。三对运行只有一个 seeded 开发任务，不是独立任务样本。

## 可观察结果

表中数字仅统计投影器支持的结构化工具结果；成功/失败是工具状态，不是任务分数。

| 组 / arm | 读取文档 | 修改 DOCX | 页数检查（失败/成功） | 交付检查 | 投影/全部结果 |
| --- | ---: | ---: | ---: | ---: | ---: |
| first baseline | 2 | 4 | 3/1 | 0 | 10/13 |
| first candidate | 2 | 3 | 2/1 | 1 | 9/12 |
| second baseline | 3 | 2 | 1/1 | 1 | 8/13 |
| second candidate | 1 | 2 | 0/1 | 1 | 5/8 |
| composition baseline | 2 | 0 | 0/0 | 0 | 2/7 |
| composition candidate | 2 | 2 | 1/1 | 1 | 7/12 |

- first：baseline 的最后一次替换规格改成无表格段落；candidate 的三次替换规格
  都包含表格。不能仅凭这点判定哪种版式更清楚。
- second：candidate 的两次替换规格均为 14 个 bullet、无表格；baseline 两次都
  含表格。可确认结构选择不同，不能把候选描述中的 10 项计划当作实际执行。
- composition：candidate 先使用表格，再改成 8 个 bullet；baseline 没有投影到
  成功修改。原执行收据记录 baseline 因动作 schema 错误停止，这个失败不能
  因 candidate 达到一页就被解释成“经验已证明有效”。

正文保持的独立评审仍不稳定，页数达标和结构差异不等于完整性或可读性通过。
同参数重复只描述观察，不证明状态相同或重复工作无用。未支持的事件保留为
`unprojected_tool_results`；它们不能被当作“没有执行”。

## 离线复用

```bash
python -m praxile.eval.native_behavior \
  --result /path/to/baseline/praxile-result.json \
  --sha256 BASELINE_SHA256 \
  --candidate-result /path/to/candidate/praxile-result.json \
  --candidate-sha256 CANDIDATE_SHA256 \
  --development-task-id BASELINE_TASK_ID \
  --development-task-id CANDIDATE_TASK_ID
```

不提供 candidate 参数时输出单份报告。任务成员和配对由调用方显式声明；
held-out ID 与开发集合重叠时拒绝读取。该命令不读取 evaluator、不使用模型，
也不把两条轨迹的第 N 步对齐。完整投影仅用于离线分析，候选生成接口仍使用
原有最多 20 条的有界证据选择。

报告中的数量差是描述性统计，不校验两侧实验条件，不提供 helped/hurt 因果
判定。`semantic_alignment_verified`、`causal_effect_verified`、
`task_success_verified` 和 `promotion_eligible` 均为 false。
收据一致性测试不是来源认证；原始轨迹保留在私有实验目录，没有上传正文。
