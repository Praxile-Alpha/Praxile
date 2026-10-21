# WorkBuddy 真实产物留存验收

2026-10-21，固定 drug-inventory-split-L2-006，使用 MiniMax-M3.1-Flash、low
reasoning、新构建 worker 与原固定 amd64 镜像，运行一次 fresh baseline。
20 步 / 600 秒 / 单请求 90 秒，未注入候选或经验，未启用官方 LLM judge。

官方 CompositeVerifier 返回 **13/15，reward 0.8667，partial_pass**。这与模型的
completed 自述分别保存，不能把 completed 当成全部规则通过。

本次补齐了此前缺失的产物留存：result.xlsx 已回收到宿主，27,091 字节，摘要
及清单摘要见[机器收据](workbuddy-retention-baseline-20261021.json)。33 个原生事件
绑定到原始结果；运行前后冻结输入一致；凭据已撤销；按本次 Compose session 标签
只读检查，容器、网络和卷均为空。原始证据保留在本机私有实验目录，不上传材料正文。

执行模型返回 10 次响应、89,035 输入 / 1,897 输出 token，provider latency 合计
30.635 秒，worker elapsed 36.240 秒；整个 Trial 还包含环境启动、官方评分与清理。
金额未知，不能将 token-plan 等同于已验证零成本。

首次尝试在 Trial 初始化阶段因找不到上游模块失败，未进入 Agent；保留失败目录，
确认固定上游提交后，在新目录通过 PYTHONPATH 加入该源码完成运行。没有改动任务、
rubric、评分器或原材料，也没有覆盖失败记录。

这不是 A/B。相较 2026-10-19 的 M3 运行，模型、worker 和推理条件不同，即使
token 更少也不能归因为 Praxile 提升。两次官方分数相同，只说明本次集成路径达到
同样的部分通过结果；尚未解释两个未通过项，不据此生成或晋升经验。

官方 verifier 的独立执行环境模式未获证明；留存文件也不独立证明其评分前字节
身份、语义完整或视觉质量。该任务不替代 Code/Web/Security、Linux 支持矩阵或
跨任务 held-out 验收。wheel 含本机既有 README 修改，因此作为冻结实验产物记录，
不称为干净发布构建。无凭据、私有 rubric、完整轨迹或生成文档进入此公开收据。
