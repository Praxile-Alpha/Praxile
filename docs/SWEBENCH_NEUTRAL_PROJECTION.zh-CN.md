# SWE-bench 通用任务投影

原 `praxile eval benchmark` 继续通过既有隔离仓库、Adapter V2 和 SWE-bench evaluator 执行。报告新增 `benchmark_contracts`，将每个输入 EvalTask 显式映射为 `praxile.benchmark_task.v1`，供通用控制面读取；不是把旧任务切换成 Harbor 执行。

关键字段：

- revision_kind=input_task_set_content_digest，dataset_revision 使用实际输入 EvalTaskSet 的 digest。它不是 Hugging Face commit，不应当作上游数据版本证明。
- legacy_manifest_digest 引用原执行 manifest，execution_backend=legacy_swebench_runner。
- environment 保留原 clone URL/base commit；环境准备仍由原 runner 负责，不能仅凭投影重建全部环境。
- evaluator 的 private_ref 是 `legacy-evaluation:<task fingerprint>` 逻辑引用，由原控制面任务对象解析，不是已保存的私有文件路径。
- 测试补丁、参考答案和任意私有 metadata 不进入投影；to_adapter_task 进一步去掉 evaluator/source references。

投影只作为报告附加字段，不改变旧任务序列化、manifest 内容/digest 或 resume 输入比较。重新分析/恢复时可以生成投影，但不会重跑已经完成的任务。旧报告没有该字段时仍可由现有消费者读取。

25 项合同、runner 和 A/B 测试通过，包含隔离仓库 fixture 的执行与 resume、manifest 字节稳定和私有字段边界。这不代表新的真实模型或跨 benchmark 性能结果。
