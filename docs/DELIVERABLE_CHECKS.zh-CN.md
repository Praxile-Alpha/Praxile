# 公开交付契约与本地检查

目标：按用户明确要求检查产物，不读取隐藏评审标准，不替代官方 verifier 或人工验收。保持原有 BenchmarkTask/OutputContract 的 V1 序列化不变。

## Contract 与 Runtime Action

### 调用方绑定与最终复验

```sh
praxile run "生成报告" --deliverable-contract requirements.json
```

JSON 文件使用本文 V1 或 V2 契约格式，路径相对项目根目录解析（也可显式绝对路径），
限制为 64 KiB 常规文件。CLI 在调度原地/隔离工作区前读取并验证，运行期间不重读
可被 Agent 修改的文件。库调用可传 `AgentRuntime.run(..., deliverable_contract=contract)`，
共用 RunService 透传该参数。独立 React Console 已提供可选 JSON 输入；内置 Python Console 尚无对应控件。

Web API 现在支持同一 JSON 对象：同步 `POST /run`、`POST /api/runs`，异步
`POST /api/runs/jobs`，以及聊天 session 的 message/message-async。示例请求：

```json
{
  "task": "生成报告",
  "deliverable_contract": {
    "schema_version": "praxile.deliverable_contract.v1",
    "source": "user_request",
    "outputs": [{"path": "report.json", "media_type": "application/json"}]
  }
}
```

API 只接受内联对象，不读取请求指定的服务器文件路径；非法或超过 64 KiB
序列化大小的契约在执行/入队前返回 400。RunService 共用校验和复制逻辑，队列
保存独立副本，Runtime 保存轨迹/checkpoint；内存中的 job 队列不因此具备进程重启恢复。
GET run 详情返回冻结契约、摘要和 deliverable_verification，旧 `/run` 返回摘要与复验结果。

聊天记录保存公开契约；retry/retry-async 自动继承最后用户消息的要求，不能通过
省略参数丢失绑定，也不能显式 null 删除。改变要求须发送新消息。沿用现有 Gateway
鉴权和权限策略，这不是新权限授权。React Console 的 Chat 输入支持勾选绑定要求、
编辑 JSON、提交前解析/大小校验；job 完成后自动显示冻结要求与逐项复验状态。
前端校验不代替后端，局部 passed 不会显示为官方评分通过。生产构建通过，
Playwright 桌面/手机两项 fixture API 交互测试及截图检查通过；内置 Python Console
和真实模型端到端 UI 验收仍未覆盖。

运行时复制并规范化契约，将副本及 SHA-256 写入 trajectory，随既有 checkpoint 保存，
并将要求提供给模型。check_deliverables 请求不能替换/弱化绑定契约；返回给调用方的
副本也不能修改内部绑定。resume 自动恢复原契约，显式提供不同契约或更改原任务文本
会被拒绝，需开启新任务。无绑定的旧任务和模型自行提出的检查保持兼容。

结束时 Runtime 独立执行一次绑定要求检查，不依赖模型是否调用过检查工具。
若模型声明 completed 而检查失败/未知/安全策略拒绝，则结果降为 needs_human。
该自动检查以 deliverable_verification 动作保存，不计为模型工具调用；报告标记
contract_binding=run_argument，未绑定则 model_proposed。取消的未完成任务不因此变成成功。

绑定证明“本次调用方明确提供的要求未被模型替换”，并非带签名的人工审批，
不授予读敏感文件/越权操作权限，也不证明自然语言要求被完整翻译进契约。
V2 的前后状态变化、可信观测持久化和官方评分仍是独立未完成项。
验证含 CLI 分发、不可变副本、模型弱化拒绝、终态强制复验与 resume 冻结；
完整快速单元测试为 458 passed / 6 skipped / 10 deselected。

### V2 终态约束

V1 文件契约与报告保持原样。V2 增加必填 `state_equals`（1 到 20 条精确终态要求），
允许 `outputs: []` 表达纯状态交付。正常 ToolRegistry 从既有浏览器会话读取状态，
不启动浏览器，不使用 action 中模型声称的 observed_state；观察缺失时 unknown，
值不符或类型不符时 mismatched。只有所有状态匹配且文件要求通过才 passed。

```json
{
  "type": "check_deliverables",
  "contract": {
    "schema_version": "praxile.deliverable_contract.v2",
    "source": "user_request",
    "outputs": [],
    "state_equals": {
      "browser.session_active": true,
      "browser.origin": "https://example.com:443"
    }
  }
}
```

当前观测项为 browser.session_active、去敏且含端口的 browser.origin 和
browser.chromium_version。其他点路径可被契约表达，但未观测到就不会通过。
独立调用 `verify_deliverables` 时，可信调用方通过 observed_state 提供观测；
不能将模型文本当作可信观测。报告复用条件检查，记录每项匹配/不符/未知。

V2 报告明确 `state_transitions_verified=false` 和
`contract_provenance_verified=false`：这是本次检查时的终态，不证明执行途中
没有越界变化；source 标签也不是用户确实确认该契约的证据。
前后状态差异、允许变化范围、可信用户确认及持久化观测来源仍待接入。
33 项交付单元测试通过，完整快速单元测试 455 passed / 6 skipped / 10 deselected。

