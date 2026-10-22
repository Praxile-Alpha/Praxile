# 五组真实容器链路验收

日期：2026-10-22。范围为基础设施集成测试，不是 WorkBuddy/JobBench 官方评测，
也不是 MiniMax/DeepSeek 模型能力实验。没有调用付费 API。

## 冻结材料

- 生产代码基线：`54f69ec53ba9b5abf118b175f8ad21354f3d8cae`。
- 镜像：`python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`。
- 新构建 wheel：`praxile-0.1.0-py3-none-any.whl`。
- Wheel SHA-256：`670725135c9d85a693485fdd2c0a82b5db57c868ce2b4fc745344722c396e789`。
- 执行器和 judge 的 upstream 响应均由测试脚本生成，报告的 token 数也为 fixture 数值。
- 一个公开文本复制任务，一次 repetition，五组共六次隔离执行、五次独立 judge。
- 每 cell 声明 100000 total tokens、12 次调用、180 秒；实际 broker 控制输出额度
  与调用准入，不能将 total_tokens 声明视为实际完整 token 上限。

构建首次因本地环境缺 setuptools 失败；随后使用隔离构建依赖生成新 wheel。
测试初次因候选 evidence_refs 前缀不合法而在执行前失败，修正为明确 fixture 的
event 引用后运行。失败没有混入成功记录或解释为模型失败。

## 验收内容

真实运行 lease 创建、固定 wheel 安装、worker 进程、broker HTTP 转发、持久化
共同额度、产物收集及容器清理。只有供应商 upstream 被脚本替代，不替代
run_owned_native、Harbor lease、runtime、selection、review 或 audit。

最终断言检查所有尝试交付的 answer.txt 字节与源文本一致，cleanup_verified=true，
broker 已撤销；经验组与完整组的 worker trace 中实际 activation/injection=true。
任务内记忆动作没有被脚本调用，不能声称任务内改进已产生效果。完整 audit 和
白名单公开摘要经过同一批真实文件验证。

Harbor 0.22 最终断言版本：1 passed，52.85 秒。Harbor 0.18 首轮：1 passed，
56.46 秒；首轮尚未加入最终文件字节/broker 撤销的额外断言。
Harbor 0.18 使用最终相同断言复验：1 passed，52.81 秒。
默认 fast suite：1684 passed、16 skipped、267 deselected，37.00 秒。

测试有显式 resource/integration/slow 标记，默认 fast 命令不启动容器。复现命令
见 `../FIVE_ARM_STUDY.zh-CN.md`。本记录不包含凭据或私有绝对路径，不自动发布
测试临时目录或原始轨迹；它不是外部来源认证或完整真实模型研究结果。
