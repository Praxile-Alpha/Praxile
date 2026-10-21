# Native 产物的独立内容评审

`praxile.eval.owned_content_review.review_owned_output` 将已回收文本产物交给
独立 `content_review_judge`。它是开发期内容评审，不是 WorkBuddy/JobBench 官方
evaluator；不能据其报告直接 promotion。

## 命令入口

下面的命令默认只预检，judge-project 使用自己的 `.praxile/config.json`：

```sh
python -m praxile.eval.owned_review_cli \
  --project /path/to/judge-project \
  --task /path/to/frozen-task.json \
  --execution-dir /path/to/harbor-lease-ID \
  --evaluator-root /path/to/private-evaluator \
  --source-path source.docx --source-format docx_body \
  --output-path answer.docx --output-format docx_body \
  --run-id docx-review-001 --total-output-tokens 4000
```

预检会读取并验证真实文件，DOCX/PDF 会启动本地解析子进程；不会初始化 judge 项目、
创建评审运行目录、实例化模型路由或发送请求。输出仅有摘要、格式和预算，不含
文档正文、私有 requirements、文件来源路径或 endpoint。必须提前显式配置
content_review_judge，且 judge 项目不能位于执行目录内。

加 `--execute` 才执行评审。默认仅允许 loopback endpoint；使用云 judge 还需
`--public`，这明确允许把源正文、产物正文和评审要求发送给配置的服务。仅加
`--public` 不会执行。Key 由 provider 的 api_key_env 提供，不作为 CLI 参数。

预检与评审共用 batch planner，校验来源引用、文本边界、分组和输出配额。
当前评审文本每侧最多 64000 字符，比抽取器的 200000 字符上限更严格；超限拒绝，
不截断。total-output-tokens 约束逻辑调用的请求输出额度，不是输入 token、总费用
或网络层重试预算。预检不证明服务连通、工具协议支持或 run ID 尚未使用；执行时
独占创建 run ID，重复 ID 拒绝，不自动重放。失败返回 2，中断返回 130；完整得到
评审结论返回 0、abstain 返回 1，返回 0 也不表示内容全部正确或获准晋升。

## 证据边界

调用方提供冻结 BenchmarkTask、execution_dir、独立 evaluator_root，以及要评审的
source_path / output_path（必须是任务清单中的路径）。函数检查：

- task fingerprint、image、公开输入清单与执行报告一致。
- 执行器已返回，清理已确认，所有 required 输出声明已回收。
- 全部冻结输入、全部声明 collected 的输出摘要和输出字节数匹配；包括 optional
  已回收项。optional unavailable 可保留，未知状态拒绝；required 缺件拒绝。
- 文件读取拒绝软/硬链接和特殊文件；选中进入评审的文件限制 1 MiB，其他核对
  文件单个最多 32 MiB，输入/输出各最多 256 个，总读取预算 128 MiB。默认两侧均为
  UTF-8，源文档和输出文档分别显式选择下述抽取 profile。
- 私有 evaluator 存储不能在 execution_dir 内，内容摘要必须匹配任务引用。

