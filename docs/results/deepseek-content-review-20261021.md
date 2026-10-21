# MiniMax 执行 / DeepSeek 评审验收

日期：2026-10-21。执行器仍为 MiniMax-M3.1-Flash，本次没有重新执行业务任务。
复用 first 组 baseline/candidate DOCX 和此前冻结的十六项开发要求，仅更换独立
内容评审供应商。[机器收据](deepseek-content-review-20261021.json)保留模型身份、
产物和私有证据文件摘要、逐项判断、用量及失败情况，不包含密钥或文档正文。

## 模型与协议

- 通过实际鉴权 GET /models 确认 `deepseek-flash` 可用；接口显示名称为
  DeepSeek-V4.1-Flash。[官方更新记录](https://api-docs.deepseek.com/updates/)
  将该别名对应到 DeepSeek-V4.1-Flash-1017。接口名称本身没有证明固定后端修订，
  因此记录为可变别名，不声称永久版本固定。
- 第一对评审启用 thinking/low，两侧各一次 HTTP 400。另一次无任务正文、
  最多 64 输出 token 的协议探测确认：thinking 模式拒绝指定函数的 tool_choice。
  保留全部失败，不用后续成功覆盖。
- 新实验显式关闭 thinking，仍采用命名函数提交和完整本地校验。每侧四组，
  每组最多 4000 输出 token、120 秒，零重试。没有修改 executor、晋升逻辑或
  全局开源配置，也没有把官方隐藏 rubric 提供给任何执行器。

## 结果与限制

| 项目 | baseline | candidate |
| --- | ---: | ---: |
| 完整评审要求 | 16/16 | 16/16 |
| DeepSeek 判为 preserved | 16 | 16 |
| 请求次数 | 4 | 4 |
| 报告的输入 token | 16940 | 14436 |
| 报告的输出 token | 2409 | 2636 |

新配对的 8 次请求合计 31376 输入、5045 输出 token；前面三次 HTTP 400 用量
未知，不能将它们计为已证实零费用。总计 11 次 POST，费用未认证。
本地持久化文件、输入引用及双侧计划一致性校验通过，比较状态为 compared。

**这不是“任务满分”或候选胜出。** 此前 MiniMax 对同一 first candidate 的完整
开发评审为 12 preserved / 3 missing / 1 uncertain，而 DeepSeek 为 16 preserved。
供应商和推理/输出预算设置不同，且这些要求是在执行后提炼，不能把两种判断
当作受控 judge 准确率比较。应人工复核分歧条目并建立独立参考标签，不能选择
更乐观的评审作为真值。正文引用存在不等于理解正确，正式质量/视觉与跨任务
校准尚未验收。本轮无经验晋升、无官方成绩，也无性能提升结论。
