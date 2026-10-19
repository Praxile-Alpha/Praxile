# 文档读取工具

`read_document` 是 Runtime/Action Schema 中正式的只读动作，不需要允许通用
Python shell。安装 `praxile[documents]` 后使用：

```json
{"type":"read_document","path":"task_folder/reference.pdf","start":1,"limit":5,"char_offset":0}
```

- PDF 的单位为页，XLSX 为行（可加 `sheet`），DOCX 为正文段落/表格块。
- `start` 从 1 开始；每次 1–20 单位，默认 5。`next_start` 提示后续单位。
- 单位文本最多返回 4,000 字符；用同一个 start、limit=1 和返回的
  `next_char_offset` 继续读取长单位。`source_sha256` 标识本次文件快照。
- XLSX 返回公式文本而不是重算结果，最多读取前 128 列；超过时显式标记
  columns_truncated。更多列、合并单元格语义与公式重算尚未支持。
- PDF 不执行 OCR 或视觉验收。DOCX 读取正文与表格，不声称覆盖页眉、批注、
  修订解释或版式质量。

## 执行与安全边界

复用敏感路径与项目目录检查。最多复制 20 MiB 输入到临时快照，在固定解析脚本
子进程中读取，不执行宏或模型提供的代码；不把原始二进制文本送入模型。
原 `read_file` 对 PDF/XLSX/DOCX 提示改用此工具。

`edit_file` 同样拒绝 `.docx/.xlsx/.pptx/.pdf`，包含大小写变体；不能把文本或
base64 当成二进制产物，也不会先解码并备份已有二进制文件。DOCX 应使用
create_docx，其他格式尚未提供正式写出时明确不支持，不以错误扩展名降级。

子进程 25 秒超时并回收；POSIX 限制 CPU 20 秒与单个输出文件 1 MiB，Linux
另限制地址空间 1 GiB。macOS 没有该地址空间限制，Windows 没有 POSIX rlimit；
因此这些限制不是跨平台完整资源沙箱。OOXML 声明的总解压大小最多 64 MiB，
解析异常和缺依赖返回 failure。当前不允许 read_document 在 batch 内并发。
文件快照不是抵御同权限进程竞态替换的 OS 安全隔离。

Harbor 适配器新增 `inherit_task_packages: true` 显式选项，允许 Praxile venv
访问任务镜像已有的解析依赖；默认 false。选择会写入 execution record，实际
版本由冻结任务镜像决定。该选项不在线安装依赖，不修改原始 benchmark 配方，
也不代表已经完成新版 wheel 的真实模型任务验收。

## 受控 DOCX 创建

```json
{"type":"create_docx","path":"output/memo.docx","document":{"blocks":[{"type":"heading","text":"Award memo","level":1},{"type":"paragraph","text":"Draft for review."},{"type":"bullet","text":"Verify recipient"},{"type":"table","rows":[["Item","Amount"],["Award","$35,000"]]},{"type":"page_break"},{"type":"paragraph","text":"Checklist"}]}}
```

这是声明式内容，不接受脚本、模板代码、宏、外链或任意 OOXML。最多 200 块、
256 KiB spec；表格最多 100 行/12 列。只创建新 `.docx`，目标父目录必须已存在。
复用写权限检查，临时子进程生成成功后，在目标目录 staging，通过 hard link
原子发布，目标若已存在则失败而不覆盖。要求文件系统支持 hard link；不支持时
明确失败，不退化成非原子覆盖。该操作不是多文件事务，也不承诺断电持久性。

动作归类为 mutating，dry-run 不写入，不允许并发 batch；成功后刷新快照，
轨迹记录路径、大小、SHA-256。任务回滚只删除仍匹配该摘要的新文件，若人工已
修改则记录 skipped_modified，避免删除后续工作。不支持在原文件上修改 Word
复杂样式或保持修订历史；同路径完整重建使用下述 revise_docx，不能把它当成
保留模板/批注/修订历史的局部 Word 编辑器。

使用默认 Word 样式、Calibri 11pt 与表格网格，不声称符合机构模板。创建成功
只代表结构化文件写出，事实正确、页数、视觉排版及官方评分均未验证。

### 摘要保护的同路径修订

```json
{"type":"revise_docx","path":"output/memo.docx","expected_sha256":"<CURRENT_SHA256>","document":{"blocks":[{"type":"paragraph","text":"Shortened memo."}]}}
```

