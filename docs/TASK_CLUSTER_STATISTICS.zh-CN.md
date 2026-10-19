# 任务级配对统计

Harness Lab 的 split 统计现使用 `praxile.task_cluster_statistics.v1`，statistical_unit=task。旧实现把 task × repetition 展平成独立样本并相减两个 Wilson 区间，可能夸大重复实验提供的信息；该口径不再用于新报告晋升。

每个任务先在同一组 repetitions 内计算 baseline 和 candidate 平均成功率，再取配对差值。任务之间等权平均。sample_count 是完整配对的任务数，observation_count 才是任务运行记录数；重复 100 次同一个任务不会变成 100 个独立任务。任何 repetition 缺失或结果非布尔值时，整个任务列入 incomplete_task_ids，不选择性保留成功的配对。

区间方法为双侧 Hoeffding bound。对 n 个独立任务均值、取值范围宽度 w，95% 半宽为 `w * sqrt(log(40)/(2*n))`。baseline/candidate 的 w=1，配对差值 w=2，最终裁剪到合法范围。该方法保守，不要求任务内重复独立；仍依赖任务簇之间独立。若任务高度相关，需要更高层的簇设计，不能声称本区间已消除相关性。无完整配对时 estimate=null，区间为完整可能范围。

报告保留逐任务均值以供复核。费用/Token 仍只有每 repetition 汇总，当前仅输出描述性平均，不再提供伪装成任务级推断的区间。任务级成本区间及更多 benchmark 连续评分指标仍待接入。

晋升要求新统计 schema、task 单位、至少两个完整任务且无缺失任务，仍需正收益及原有门禁。旧报告或缺统计单位的报告不会被质量门禁放行；必须用冻结执行证据重新生成报告，不能手改 schema 标签充当重新分析。

兼容注意：split 的 sample_count/n 现在表示任务数，不再表示 repetition 数；baseline/candidate 的 resolved 旧计数移除，使用明确的逐任务均值、observation_count 和 rate。顶层实验 manifest 与冻结执行输入不变。

9 项统计、门禁及隔离执行测试通过，覆盖重复不放大样本、缺失配对、等权均值、重复记录拒绝和旧统计门禁。这些测试验证统计实现，不代表实际实验收益。

## 费用完整性

trace metrics 增加 reported_cost_subtotal、cost_total、cost_complete 和 unpriced_model_calls；aggregate 增加 unpriced_task_count。为兼容保留的 cost 字段仍是已报告小计，不能单凭它认定总费用为零。有 MODEL_CALL 未提供 cost，或者没有 MODEL_CALL 证据时，cost_total=null；明确报告 cost=0 的模型调用可以是已知零费用。

Harness Lab 只消费 cost_complete=true 的 cost_total，且 ignore_errors 模式不比较费用。任何 repetition 缺完整价格，则 cost_delta_mean=null，不通过既有成本门禁。普通 A/B 也要求全部任务明确报告费用完整，才输出 cost_delta；旧报告未标完整性时保持未知。

这里的完整性仅针对已记录 MODEL_CALL。尚未进入该 trace 的官方 judge、离线 proposer/probe/composer 等费用不因此被视为零，也不能宣称已完成整条演化流程总成本统计。
