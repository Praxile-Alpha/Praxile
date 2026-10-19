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
复杂样式或保持修订历史；要修订内容必须另存新路径，再由用户决定最终版本。

使用默认 Word 样式、Calibri 11pt 与表格网格，不声称符合机构模板。创建成功
只代表结构化文件写出，事实正确、页数、视觉排版及官方评分均未验证。

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
产物链路与 JobBench 官方评分仍需后续实现和实测。不能因提取成功而勾选整个
R1 专业交付能力，也不能宣称 benchmark 得分提升。

## 模型输出截断诊断

OpenAI-compatible 的 `finish_reason` 与 Anthropic 的 `stop_reason` 统一保留为
`finish_reason`，写入模型响应 trace 和调用成本的路由记录。动作循环遇到
`length` / `max_tokens` 时，即便文本恰好能解析成 JSON，也不执行其中的动作；
记录 `failure_kind=output_truncated`，保留已报告 token，并要求返回更短的完整
动作。其他解析失败仍记录为 `invalid_json_action`，沿用既有重试上限。

缺少终止元数据不推断为截断；此前真实实验未保留该字段，不能追溯断言它们
就是 token 上限导致的失败。此修改不自动提高预算，也不代表产物质量已通过。
