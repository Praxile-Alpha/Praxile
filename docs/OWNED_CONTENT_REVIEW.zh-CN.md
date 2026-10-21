# Native 产物的独立内容评审

`praxile.eval.owned_content_review.review_owned_output` 将已回收文本产物交给
独立 `content_review_judge`。它是开发期内容评审，不是 WorkBuddy/JobBench 官方
evaluator；不能据其报告直接 promotion。

## 证据边界

调用方提供冻结 BenchmarkTask、execution_dir、独立 evaluator_root，以及要评审的
source_path / output_path（必须是任务清单中的路径）。函数检查：

- task fingerprint、image、公开输入清单与执行报告一致。
- 执行器已返回，清理已确认，所有 required 输出声明已回收。
- 所选源输入与输出的文件摘要、输出字节数、原始 Native 轨迹摘要匹配。
- 文件读取拒绝软/硬链接和特殊文件，限制单文件 1 MiB，源输入必须为 UTF-8；
  默认文本输出同样为 UTF-8，DOCX 输出需显式选择下述抽取 profile。
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
默认输出只支持 text/* 或 application/json；DOCX 使用显式 profile，PDF 等仍拒绝。
不根据扩展名偷偷转换或截断。

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

## DOCX 正文抽取

`review_owned_output(..., output_format="docx_body")` 要求任务 OutputContract 的
media_type 为 application/vnd.openxmlformats-officedocument.wordprocessingml.document。
验证原始 DOCX 摘要后，受限子进程复用 document_worker 的正文段落/表格解析，
依次连接全部正文块，不使用 read_document 的 4000 字符分页片段。需要安装
`praxile[documents]`。源材料仍只接受 UTF-8 文本，不自动抽取 DOCX 源材料。

明确限制：单个原始文件 1 MiB、ZIP 展开 64 MiB、最多 10000 ZIP 条目和正文块、
正文 200000 字符；超过边界拒绝，不截断。重复 ZIP 条目拒绝。子进程超时 25 秒，
继承 document_worker 的平台相关 CPU/地址空间限制；这不是敌对文件的完整沙箱。

execution_binding 仍保留原始输出 SHA-256，额外保存 output_extraction：
profile=praxile.docx_body_text.v1、python-docx 版本、worker 代码 SHA-256 和抽取
文本 SHA-256。持久化读取时再次核对抽取文本摘要；无此字段的旧文本评审保持兼容。
版本是运行时记录而非自动固定安装，严格对比实验仍需冻结解析依赖。

抽取只覆盖正文和表格文本，不覆盖页眉、批注、修订解释、图片或排版；这些要求
不能从正文评审推断通过。真实解析测试覆盖长段落、表格顺序、超限与损坏文件，
桥接 fixture 覆盖显式启用、原始字节篡改拒绝、抽取证据持久化和加载时校验。