expected_sha256 必须是 read_document、check_document_layout 或前次写入观察
得到的当前文件摘要；schema 要求 64 位小写十六进制。完整 blocks 重新生成
DOCX，旧样式/批注不会被自动保留。只允许现有项目内 DOCX，拒绝符号链接组件。
原稿上限 10 MB，生成器沿用 create_docx 的资源限制和声明式白名单。

新稿在目标目录的临时子目录中完整生成后，获取项目内按路径归一化的文件锁，
重新核对当前摘要；原始二进制备份写入 `.praxile/backups/document-revisions/`
并 fsync，再核对一次后原子替换。备份失败不替换，替换失败保留原稿和备份，
临时生成目录清理。轨迹记录 before_sha256、sha256、备份路径及 changed。
修订动作受写权限、架构门禁、dry-run、非并发 mutating 分类和快照刷新约束。

回滚只在当前摘要仍等于该次修订结果、备份路径/摘要均有效时恢复原稿；人工
后改则 skipped_modified，损坏备份则 backup_digest_mismatch，不静默覆盖。
这是协作进程锁与摘要检查，不是抵御所有非协作进程竞态的 OS compare-and-swap。
不承诺备份与轨迹的断电原子事务；崩溃或日志写入失败后可能需人工检查保留备份。

真实 LibreOffice + 模型 fixture 已验证：两页稿完成被拒绝、同路径修订、
再检查为一页、完成、按轨迹恢复原稿。尚未进行真实模型自动修订验收。

## 验证与剩余边界

### 原生产物清单检查

Harbor 的 `output_directory: "output"` 显式选项（worker 参数
`--output-directory output`）在执行后产生独立 `artifact_inventory`。旧调用未
配置时行为不变。目录必须在 workspace 下，不能传根目录或 `..`。扫描不跟随
符号链接，最多 200 个文件/目录条目、单文件 10 MB、30 秒总检查预算；文档
解析复用既有 artifact_openers，单次最多 15 秒。结果含文件摘要、大小和原因。

状态区分 missing / empty / issues / clean_structure / incomplete /
inspection_error / blocked。未知格式保留 unsupported，不能算通过。
扫描完成不等于文档有效，格式有效也不等于事实正确或满足用户要求。
清单来自实际输出而非用户约定，source=observed_outputs，绝不伪装成
user_request contract；quality_verified 与 required_outputs_verified 均为 false。
模型轨迹原始结果保持不变，消费者必须单独查看 artifact_inventory 和正式
verifier，不能只依赖进程结束或模型 completed。检查仍在 worker 环境执行，
不是独立可信见证；官方评分继续物理隔离。

4 项资源测试验证伪 DOCX、真实 DOCX、空/缺失/未知文件、符号链接与扫描上限。
worker fixture 端到端验证模型 completed 与清单 issues 可同时存在。旧真实
实验的两个无效产物离线复查均判 failed，分别为解析失败和空文件；未调用模型。

验证记录：5 项资源测试覆盖真实格式、分页/字符续读、损坏文件与超时返回；
6 项 schema 测试覆盖参数范围和禁止批量并发。另用冻结 JobBench easy 任务
的四份公开参考材料执行首页/首行提取，均成功：PDF 分别 1、29、16 页，
XLSX 默认表 22 行。这只证明本地解析可用，不代表完整读取、理解或评分通过。

另有 3 项创建资源测试验证真实 DOCX 回读、禁止覆盖、路径拒绝和按摘要回滚，
7 项 spec/schema 测试验证拒绝非法块及 mutating 分类。

当前已补齐读取和基础结构化 DOCX 新建。XLSX/PDF 写出、渲染检查、公式重算、完整
产物链路与 JobBench 全量验收仍需后续实现和实测。不能因提取成功而勾选整个
R1 专业交付能力，也不能宣称 benchmark 得分提升。

2026-10-19 单任务复验：0ba84dd 下两次 create_docx 均成功，官方独立 verifier
给出 73/82（0.8902），执行器和 judge 都使用 MiniMax-M3 的分离调用。该结果
不是独立模型族验证或 A/B 收益。更重要的是本地逐页渲染发现 checklist 有
5 页，而公开任务要求 1 页，模型却报告已满足。结构检查没有发现该版式问题。
本次原始交付物不再修改，作为失败证据保存。后续需要将受控渲染、页数约束
和修订闭环接入 runtime，而不是把结束后的人工检查写成已实现的 Agent 能力。
公开摘要见 `results/jobbench-inventory-smoke-20261019.json`；不发布 rubric 原文。

## Runtime 页数检查

