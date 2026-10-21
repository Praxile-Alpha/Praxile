# Worker 功能预检

真实 Office 复验曾出现“可以用 Shell 生成 XLSX，但 Praxile opener 缺依赖”。
因此，依赖包安装成功与工具能力可用现在分开记录。Harbor Native 的 agent kwargs
可显式声明：

```json
{"required_capabilities":["xlsx_values.v1"]}
```

当前唯一支持的 profile 是 `xlsx_values.v1`，覆盖新建值型工作簿、指定 sheet 读取、
安全 opener 和 literal 字符串/数值回读。不是公式计算、渲染、业务正确性或
跨任务模型表现的证明。未知或重复 profile 在构造时拒绝。

## 启动顺序

1. 按既有 wheel/SHA-256 清单校验、上传并离线安装到 worker venv。
2. 用该 venv 的 Python 执行 `praxile.capability_probe --profile xlsx_values.v1`，
   单 profile 限时 60 秒，创建临时小工作簿并实际调用工具，不使用任务材料。
3. 保存 `agent/praxile-capabilities.json`，绑定 worker_environment_sha256，包含
   检查项目及实际包版本。失败记录 `praxile-capability-failure.json` 并停止 setup。
4. 只有预检通过才能 run；直接跳过 setup 调用 run 也会拒绝，尚未启动凭据代理
   或模型请求。原先未声明 profile 的配置兼容，不据此宣称做过功能检查。

声明进入 worker 环境摘要、远端安装路径与 agent version，不能将不同声明的
执行当成完全相同环境。报告是本地功能证据，不是针对恶意镜像或宿主的认证。
不额外保证 Harbor 所有命令的统一硬截止或预检后环境不会被改变。

## 固定表格依赖

使用既有 dependency_wheels 参数逐个传入本机 wheel 路径及摘要，不在线解析
依赖、不修改原任务镜像。此次下载由本机配置的 PyPI 镜像提供，摘要已记录：

| Wheel | SHA-256 |
| --- | --- |
| openpyxl-3.1.5-py2.py3-none-any.whl | 5282c12b107bffeef825f4617dc029afaf41d0ea60823bbb665ef3079dc79de2 |
| et_xmlfile-2.0.0-py3-none-any.whl | 7a91720bc756843502c3b7504c77b8fe44217c85c537d85037f0f536151b2caa |
| defusedxml-0.7.1-py2.py3-none-any.whl | a352e7e428770286cc899e2542b6cdaedb2b4953ff269a210103ec58f6198a61 |

这三个包只覆盖该 XLSX profile，不代表安装完整 documents extra。生产实验还需
冻结 Python、Praxile wheel、任务镜像与其他所需依赖，并对新环境单独标注，不能
将新增依赖后结果与旧环境直接视为严格 A/B。

## 验证方式

本机显式预检（不调用模型）：

```sh
python -m praxile.capability_probe --profile xlsx_values.v1
```

资源测试 `test_capability_probe.py` 使用实际 openpyxl 写出/回读/校验。
`test_harbor_capabilities.py` 覆盖声明、身份变化、成功/失败/无效报告及 run 门禁。
真实 Docker 用例需显式设置 PRAXILE_TEST_WHEEL、PRAXILE_TEST_LEASE_IMAGE、
PRAXILE_TEST_XLSX_WHEELS（只含上述三个 wheel 的目录），以 --forked 执行。
该用例验证纯 Python 镜像中离线安装、功能检查和系统 Python 未被污染，不调用模型。

2026-10-21 开发 worker wheel 摘要：
`1aa21edda9206d842ad0590108111b3eaa84c248695ef64f452d1e876cbb3094`，
固定镜像 `python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`。
这是当前工作树构建的探针测试 wheel，不是 pristine release。完整 Office 任务
在新依赖环境下的真实模型调用尚未重跑，不能把预检通过当作任务验收通过。

该 wheel 用于容器内探针；宿主适配器使用当前工作树代码，二者不是同一个
源码快照。最终适配器另有 renderer 失败后禁止 run 的回归检查。能力声明
目前仅接入 Harbor Native 参数，尚未自动推广到所有 owned runner 入口。
