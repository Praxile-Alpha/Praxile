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
- PDF、DOCX、XLSX、PPTX 等尚未接入可靠 opener，返回 unsupported，不能仅凭后缀或文件存在判通过。

结果包含契约摘要、文件摘要、字节数、结构检查结果，以及 `official_evaluation=false`、`semantic_quality_verified=false`。不返回文件正文，不据此证明内容正确、公式已重算、排版合格或任务成功。仅作本地修复反馈与审计证据，不自动增加 promotion 资格。

## 待完善

完整交付契约仍需允许状态变化、独立证据位置、可信用户确认；格式 opener、公式重算、文档渲染与人工质量验收仍需后续实现。本检查不是对同机恶意进程并发修改文件的沙箱，应在隔离工作区收集产物后运行。

12 项单元测试覆盖结构、缺失/超限/unsupported、路径与符号链接、敏感文件、来源声明和 runtime 分发。此阶段不勾选 R1 的完整 Deliverable Contract/本地自检总项。
