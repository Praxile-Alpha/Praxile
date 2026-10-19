# 公开交付契约与本地检查

目标：按用户明确要求检查产物，不读取隐藏评审标准，不替代官方 verifier 或人工验收。保持原有 BenchmarkTask/OutputContract 的 V1 序列化不变。

## Contract 与 Runtime Action

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
