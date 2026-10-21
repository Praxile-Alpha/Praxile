# 新表格工具版本的真实 Office 复验

固定原 Office 库存任务、数据摘要、amd64 镜像、Harbor 0.18、MiniMax M3.1 Flash
low reasoning，运行一条新鲜基线。预算 20 步/600 秒，每次模型请求 90 秒，零
自动重试，无候选注入。机器摘要见 [收据](workbuddy-xlsx-tools-baseline-20261021.json)。

执行代码来自 96d468b。wheel 使用当前工作树构建，包含用户已有 README 改动，
不是 pristine release build；未覆盖用户修改或把它们纳入本提交。

## 结果

- 官方 CompositeVerifier 规则结果仍为 13/15、reward 0.8667，partial_pass。
- 原生执行报告 completed，42 个事件与 trial 绑定；这不覆盖自检失败。
- result.xlsx 留存，26743 字节，执行器 inventory 与宿主回收摘要 matched。
- inventory 为 issues，文件 opener 返回 unsupported / install_praxile_documents_extra；
  不能把文件存在或官方部分通过当作 Praxile 自检通过。
- 输入材料未变，broker 已撤销，无 inflight 调用；完成后按该 session 标签查询
  Docker 容器、网络、卷均为空。官方 verifier 独立环境身份仍为 unknown。

## 未达到的目标

模型没有调用 create_xlsx/revise_xlsx。它先请求不支持的文档并发读取，第一次
16 个动作同时违反最多 8 项和文档不允许 batch 两项规则；第二次改为 8 项，但
仍包含 read_document，继续拒绝。随后通过 edit_file 写脚本、run_command 执行，
生成结果。还有一次文档读取 KeyError；没有把这些失败抹去或改成成功。

因此，此次不能算新增 XLSX 工具的真实调用验收。发现工具描述“只读”和 batch
提示“支持只读动作”容易混淆：后续代码改为明确列举 batch 允许动作，强调文档
解析虽只读但必须顺序执行，并给出 standalone 修复提示。没有放宽资源限制，
修复尚未追加真实模型复验，不宣称消除了该失败。

## 成本与解释边界

13 次成功响应：102986 input / 2487 output token，provider 累计延迟 34.682917 秒，
金额未知。此前留存基线为 10 次响应、89035 / 1897 token，两次官方分数相同。
这次调用与 token 更多，但单次、未配对、版本/提示词均变化，不能由此归因于新增
工具或宣称回归/增益。无 DeepSeek 或其他 LLM judge 调用，无 promotion。

下一步需要冻结包含完整文档校验依赖的新 worker 环境，再验证真实工具使用；
环境改变必须作为新的实验条件，不将新分数与旧结果直接冒充严格 A/B。私有轨迹、
脚本正文、文档内容、rubric、凭据和本机路径没有写入公开收据。
