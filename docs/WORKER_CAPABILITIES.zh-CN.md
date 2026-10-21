# Worker 功能预检

真实 Office 复验曾出现“可以用 Shell 生成 XLSX，但 Praxile opener 缺依赖”。
因此，依赖包安装成功与工具能力可用现在分开记录。Harbor Native 的 agent kwargs
可显式声明：

```json
{"required_capabilities":["xlsx_values.v1"]}
```

支持 `xlsx_values.v1`、`docx_text.v1` 和 `pdf_text.v1`，可以同时声明，所有声明均须通过。
前者覆盖新建值型工作簿、指定 sheet 读取、安全 opener 和 literal 字符串/数值
回读；后者实际创建标题、段落和表格，读取正文，通过 opener 并核对标题样式、
段落文本和表格单元格。不是公式计算、渲染、业务正确性或跨任务模型表现的证明。
未知或重复 profile 在构造时拒绝，检查项目和包版本按 profile 分别校验。

`pdf_text.v1` 生成不含任务数据的两页文本 PDF，分别通过 read_document 读取
各页，再调用 opener 确认页数，最后使用隔离的独立评审抽取子进程核对页面顺序、
完整文本及 text_sha256。记录 pypdf、pdfplumber、pdfminer.six 版本。不代表扫描
PDF 的 OCR、表格结构还原、渲染或视觉质量；该 profile 暂只通过本机功能和
适配器协议测试，完整固定依赖 Docker 环境尚待验收。

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
python -m praxile.capability_probe --profile docx_text.v1
python -m praxile.capability_probe --profile pdf_text.v1
```

资源测试 `test_capability_probe.py` 使用实际 openpyxl 写出/回读/校验。
`test_harbor_capabilities.py` 覆盖声明、身份变化、成功/失败/无效报告及 run 门禁。
真实 Docker 用例需显式设置 PRAXILE_TEST_WHEEL、PRAXILE_TEST_LEASE_IMAGE、
PRAXILE_TEST_XLSX_WHEELS（只含上述三个 wheel 的目录），以 --forked 执行。
该用例验证纯 Python 镜像中离线安装、功能检查和系统 Python 未被污染，不调用模型。

DOCX profile 记录 python-docx、lxml、defusedxml 的实际版本；部署时还须固定
它们的完整依赖 wheel（包括 typing-extensions），并匹配容器平台。DOCX Docker
用例额外设置 PRAXILE_TEST_DOCX_WHEELS（只含下表四个 wheel 的目录）。它不自动
安装完整 documents extra。此次下载沿用本机配置的 PyPI 镜像，记录实际文件摘要。

| DOCX Wheel | SHA-256 |
| --- | --- |
| python_docx-1.2.0-py3-none-any.whl | 3fd478f3250fbbbfd3b94fe1e985955737c145627498896a8a6bf81f4baf66c7 |
| lxml-5.4.0-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl | 529024ab3a505fed78fe3cc5ddc079464e709f6c892733e3f5842007cec8ac6e |
| typing_extensions-4.14.1-py3-none-any.whl | d1e1e3b58374dc93031d6eda2420a48ea44a36c2b4766a4fdeb3710755731d76 |
| defusedxml-0.7.1-py2.py3-none-any.whl | a352e7e428770286cc899e2542b6cdaedb2b4953ff269a210103ec58f6198a61 |

DOCX/XLSX 双 profile 容器复验使用 af8f2b3 开发 worker wheel，摘要
`1b8d10446089b717acfc691dfa56838bfe894149f1346550689f18da4ad83137`，737277 字节。
构建包含本地 README 改动，不是 pristine release。镜像引用沿用下述 python 摘要，
实际平台 linux/arm64，本地 image ID 为
`sha256:ce9a404c2c0138e747a43e6ea022d2f7e670ed868df35d627a663ba7fb940ea9`。
此依赖组合仅验收 Python 3.12 ARM64，不代表其他平台或 DOCX 视觉/语义质量。
`test_harbor_capabilities.py` 使用上述两组依赖和 --forked，在 Harbor 0.18.0 下
15 passed（22.09 秒），0.22.0 下 15 passed（21.66 秒），含两个真实容器用例。

2026-10-21 开发 worker wheel 摘要：
`1aa21edda9206d842ad0590108111b3eaa84c248695ef64f452d1e876cbb3094`，
固定镜像 `python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`。
这是当前工作树构建的探针测试 wheel，不是 pristine release。完整 Office 任务
在新依赖环境下的真实模型调用尚未重跑，不能把预检通过当作任务验收通过。

该 wheel 用于容器内探针；宿主适配器使用当前工作树代码，二者不是同一个
源码快照。最终适配器另有 renderer 失败后禁止 run 的回归检查。

## Owned Runner 与 CLI

独立 `run_owned_native` 接受同名 `dependency_wheels` 和 `required_capabilities`
参数。CLI `python -m praxile.eval.owned_native_cli` 在原有任务/镜像/模型参数外，
可重复添加 `--dependency-wheel /absolute/package.whl SHA256` 和
`--require-capability xlsx_values.v1`。每个依赖均须提供真实摘要，不下载或自动
解析依赖。默认仍为 plan only，只有 `--execute` 才启动容器和模型。

plan only 会校验声明、文件名及摘要，但不会执行功能探针。计划仅显示依赖
文件名和摘要，不显示本机绝对路径。真实执行经过相同的适配器预检。
依赖内容和 profile 进入 run ID 的冻结计划；变更后不能 `--resume` 旧结果。
成功完成时 worker 环境与能力报告加入完成证据摘要，篡改报告会使恢复失败。
未声明能力的历史运行保持兼容，不声称补做过预检；这也不提供中断动作重放。
