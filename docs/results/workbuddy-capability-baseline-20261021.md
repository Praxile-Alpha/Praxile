# 能力预检后 Office 真实复验

2026-10-21，提交 cae4353 的开发 wheel，MiniMax-M3.1-Flash / low，固定
WorkBuddy Office 库存任务、镜像、20 步和 600 秒预算。只有一个 baseline，
没有 candidate，不启用经验，也没有新增付费 LLM judge。本次使用官方规则评分。

相对上一轮新增三个固定 wheel：openpyxl 3.1.5、et-xmlfile 2.0.0、defusedxml
0.7.1，仅安装到 worker venv。声明 xlsx_values.v1，在实际任务环境内先完成
创建、读取、opener、literal 回读预检，再启动模型。版本及安装摘要见
[机器收据](workbuddy-capability-baseline-20261021.json)，依赖摘要见
[能力说明](../WORKER_CAPABILITIES.zh-CN.md)。构建包含本地 README 改动，非 pristine release。

## 实测结果

| 观察 | 结果 |
| --- | --- |
| worker 功能预检 | passed |
| 任务执行 / finish | 完成 |
| 产物 opener | passed，之前为 unsupported |
| check_deliverables | success |
| 官方规则评分 | 13/15，0.8667，partial_pass |
| create_xlsx / revise_xlsx 实际调用 | 0 |
| Shell 调用 | 1 次被现有安全策略拦截，1 次成功 |
| schema 失败 | 0 |
| 已观测/留存产物摘要 | 一致，1 个 XLSX，26412 字节 |
| 模型用量 | 11 次响应，82537 输入 / 1884 输出 token |
| 供应商请求耗时合计 | 29.292129 秒，非完整任务耗时 |
| 凭据与资源 | broker 已撤销、无在途请求；实验标签下容器/网络/卷均为空 |

输入快照前后相同，36 个规范化 trace 事件可绑定原始结果。第一次启动在输入
快照阶段因 /tmp 符号链接被拒绝，未启动模型；随后显式解析依赖物理路径，在新
目录进行上述执行。没有清除首次失败，也没有自动重试模型请求。

## 结论边界

这轮验证了真实任务中缺失文档依赖的问题已消除，而不是验证了任务质量提升。
官方仍有两项失败，分数没有提高；模型仍选择 Python/Shell 创建文件，不能声称
新 XLSX 创建/修订工具已通过真实模型调用验收。opener 成功只证明结构可读，
不证明分类语义、公式重算或视觉质量。

环境依赖、代码及模型采样均可能影响用量差异，因此不以比上一轮少的 token
宣称效率提升。任务与 verifier 环境的独立性仍为 unknown，产物摘要一致也不
构成 verifier 实际输入认证。没有经验晋升、没有正式 held-out 改善结论。
评分细节保留在隔离证据目录，不将隐藏检查项、答案或 rubric 写入经验资产。
