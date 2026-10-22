# 开发产物内容保持评审

`praxile.eval.content_review.review_content` 是显式调用的 Python API，不会自动
加入 runtime、官方 evaluator 或 promotion。调用方传入已抽取的原文、产物正文
和保留要求，配置独立 `content_review_judge` 角色及 ModelRouter，再提供
TrajectoryLogger。默认 `private=True`，遵守路由隐私约束；公开材料调用方可显式
指定 False。不要把官方私有 rubric 当作此接口的普通任务要求。

每条要求包含 `id`、`requirement`、`source_quote`，原文引文必须精确存在且 ID
唯一。每次最多 32 条要求，源文/产物各最多 64000 字符；超限拒绝，不静默截断。
评审逐项返回 `preserved/missing/contradicted/uncertain`、产物引文及理由。
preserved/contradicted 必须有精确存在的非空产物引文；遗漏条目、重复 ID、伪造
引文、非法 JSON 或截断响应均 abstain，不能通过只评审容易的条目取得通过。

记录保存正文和输入摘要、要求摘要、所用 role、预算与逐项引文。引文本身可能
含敏感内容，日志应维持项目私有，公开前另行脱敏。ModelRouter 的独立调用用量
进入传入的 TrajectoryLogger；请求失败时用量未知，不补零。接口本身不重试，
provider/transport 的重试仍由既有配置控制。异常正文不会写入记录。

可显式传 `response_mode="function"`，通过已有 named function submission 提交
评审对象；默认仍为 `text`，不改变其他调用。function 模式需执行模型声明工具
支持且使用 OpenAI-compatible provider；不支持时不能静默换协议。它只提交
结果，不执行工具。记录包含协议名与 contract 摘要，来源引文和完整覆盖依旧
由本地验证器检查，不因为模型用了 function 就信任输出。

验证失败返回固定 `validation_code`，区分截断、JSON、顶层形状、条目字段、
未知/重复 ID、判定值、理由、引文类型/长度、无法引用和覆盖不完整；不记录
模型提供的未知字段或错误正文，不做格式修复或自动重试。

provider 已收到响应、但 function submission 无效时，抛出兼容 ModelError 的
`InvalidStructuredResponse`，仅保留固定 submission 错误码、白名单结束标记
和 token 字段。内容评审器按失败调用计入 TrajectoryLogger，不执行补救请求。
非法 token 值以 null 保留未知，不删除后误用另一别名补成完整数据。响应正文、
请求头及未知 usage 字段不进入安全证据。其他调用方仍按 ModelError 处理；
这不代表所有 runtime 消费者已统一补记失败用量。

## 私有响应检查点

为避免诊断时重复请求，可显式传入 `response_checkpoint` 回调，默认关闭。
用量记录后、本地校验前，回调收到有大小上限的响应正文、输入/响应摘要、
协议模式与结束原因；不传递 provider 原始 envelope、请求头、异常消息或路由。
回调收到独立字典，修改它不能改变后续校验使用的正文。检查点失败会弃权，
不会继续给出通过结果或重试。模型调用费用仍计入日志。

回调可能收到源文引文及不可信模型文本。调用方负责使用权限受限的本地目录、
原子写入及清理策略；不要上传到公开结果或提交 Git。回调不是上传授权，也
不负责替调用方证明正文不含秘密。常规评审日志仅标记检查点完成和响应摘要，
不嵌入完整响应。可用检查点做本地重放；重放不得伪装成新的真实模型调用。

## 固定预算分组

`praxile.eval.content_review_batch.review_content_batched` 提供显式 Python API：

```python
report = review_content_batched(
    router, logger, source_text=source, output_text=output,
    requirements=requirements, total_output_tokens=8000, batch_size=4,
    timeout_per_call=90, private=False, response_mode="function",
)
```

必须先对全部要求完成引用、唯一 ID 和边界校验，再按输入顺序冻结分组。
总输出预算平均分配，余数给前面的组；每组额度在调用前确定，不回收、转移
剩余预算或自动重试。完整正文每组相同，不通过丢弃上下文换取通过。

报告记录计划和摘要、每组额度、实际逻辑请求数及未完成 ID。任一组弃权即
停止，保留已完成组的局部结果，但顶层不返回完整 items 或整体保留结论。
只有所有组经过同一引用/覆盖验证，才汇总 LLM 判断；依旧不能自动晋升。
检查点额外绑定计划摘要和组序号，保存失败同样停止。

总额度限制的是向模型请求的输出 token，不是美元费用、输入 token 或硬性
总耗时。重复发送正文会增加输入成本；provider 的计费与重试不能凭这个额度
认证。传输重试、fallback 需单独配置。与单次评审比较必须冻结相同总预算；
扩大到 8000 的验收不能冒充优于此前 3000 的公平效率结果。