全部任务声明文件的核对不等于扫描工作区，也不表示所有文件都经过质量评审。
只把选中的源和产物正文发送给 judge；其他文件仅核对字节，不发送正文。
集合摘要 artifact_set_sha256 绑定排序后的输入/输出路径、状态、字节数和摘要，
optional 未回收状态也进入集合。文件逐项读取，不是多文件原子快照。
来源公开性由上游负责；同权限恶意宿主可同时改写报告和
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
默认输出只支持 text/* 或 application/json；DOCX/PDF 使用显式 profile，其余二进制格式仍拒绝。
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
输出、evaluator 六个摘要及 artifact_set_sha256），不保存 evaluator 文件路径。原有输入、judge 身份、
轨迹、报告仍按既有方式存储，重复 run ID 拒绝重放。

fixture 测试覆盖篡改拒绝、清理前拒绝、私有存储边界、独立角色、摘要持久化及
重复调用拒绝；不代表真实 judge 准确率、官方成绩或跨任务泛化。

## DOCX 正文抽取

`review_owned_output(..., output_format="docx_body")` 要求任务 OutputContract 的
media_type 为 application/vnd.openxmlformats-officedocument.wordprocessingml.document。
验证原始 DOCX 摘要后，受限子进程复用 document_worker 的正文段落/表格解析，
依次连接全部正文块，不使用 read_document 的 4000 字符分页片段。需要安装
`praxile[documents]`。

源材料为 DOCX 时，另传 `source_format="docx_body"`，同样先验证公开输入清单
中的原始文件摘要，再运行受限抽取器。两侧可分别选择 text/docx_body/pdf_text，默认均为
text；不自动按扩展名推断。InputArtifact 尚无 media_type 字段，源格式是控制面
显式声明，必须经真实 DOCX 解析成功，不是把重命名后的任意文件当成 DOCX。

```python
result = review_owned_output(
    judge_config, task,
    execution_dir=execution_dir, evaluator_root=private_evaluator_root,
    source_path="source.docx", output_path="answer.docx",
    source_format="docx_body", output_format="docx_body",
    run_id="development-docx-review-001", total_output_tokens=4000,
)
```

requirements.source_quote 引用的是抽取后的源正文文本；保留原始 source_sha256，
并增加 source_extraction，字段结构与 output_extraction 相同。持久化读取分别
验证 source_text / output_text 的摘要，不交换或合并两侧的证据。

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
另有真实 DOCX 源文件与产物一起通过冻结任务/执行报告校验的测试；文本/源 DOCX/
输出 DOCX/双侧 DOCX 分别验证持久化。该测试没有调用真实 LLM judge。

## PDF 文字抽取

两侧可分别显式选择 `--source-format pdf_text`、`--output-format pdf_text`，
Python 参数为 `source_format="pdf_text"` / `output_format="pdf_text"`。
输出契约必须声明 `application/pdf`；源格式是控制面声明，解析器实际验证格式。
依赖既有 `praxile[documents]` 中的 pypdf，不增加核心安装依赖。

先验证原始文件摘要，再启动限时子进程，以 pypdf strict 模式逐页提取文字，
按页顺序用换行连接，不截断。profile 为 `praxile.pdf_page_text.v1`，记录 pypdf
版本、worker 摘要和文本摘要，原始 PDF 摘要继续保留。源与产物可使用不同格式；
跨实验比较仍要求同一侧的 profile、解析器版本和 worker 摘要一致。

原始文件最多 1 MiB、1–100 页、总计 200000 字符；评审层仍限每侧 64000 字符。
加密、损坏、超限、任一页面没有可提取文字均拒绝。即使只是合法空白页也会拒绝，
需人工确认或另选显式管线，不能靠忽略该页获得完整性结论。
真实解析与双侧 PDF 桥接测试覆盖这些边界；九种 text/DOCX/PDF 配对验证证据
持久化和加载时校验，没有调用真实 judge。

本模式不做 OCR、视觉检查、表格结构还原、批注/表单完整性验证，也不保证多栏
文字阅读顺序。含文字的页面仍可能包含未提取的关键图片；可提取不等于完整。
针对这些要求必须补视觉或人工证据，不能凭文字 judge 判定通过。

## 持久化比较的抽取条件

`python -m praxile.eval.content_review_files --baseline DIR --candidate DIR` 在已有
文本/预算/judge 检查外，核对双方声明的 execution_binding：任务 fingerprint、
原始源文件和 evaluator 摘要必须相同；源/输出各自的抽取 profile、parser_version、
worker_sha256 必须相同。抽取文本摘要在加载各自记录时已校验。

A/B 可以有不同 output_sha256、执行报告和轨迹摘要，不将候选产物变化误当作
条件冲突。`content_disagreement --left DIR --right DIR` 比较同一产物的 judge
判断，则额外要求原始 output_sha256 和 artifact_set_sha256 相同，不能仅凭相同
正文忽略不同二进制产物或附属文件集合。A/B 允许不同集合摘要，因为候选可以
改变输出；双方仍必须具备相同核对范围，一侧旧无集合摘要时拒绝比较。
两侧均为旧无集合摘要时兼容已有比较，但不据此宣称校验了所有文件。

一侧有执行绑定而另一侧没有，或任一条件不一致，结果为 inconclusive，reason 为
execution_conditions_mismatch，并列出不同字段名；不保留可比 rows 或分数计数。
两侧都是旧无绑定文本记录时保留原比较行为，但 execution_conditions.status 明确
为 unbound_legacy，不能据此声称已核对原始文件/抽取环境。既有证据文件不重写。

matched_declared_bindings 仅表示本地已保存声明一致，不是针对恶意宿主的认证，
也没有认证执行模型、整个运行环境或重复试验条件；不能由此推出因果增益、官方
得分或 promotion。纯报告函数缺少 manifest 时仍只有原有文本级比较语义。
