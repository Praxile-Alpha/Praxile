# 官方导入结果的离线比较

先用现有 `praxile.eval.jobbench_results` 或 `praxile.eval.workbuddy_results` 导入官方 trial。新比较工具只接受这些规范化导入报告，不读取 rubric 原始行，不调用模型，也不重跑任务。

创建比较计划（路径相对于计划文件）：

```json
{
  "schema_version": "praxile.import_comparison_plan.v1",
  "repetitions": [
    {"baseline": ["baseline/task-a.json"], "candidate": ["candidate/task-a.json"]}
  ]
}
```

执行：

```sh
python -m praxile.eval.compare_imports --manifest comparison-plan.json --output comparison.json
```

同一计划只比较同一种 benchmark。JobBench 使用官方 weighted score/maximum，WorkBuddy 使用官方 composite reward/1。每个任务保留分子和分母，先计算归一化差值，再在同任务 repetitions 内求平均，最后对任务等权聚合。不跨任务直接相加 rubric 分值。

同任务 checksum 或分母变化拒绝比较；同一 arm 重复 task ID 拒绝。缺任务、judge/infra 错误保留未知，该任务整个配对不纳入均值，仍完整列在任务明细。完整配对子集可能有选择偏差，必须同时报告 incomplete_task_count。区间沿用任务簇 Hoeffding 方法，不把 repetitions 当独立任务。

输出记录计划与输入文件 SHA-256，不复制 official_details、rubric 内容、凭据或本机文件路径。文件摘要只证明输入身份，不证明评分真实性。模型、executor、环境和 judge 配置尚不能从现有导入结构完整核验，因此 controlled_experiment_verified=false、promotion_eligible=false。这是描述性比较入口，不替代官方评分或受控实验协议。

6 项测试通过，包含 CLI 文件读写、输入摘要、不覆盖输入、防私有详情外传、缺评分/变化合同拒绝及任务级聚合。尚无本轮真实 WorkBuddy/JobBench 多任务实验结果。
