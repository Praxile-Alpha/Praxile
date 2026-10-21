# Native 文本产物的独立内容评审

`praxile.eval.owned_content_review.review_owned_output` 将已回收文本产物交给
独立 `content_review_judge`。它是开发期内容评审，不是 WorkBuddy/JobBench 官方
evaluator；不能据其报告直接 promotion。

## 证据边界

调用方提供冻结 BenchmarkTask、execution_dir、独立 evaluator_root，以及要评审的
source_path / output_path（必须是任务清单中的路径）。函数检查：

- task fingerprint、image、公开输入清单与执行报告一致。
- 执行器已返回，清理已确认，所有 required 输出声明已回收。
- 所选源输入与输出的文件摘要、输出字节数、原始 Native 轨迹摘要匹配。
- 文件读取拒绝软/硬链接和特殊文件，限制单文件 1 MiB，输入/输出必须为 UTF-8。
- 私有 evaluator 存储不能在 execution_dir 内，内容摘要必须匹配任务引用。

只验证被选源文件和输出的实际字节；其余输出只检查回收声明，不声称全量质量
或所有产物摘要复核。来源公开性由上游负责；同权限恶意宿主可同时改写报告和
内容，摘要一致不等于签名认证。环境已清理后才允许启动本桥接，但尚无独立
操作系统身份的 judge 沙箱。

## 评审要求格式

任务 evaluator 必须显式声明 name=`praxile-content-review`、version=`1`，
private_ref 是 evaluator_root 下的相对文件名，content_digest 是该文件的
`sha256:<hex>`。文件只允许一个 requirements 字段：

```json
{
  "requirements": [
    {"id": "r1", "requirement": "保留截止日期", "source_quote": "源材料中的准确原文"}
  ]
}
```

要求与 source_quote 复用既有严格校验、逐项判断及引用机制。源文件只选一项，
输出仅支持 text/* 或 application/json；DOCX/PDF 等须另接固定版本抽取器，
不在这里偷偷转换或截断。

```python
from praxile.eval.owned_content_review import review_owned_output

result = review_owned_output(
    judge_config, task,
    execution_dir=execution_dir,
    evaluator_root=private_evaluator_root,
    source_path="source.txt", output_path="answer.txt",
    run_id="development-review-001", total_output_tokens=4000,
)
```

judge_config 必须单独配置 content_review_judge。默认 private=True 只允许
loopback endpoint；明确允许材料发送至云端后，调用方才可传 private=False。
函数只调用评审服务，不调用执行 Agent、不将评审要求写回输入快照。

持久化 review manifest 保存 execution_binding（任务、执行报告、轨迹、源文件、
输出、evaluator 六个摘要），不保存 evaluator 文件路径。原有输入、judge 身份、
轨迹、报告仍按既有方式存储，重复 run ID 拒绝重放。

fixture 测试覆盖篡改拒绝、清理前拒绝、私有存储边界、独立角色、摘要持久化及
重复调用拒绝；不代表真实 judge 准确率、官方成绩或跨任务泛化。