`check_document_layout` 已接入 Action Schema、ToolRegistry 和路径权限检查。
先安装 `praxile[documents]`，再由用户在配置中显式指定受信任的 LibreOffice：

```json
{"documents":{"renderer_executable":"/absolute/path/to/soffice"}}
```

默认空字符串禁用，模型动作不能提供执行路径。调用示例：

```json
{"type":"check_document_layout","path":"output/checklist.docx","max_pages":1}
```

返回真实渲染 PDF 页数、输入/PDF 摘要、page_limit_met；超页为 failure，未
指定上限只报告页数。上限来源标记为 action_argument，不伪装成用户确认的契约。
提示模型缩短内容、用摘要保护的 revise_docx 修订、再次检查；检查动作本身
不覆盖原文件，不静默缩字号。
已用真实两页 fixture 验证失败及一页修订通过；上一轮真实 checklist 的离线
复查同样返回 pages=5、page_limit_met=false，未调用模型、未修改原始产物。

当前仅 POSIX；临时快照 10 MB，ZIP 展开声明上限 50 MB/2000 条目，拒绝宏、
嵌入对象、外部关系与 XML 实体；固定子进程 45 秒超时后回收进程组，CPU 30 秒、
单文件 20 MB，Linux 地址空间 2 GB。使用独立 LibreOffice profile，子进程
不继承模型 API Key。此防御不是网络或 OS 沙箱；应在隔离容器中处理不可信文档，
信任配置的可执行文件。PDF 在临时目录中检查后删除，尚不提供页面图像回传。

本动作只验证页数，不检查表格断行、视觉质量或事实正确，也不是官方评分。
Harbor 已支持 `renderer_executable`（容器内可信绝对路径），透传至 worker
`--renderer-executable` 并记录 execution metadata；空字符串继续禁用。
配置本身不安装 LibreOffice，也不证明对应镜像具备渲染依赖。

### 用户契约与完成门禁

caller-bound 交付契约中的 DOCX 可以声明：

```json
{"schema_version":"praxile.deliverable_contract.v1","source":"user_request","outputs":[{"path":"output/checklist.docx","media_type":"application/vnd.openxmlformats-officedocument.wordprocessingml.document","structure":{"max_pages":1}}]}
```

调用方必须明确提供/确认契约；不从隐藏 rubric 推断，不把模型自拟上限提升为
用户要求。`check_deliverables` 比较结构检查与渲染输入摘要；缺渲染器、摘要变化
或超页均不通过。原始纯函数调用未提供 layout_checker 时返回 unsupported，
不会跳过要求。上限限制 1..2000，不接受布尔值、字符串或零。

Runtime 在 completed finish 前重检绑定契约，不通过则将证据返给模型，允许
在剩余 max_steps 内修复或转 needs_human；不会扩充步数预算。最终落盘前仍再次
检查，防止仅凭早期成功结果宣称通过。内部检查不计为模型工具调用。
fixture 验证了 JSON 产物的拒绝/修复/完成，真实 LibreOffice 验证 DOCX 超页
拒绝；无渲染器的模型 completed 被降为 needs_human。

同路径修订工具已实现并通过真实渲染 + fixture 流程；真实模型 DOCX 自动
修订尚未验收，完整自主修订能力仍不能宣称完成。

### Benchmark 公共契约输入

Harbor `PraxileAgent` / `WorkBuddyPraxileAgent` 可通过 `deliverable_contract`
参数接收上面的公共 JSON 对象。控制面在构造 agent 时验证、深拷贝并规范化为
不可变字符串；调用方随后修改原字典不会改变实验契约。上传文件的 SHA-256
写入 `public_contract_sha256`，worker 在读取凭据和创建 Runtime 之前检查摘要、
64 KiB 上限、普通文件/非符号链接以及严格无重复字段 JSON，再绑定 Runtime。

直接启动 worker 时使用成对参数：

```text
--deliverable-contract-file /absolute/path/to/public-deliverables.json
--deliverable-contract-sha256 <SHA256_OF_EXACT_FILE_BYTES>
```

API `run_native(..., deliverable_contract=...)` 同样在构造 Runtime 前验证并复制。
未提供契约保持原行为；提供后，既有完成门禁对产物要求生效，包括配置好
renderer 时的 max_pages。契约只来自调用方明确授权的公开要求；本功能不读取
rubric，也不能证明任意调用方标注 user_request 的对象真的得到用户确认。
摘要是完整性校验而非 OS 隔离证明，不能代替隐藏评审材料的独立容器边界。