### V1 文件要求

```json
{
  "type": "check_deliverables",
  "contract": {
    "schema_version": "praxile.deliverable_contract.v1",
    "source": "user_request",
    "outputs": [
      {
        "path": "output/report.json",
        "media_type": "application/json",
        "required": true,
        "required_fields": ["summary", "sources"]
      },
      {
        "path": "output/table.csv",
        "media_type": "text/csv",
        "required_fields": ["name", "count"]
      }
    ]
  }
}
```

该 action 已注册 schema、工具分发和 runtime 提示词，通过既有 action/observation 轨迹记录检查结果。`source=user_request` 只是声明，不是可信来源证明：Agent 自行构造的契约不能作为独立验收标准。控制面仍需核对它与用户要求是否一致；不允许把私有 rubric 改个 source 标签后交给 Agent。

## 当前检查范围

- JSON：可解析，拒绝 NaN/Infinity，required_fields 检查顶层字段名。
- CSV：UTF-8 编码，非空且唯一表头，所有数据行宽度一致，required_fields 检查表头。
- UTF-8 纯文本/Markdown：可解码且非空；尚不支持段落/标题结构要求。
- 必需文件缺失、过大、非普通文件、符号链接明确失败；optional 缺失单独记录。
- 每个文件最多读取 10 MB，最多 100 个输出要求；无 glob，不执行产物，不联网。runtime 额外执行敏感路径读取策略。
- PDF、DOCX、XLSX、PPTX 使用 `praxile[documents]` 的可选解析库在子进程中重新打开；缺依赖时返回 unsupported，不凭文件后缀判通过。

结果包含契约摘要、文件摘要、字节数、结构检查结果，以及 `official_evaluation=false`、`semantic_quality_verified=false`。不返回文件正文，不据此证明内容正确、公式已重算、排版合格或任务成功。仅作本地修复反馈与审计证据，不自动增加 promotion 资格。

## 待完善

完整交付契约仍需允许状态变化、独立证据位置、可信用户确认；公式重算、文档渲染与人工质量验收仍需后续实现。本检查不是对同机恶意进程并发修改文件的沙箱，应在隔离工作区收集产物后运行。

12 项单元测试覆盖结构、缺失/超限/unsupported、路径与符号链接、敏感文件、来源声明和 runtime 分发。此阶段不勾选 R1 的完整 Deliverable Contract/本地自检总项。

## Office/PDF 打开检查

安装 `python -m pip install 'praxile[documents]'`，即可通过相同 action 检查标准 PDF/DOCX/XLSX/PPTX MIME 类型。文件先复制到私有临时目录，子进程使用 Python `-I` 隔离启动，15 秒超时；Linux 增加 1 GB 地址空间上限。它不是 OS 沙箱，macOS/Windows 没有同等内存上限，不可信文档仍应放在受限容器中。

Office ZIP 最多 2000 成员、声明解压后最多 50 MB；拒绝重复成员、加密包和 VBA 内容。XML 在库解析前经 defusedxml 检查，禁止 DTD/实体声明，包括 UTF-16 编码。XLSX 以只读模式遍历最多一百万个单元格并关闭工作簿；PDF 限制页数且拒绝加密文件。

输出记录 opened 和页/幻灯片/工作表等结构信息，明确 `rendered=false`，XLSX 明确 `formulas_recalculated=false`。二进制格式的 required_fields 暂不支持，返回 unsupported，而不是忽略要求。解析成功不代表正文正确、图像可读、公式有最新缓存或页面布局正常。

### 用户结构要求

输出项可增加 `structure`，只支持与格式对应的明确要求：

| 格式 | 结构约束 |
|---|---|
| CSV | `min_rows`，不含表头 |
| XLSX | `required_sheets`，精确工作表名称 |
| DOCX | `required_headings`、`min_tables`；标题取正文 Heading1–Heading9 段落，去除首尾空白后精确匹配 |
| PPTX | `min_slides` |
| PDF | `min_pages` |

例如 XLSX 输出项可声明 `"structure":{"required_sheets":["Summary","Details"]}`。没有这些工作表时即使文件能打开，仍会得到 `status=failed`、`reason=user_structure_requirement_unmet`，并保留 `opened=true` 和具体缺失项。无效字段、类型不符或格式不支持的约束在解析契约时拒绝，不静默忽略。

`required_fields` 仍用于 JSON 顶层键/CSV 表头，不能用它表达 Office 任意结构。DOCX 页数需要渲染，尚不支持；标题检查不覆盖表格内文本、页眉页脚、自定义标题样式或语义等价标题。结构检查不替代内容评审。

增补验证：19 项单元测试、9 项真实格式资源测试通过，后者对四种格式分别验证满足与不满足结构要求的情况。

9 项资源测试覆盖四种真实格式生成后重新打开、损坏文件、超时、宏、ZIP 成员限制以及两种编码的 XML 实体攻击。使用 `tests/resource/test_document_deliverables.py --forked` 运行；未安装可选依赖时明确跳过。