可选 `output_allocations=[1800, 1400, 1800, 3000]` 显式分配每组额度。列表长度
必须等于组数、每项为 1..8000 的整数、总和必须精确等于 total_output_tokens；
错误在任何模型调用前拒绝。输入列表复制后冻结，后续修改不能改变计划。
分配策略和每组额度进入计划摘要，默认仍按总预算均分。参考开发结果调节
分配属于开发调优，不得追认为预注册策略；正式比较须事先冻结同一计划。

报告的 `usage_evidence` 只取本批次新增的 TrajectoryLogger 调用记录，并以准备的
逻辑请求数核算覆盖（不是供应商收到请求数）。未返回用量的失败请求使完整 token 总量为 null，不用已知
小计替代；此前无关调用不计入。计费、内部重试、并发写同一 logger 均不能由
该局部账本认证。调用方应为评审使用独立 logger，不与并发任务混用。

独立 run 在每组请求前保存 trajectory/report，包含 `pending_group_index` 和
调用意图；进度落盘失败则不发起该组模型请求。每组结束后更新报告，中断时
保留已完成组、未评审 ID 和未知用量，不用一个通用错误报告替代已有结果。
`progress_checkpoint` 接收副本，不能修改内部计划和结果；异常继续向调用者传播。
SIGKILL/断电不会执行 finally，只能依赖此前快照，且文件未提供跨文件原子提交
或供应商收件认证。它不能证明请求未计费，不能自动恢复或重放评审。

## 来源绑定的多片段引用

单次和分组 API 都可显式设置 `citation_mode="units"`；默认 `quote` 不变。
控制面按 512 个 Unicode 码点无损划分产物正文，每段携带完整正文 SHA-256、
起止偏移和原文。模型收到有序片段，返回 `output_refs` ID 数组而非重写引文。
function 模式使用独立 `submit_content_review_units_v1` contract。

每条要求最多引用 8 段；preserved/contradicted 至少引用一段，missing/uncertain
可空。未知 ID、其他正文的 ID、重复引用、非法类型均弃权。最终 `output_spans`
由控制面从本次快照还原，允许不相邻片段，但不把它们拼成伪造的连续原文。
计划、输入摘要、检查点及记录均绑定引用模式，不混用旧响应。

固定分段可能切断句子，提示词要求阅读相邻段。引用粒度较粗，片段存在不代表
与判断相关，更不证明保留要求成立；语义校准与人工复核依旧需要。该模式不做
OCR、原始 DOCX 来源认证、完整抽取证明，也不改变晋升门禁。

## 参考标签校准

`praxile.eval.content_calibration.calibrate_content_review` 接收 1..32 个显式
开发样例及每例固定输出额度。每例包含 source_text、output_text、requirements
及 expected 参考标签。全部输入和标签覆盖在调用前验证；expected、case ID
不进入 judge 消息，只在返回后对照。每例最多一次逻辑请求，不自动修复或重试。

报告区分协议校验与语义参考一致性：记录 decided/abstention、全体准确率
（弃权计不正确）、有判定时的条件准确率、preserved precision 与错误保留率。
uncertain 或调用失败保留为弃权；无分母的指标是 null。无置信概率时不伪造
概率校准误差，probability_calibration_error 保持 null；错误保留率不是实际
promotion 率。标签只是调用方提供的参考，不由接口认证。

`tests/fixtures/content_review_development.json` 是人工编写的四个开发冒烟样例，
不是独立人工审核集或 held-out 数据。一次四例匹配不能证明真实复合要求的
准确性、统计可靠性或广泛抗注入能力，也不会改变生产策略或晋升状态。

## 同条件描述性比较

`praxile.eval.content_review_compare.compare_content_reviews` 接收两份 batch 报告
及双方明确的 judge 身份摘要。校验计划摘要、源文/要求摘要、预算/分组/模式等
条件，输出文本摘要允许不同；judge 摘要或条件不一致返回 inconclusive。
任何一侧未完成同样返回 inconclusive，不删除失败组来计算“完整”比较。

两侧完整时复核逐组状态、输出快照、角色、模式、预算、ID 覆盖和合并一致性，
返回逐要求的 baseline/candidate 判定及保留项数量。uncertain 仍是未知，不
强行按失败或胜负排序；不产生奖励、显著性结论、总分或晋升许可。

身份摘要由调用方声明，不是独立来源认证。比较器无法凭报告证明正文抽取
完整、引文相关或 LLM 判断正确；生产门禁不可把它当官方 evaluator。
事后评审仍需注明调参历史、顺序和有限样本，不能将挑选成功的旧单侧记录拼成
公平对照。报告可以指明代码、模型和请求选项摘要，私有配置或凭据不得公开。

## 持久化运行命令