本机 worker + HTTP fixture 验证有无契约的行为分离，非法摘要/重复字段/过大
文件/符号链接在模型调用前拒绝。真实 Docker fixture 验证上传、读取、门禁和
容器回收，不等同于真实模型 DOCX 修订验收。当前验证 wheel 摘要：
`327b9d47c61888e0050167832096757bb00b905393c44f64e15141098b119316`。
Harbor 0.18 与 0.22 各 2 项 Docker 测试通过；本机 worker 集成 8 项、输入
契约单元 7 项通过，fast suite 582 passed。本轮没有付费模型调用。

## Harbor 渲染依赖预检

配置 `renderer_executable` 时，`setup()` 在 worker 虚拟环境的隔离 Python 中
导入 `python-docx`、`defusedxml`、`pypdf`，并检查 POSIX 与渲染器可执行路径。
预检限时 15 秒，失败即停止 setup，不读取模型凭据、不开始模型调用；错误不会
回显容器原始输出。未配置渲染器的任务保持既有安装行为。

这只是依赖可用性检查，不证明实际渲染成功或产物合格。安装仍为
`--no-index --no-deps`，不会自动联网补依赖或改动冻结镜像。实验所需额外依赖
须明确授权、固定版本并记录摘要；改变依赖后的环境不能冒充原环境严格 A/B。
Harbor 0.18/0.22 各 18 项适配器测试通过（各 2 项跳过、6 项排除）。

## 固定紧凑版式

`create_docx` 与 `revise_docx` 的 document 可选 `"layout":"compact"`，默认
`standard` 保持原渲染行为。compact 使用 Letter 纸、0.6 英寸页边距、10pt 正文、
9.5pt 表格和单倍行距；表格首行加粗并跨页重复，行不拆分。没有任意模板、字体
路径、宏或用户脚本入口，不承诺任何内容都能装进一页；仍需渲染验证。

12 步真实 MiniMax 产物修订实验执行了三次 revise_docx，checklist 从 5 页逐次
变为 4/3/2 页，memo 摘要不变，但最终 needs_human。详情见
`results/jobbench-seeded-repair-20261019.json`。该实验使用原有产物和单独修订指令，
不是原始 benchmark 成功，也不是 A/B 效果验证。

随后对最后一次模型修订的相同 blocks 应用 compact，离线本机渲染为一页并检查
截图，无文字裁切或重叠；不代表事实正确、信息完整，也不是模型自主选择 compact
的证据。新生成 DOCX 摘要为
`6db4733c63e74fe8a577f80a49daa164ea55fc3035f55504846c2b9cfc63861a`。
606 项快速单元测试、8 项文档修订/版式资源测试通过。

## 模型输出截断诊断

OpenAI-compatible 的 `finish_reason` 与 Anthropic 的 `stop_reason` 统一保留为
`finish_reason`，写入模型响应 trace 和调用成本的路由记录。动作循环遇到
`length` / `max_tokens` 时，即便文本恰好能解析成 JSON，也不执行其中的动作；
记录 `failure_kind=output_truncated`，保留已报告 token，并要求返回更短的完整
动作。其他解析失败仍记录为 `invalid_json_action`，沿用既有重试上限。

缺少终止元数据不推断为截断；此前真实实验未保留该字段，不能追溯断言它们
就是 token 上限导致的失败。此修改不自动提高预算，也不代表产物质量已通过。

### 动作解码失败的证据

非截断响应解析失败时，轨迹现在保存 `decode_evidence`（版本
`praxile.action_decode.v1`）：完整响应 SHA-256、字符数、已移除 reasoning 块数、
最终段字符数及失败原因。JSON 语法错误保留解析器消息和偏移量，偏移相对于
该解析候选的修复后文本，不是原始含 reasoning 响应的位置。
原因区分 `unclosed_reasoning`、`nested_reasoning`、`missing_final_action` 和
`invalid_json_object`；证据不包含完整正文。既有 4000 字符预览新增
`content_preview_truncated` 标记，不能用该预览重新构造完整失败响应。

修复提示明确说明动作没有执行、不得假设文件已改变，并附解码诊断。
解析边界和重试预算不放宽，成功解码后仍经过 Action Schema 与安全策略。
60 KB 的合法 DOCX 修订 JSON 测试可解析；这仅排除了固定小长度上限的假设，
不能追溯证明上一轮真实 MiniMax 无效 JSON 的具体原因，也不是成功修订验收。
输入 DOCX 尚未创建时，分页工具返回 `input_missing` 且不启动渲染进程，避免
把缺文件误报为依赖故障。此处检查仍先经过路径安全策略。
