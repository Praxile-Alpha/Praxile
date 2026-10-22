# 原生冻结经验评测

原生执行入口现在支持显式 `heldout_frozen` 候选通道。它复用隔离 lease、固定
worker、语义激活门控与轨迹转换，不开放经验写回或评测反馈生成候选的路径。
这不是五组 held-out 调度或 online-stream 实验的完成声明。

## 冻结协议

在执行前保存 JSON 并记录文件 SHA-256：

```json
{
  "schema_version": "praxile.frozen_native_protocol.v1",
  "development_task_ids": ["dev-001"],
  "heldout_task_ids": ["eval-001", "eval-002"],
  "candidate_file_sha256": "<candidate.json 的 SHA-256>",
  "repository": "public/docs"
}
```

协议与候选文件均受大小、摘要及符号链接检查。集合必须非空、无重复且不相交；
当前 BenchmarkTask 的 split 必须明确为 `heldout`，task_id 必须属于评测集合。
候选 source_task_ids 必须非空且全部属于开发集，不能来自任何评测任务。
这是声明及本地完整性检查，不是外部数据来源认证，也不能证明操作者此前没有
看过评测内容。冻结时点和数据谱系需要完整实验控制面进一步约束。

## 调用

使用原有 `python -m praxile.eval.owned_native_cli` 的任务、worker、模型、凭据
及预算参数，追加：

```text
--development-candidate candidate.json
--development-candidate-sha256 <候选文件摘要>
--repository public/docs
--frozen-protocol protocol.json
--frozen-protocol-sha256 <协议文件摘要>
```

不要同时传 `--development-only`。默认仅预检，显式 `--execute` 才会启动执行。
Python 调用 `run_owned_native(..., frozen_protocol=protocol)` 同样先检查集合和
候选再分配 lease。CLI 校验文件摘要；Python 调用者负责预先冻结提供的协议对象。

为了兼容历史数据，本阶段保留 development_candidate 参数及 development_context
字段名，但其 track 为 `heldout_frozen`，experience_mode 为
`heldout_frozen_candidate`。报告与命名 run claim 绑定 protocol_digest，事件转换
也保留 heldout_frozen，不能将其投影成 development。下游只能按显式 track
解释协议，不能仅按历史字段名归类。

完整任务名单仅留在控制面；adapter/worker 收到冻结候选和模式，不收到完整协议、
私有 evaluator 或其他评测任务材料。worker 关闭长期经验检索，候选仍需通过
任务语义与 anti-scope 检查。可显式开启的任务内临时反思不写长期资产。

## 验证边界与回滚

单元测试覆盖来源、集合、摘要、混用拒绝、CLI 调度、worker 使用与 trace 标记；
Harbor 兼容测试以替身环境验证上传、控制器透传与结果拒绝，不代表真实容器或
真实模型质量验收。既有 development 行为保持默认，旧五组调度仍只接开发集。

回滚时恢复代码版本即可；不重写历史报告、候选或协议。尚未实现五组 held-out
调度、online-stream 经验更新与顺序账本、跨协议汇总防混用及真实迁移验收。