不再需要临时脚本来启动一次 batch 评审：

```sh
python -m praxile.eval.content_review_run --project /path/to/project \
  --inputs /path/to/review-input.json --run-id review-001 \
  --total-output-tokens 8000 --batch-size 4 \
  --output-allocations 1800 1400 1800 3000
```

输入文件只包含 `source_text`、`output_text`、`requirements`，上限 1 MiB，严格
JSON，不接受附带参考标签。必须配置显式 `model_roles.content_review_judge`
角色的 provider/model；模型应声明 supports_tools。默认私有模式要求实际
选中的端点是 localhost、127.0.0.1 或 ::1。获准发送给云端的公开材料须显式
增加 `--public`，并使用自己配置的云模型与环境变量凭据。loopback 不是沙箱
认证，调用方仍需信任本地服务，不能用代理转发冒充隐私保证。

结果写入 `.praxile/eval/v2/content_reviews/<run-id>/`，包含 manifest、输入快照、
trajectory 和 report。新运行目录权限为 0700，拒绝状态路径中的符号链接。
manifest 记录主路由身份、去敏配置摘要、代码摘要和预算；这不是远程模型
实现或原始文档来源的认证。已存在 ID 一律拒绝，成功或失败都不覆盖。

每次响应后会保存轨迹，最终保存完整报告；进程被强杀可能留下 pending 状态。
这种状态不能被视作从未调用，也不会自动恢复或重发。需要人工检查后使用新
ID 发起新实验，明确记录它不是原运行的成功替换。

完整模型响应默认不保存；需要本地诊断时可加 `--retain-responses`。输入、
轨迹中的引用和可选原始响应都可能含敏感材料，不应提交 Git。退出码 0 仅表示
批次评审完成，不表示内容全部保留；弃权返回 1，启动/配置/存储错误返回 2。
网络重试、计费和总体 wall time 仍受各自约束，不由输出 token 总额认证。

## 从运行目录离线比较

```sh
python -m praxile.eval.content_review_files \
  --baseline /path/to/project/.praxile/eval/v2/content_reviews/baseline-001 \
  --candidate /path/to/project/.praxile/eval/v2/content_reviews/candidate-001
```

命令只读取文件，不调用模型。逐一核验 manifest 输入/身份摘要、计划与输入
快照、分组额度、报告与轨迹内两处记录、引用片段与正文及偏移。拒绝符号链接
证据文件、重复 JSON 字段和非有限数，每个文件上限 16 MiB。缺失、损坏或不
一致退出 2；条件不匹配或未完成退出 1；可作描述性比较退出 0，仍不代表获胜。

支持新 content_review_run 目录格式，不猜测旧临时脚本格式或自动迁移历史
报告。pending/中断且缺完整计划的记录不能比较。身份摘要由本地记录者声明，
即使所有文件自洽也不是防伪签名；同时改写完整输入和证据链的攻击不属于
该校验的认证范围。最终仍标记 provenance_verified=False、promotion_eligible=False。

## MiniMax 执行、DeepSeek 独立评审

执行器和评审器可以使用不同供应商。保持 coding/executor 的 MiniMax 路由不变，
只在实验配置中增加以下 provider 和 role（合并到已有节点，不覆盖其他模型）：

```json
{
  "model_providers": {
    "deepseek": {
      "type": "openai_compatible",
      "base_url": "https://api.deepseek.com",
      "api_key_env": "DEEPSEEK_API_KEY",
      "max_retries": 0,
      "models": [{"name": "deepseek-flash", "supports_tools": true}]
    }
  },
  "model_roles": {
    "content_review_judge": {
      "provider": "deepseek",
      "model": "deepseek-flash",
      "request_options": {"thinking": {"type": "disabled"}}
    }
  }
}
```

2026-10-21 核对[官方更新记录](https://api-docs.deepseek.com/updates/)，
`deepseek-flash` 指向 DeepSeek-V4.1-Flash-1017；这是可变别名，不是永久固定版本。
复现实验应保留核验日期、模型列表返回及配置摘要，不只记录“最新模型”。
当前评审使用命名函数强制提交。真实协议探测确认，启用 thinking 时 DeepSeek
拒绝这种 tool_choice（HTTP 400）；因此本配置显式禁用 thinking，而不是绕过
本地 schema、引用或覆盖校验。模型密钥仅从环境注入，不放进 JSON 或提交 Git。

该角色仅覆盖本内容评审接口，不会自动替换所有 semantic judge 或官方评分器。
供应商分离减少了同模型自评依赖，但不证明 judge 已校准或评判正确。已有产物
可单独评审，不需要重新调用 MiniMax 执行器；旧评审结果保留，新结果单独编号。

## 同产物跨 Judge 分歧审查

```sh
python -m praxile.eval.content_disagreement \
  --left /path/to/minimax-review \
  --right /path/to/deepseek-review
```

该入口与 baseline/candidate 成果比较不同：要求**原文、产物、要求列表摘要
全部一致**，才按 requirement ID 比较 judge 判断。显式列出分组额度、总预算、
超时等计划差异；不同 judge 身份允许比较，但不是供应商优劣实验。两份报告
必须完整，任何一侧弃权都返回 inconclusive，不把缺失判断当作反对票。

不调用模型、不投票、不产生真值或准确率。分歧项与任一侧 uncertain 项进入
`review_required_ids`，`reference_verdict` 保持 null，需独立人工复核；一致项也
不代表正确。CLI 复用持久化目录的输入/引文/轨迹校验，拒绝不一致文件；
Python `compare_judgments` 接口仅校验报告内部一致性，不能代替正文或来源认证。

本次旧 MiniMax 报告通过 Python 接口分析，不改写成新目录格式；其身份是从
旧输入回顾性提取，不能声称旧实验具备新 manifest 的全部绑定保证。DeepSeek
使用新目录校验。[真实离线分歧记录](results/judge-disagreement-20261021.json)
识别四项：packet-row-6、packet-row-14、roles、dates。左/右分别为 MiniMax/
DeepSeek；旧标签没有被更乐观的新标签覆盖。仍缺人工参考标签与稳定准确率验收。

## 证据边界

### 复合要求与标签语义

`tests/fixtures/content_review_compound_development.json` 增加六个作者标注的开发
样例，覆盖部分前提遗漏、完整释义、责任人交换、期限遗漏、例外反转与例外保留。
标签仅在控制面比较，不提供给 judge，不是人工认证或 held-out 参考集。
DeepSeek 非思考模式真实六次调用与标签匹配 5/6，0 弃权、0 false-preserved；
期限遗漏被判成 contradicted。见
[真实校准收据](results/deepseek-compound-calibration-20261021.json)。小样本的零
false-preserved 不证明真实文档没有误保留，也不解决已有四项人工复核分歧。

据此新增 `praxile.content_verdict_policy.v1`：保留须涵盖所有实质义务；仅遗漏
应为 missing；显式冲突才为 contradicted；两者同时存在按冲突并解释；证据
不足为 uncertain。每条新报告记录政策版本和摘要，旧报告不回填。普通 A/B
比较拒绝新旧政策混比；分歧审查则明确列出政策差异。

**上述六次真实结果来自修改前的提示词。** 政策实现阶段只完成提示词透传、
摘要绑定及比较器回归测试，不能将这些测试称为真实模型准确率改善。

随后补充的新政策真实复验见
[独立记录](results/deepseek-verdict-policy-v1-20261021.json)：同一六例、模型别名、
非思考设置、1200 输出 token/60 秒每例与零重试配置，得到 6/6 标签匹配，
compound-04 从 contradicted 变成 missing，其余五项保持原判断。每条实际
评审均核对了新政策摘要，旧 5/6 记录不改写。这是经过错误诊断后对同一开发集
的一次复验，不是 held-out、随机重复或因果消融；不证明普遍准确率改善。
参考标签仍为作者声明，已有真实文档的四项分歧仍需独立人工复核。

- 引文校验只证明引用存在，不证明模型理解正确或要求列表完整。
- `all_declared_requirements_preserved` 是 LLM 对已声明要求的判断，不是环境验证。
- `content_preservation_verified`、`requirement_coverage_verified`、
  `official_evaluation`、`promotion_eligible` 均保持 False。
- 本接口不认证文本来源，不检查抽取完整性，也不做视觉检查。调用方须关联
  原始产物哈希、抽取工具/版本及覆盖限制，不能用截断正文证明完整文档通过。
- 用于正式比较时，要求列表须在执行前冻结。对已有六次 DOCX 结果的补充评审
  只能标记为事后诊断，不能追认为预注册官方评分。

请求记录包含逻辑调用数、用量是否可用、有限错误分类及可用时的响应摘要和
结束原因；逻辑请求数不等于实际传输尝试数，用量可用也不等于完整。
若异常链含传输证据，仅投影白名单状态、HTTP 状态码、耗时和最近传输调用的
尝试次数；不复制异常消息、URL、响应正文或请求头。该局部证据不代表所有
fallback 路由的完整尝试账本；缺失/非法证据继续保留未知。

单元测试验证调用、用量记录、引用拒绝与弃权行为。六个真实产物的事后评审
已有两份有效响应、四次请求阶段弃权；没有完整成功配对，不用于排名或晋升。
详见 [真实评审记录](results/docx-content-review-20261021.md)。模型准确性、校准、
完整文档抽取流水线仍未验收。该接口不是 R2 完成证明。
