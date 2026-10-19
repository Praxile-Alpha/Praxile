# 任务内浏览器交互

R1 增量能力：同一个 AgentRuntime 的浏览器可以保持页面状态，连续导航、填写、点击、断言文本并截图。不表示已实现完整浏览器工作流、下载、人工接管或崩溃恢复。

## 公开 URL 下载（后续增补）

`browser_download` 支持明确 HTTP(S) URL 的流式 GET 下载，不依赖 Playwright：

```json
{"type":"browser_download","url":"https://example.com/report.csv"}
```

需显式配置 browser.enabled=true、browser.allow_downloads=true 和非空 allowed_hosts。
max_download_bytes 默认 10000000，范围 1..100000000。初始 URL、每次重定向及最终 URL
均检查域名和嵌入凭据；禁用环境代理，不携带浏览器 cookie、Authorization 或连接器凭据。
HTTP 请求自身仍可能有服务端副作用，故 dry-run 阻止该动作。

只写入项目内 artifact_dir/browser 的随机 `.bin` 文件，不采用服务器文件名，
不自动执行或打开下载内容。大小超限、残缺响应、慢速超时及重定向被拒时不发布最终文件，
临时文件清理；成功记录路径、字节数、SHA-256 和来源 host，不记录完整认证查询 URL。
下载成功不代表内容安全或交付质量通过，需单独检查文件。

实现使用分块 read1、socket timeout 和分块间截止检查，避免 read(n) 长时间等待整块。
这不是硬实时期限或网络沙箱：DNS/系统调用、域名解析到的 IP 和站点自身权限仍需环境隔离。
不支持登录态导出、点击触发下载、自动 cookie 转发、续传或下载内容安全扫描。
Playwright 会话仍禁用隐式下载，原有安全边界不放宽。

6 项本机 HTTP 资源测试覆盖成功、声明超限、实际超限、残缺、跨域重定向及慢速响应；
单元测试覆盖 opt-in、空白名单、嵌入凭据、符号链接逃逸和真实 action-loop dry-run 分发。
该增补不使路线图的完整浏览器下载/接管项自动完成。

## 启用

### 用只读 DOM 状态约束经验激活

用户可在 `browser.observation_probes` 中定义最多 16 个 origin 范围内的探针：

```json
{
  "save_visible": {
    "origin": "http://localhost:3000",
    "selector": "#save-button",
    "kind": "visible"
  },
  "row_count": {
    "origin": "http://localhost:3000",
    "selector": "table tbody tr",
    "kind": "count"
  }
}
```

默认空配置。探针由控制面配置，不是模型 action；没有自定义 JavaScript、表单值或
页面文本读取。配置校验报告 `browser.observation_probes` 路径；BrowserEnv 创建时
复制定义，运行中修改配置对象不会悄悄更换同名探针含义。

`observe_state()` 只查看已存在且符合当前域名白名单的交互会话，不启动/导航页面。
返回 `dom.save_visible`（布尔）、`dom.row_count`（整数）以及规范化探针定义的
`dom_profile_sha256`。缺失/多匹配的 visible、选择器异常、跨 origin 或观测期间导航
均不提供匹配值；其中 count=0 是合法观察，不能与 unknown 混淆。
临时状态不包含 URL 路径、查询参数、输入值或异常原文。

经验侧车可声明：

```json
{
  "activation_requirements": {
    "state_equals": {
      "browser.dom.save_visible": true,
      "browser.dom_profile_sha256": "REPLACE_WITH_OBSERVED_PROFILE_SHA256"
    }
  }
}
```

摘要要求不是强制自动添加；推荐显式绑定，防止后续任务更换选择器定义但复用相同
经验条件名。原任务语义、anti-scope、工具版本检查仍然生效。已有 runtime 在每次
模型请求前重查条件，失效/未知时 checkpoint + needs_human，不继续使用旧上下文。
Deliverable V2 的 state_equals 也能检查这些已有会话状态，但不等同官方评审。

验证：13 项探针单元测试、运行时 DOM 条件失效/未知测试，以及 3 项真实 Chromium
资源测试通过。真实页面覆盖按钮隐藏前后状态、count 和稳定摘要。
尚无 DOM 原子快照、跨进程应用状态、完整业务状态机或视觉质量判断；页面可能在
观测之后变化，这不是替代实际操作权限检查的安全锁。

### 一次性页面与重定向边界（后续修复）

browser_open/browser_screenshot 与交互会话共用受限 context：显式非空域名白名单、
Service Worker 关闭、WebSocket 拦截、隐式下载关闭。一次性 context 无论成功或失败
都会释放；异常只记录类型，避免把 Playwright 的 URL/凭据原文写入错误消息。
截图目录不得经 .praxile 符号链接逃出项目，现有截图目标为符号链接时拒绝。

真实 Chromium 测试发现 route.continue_ 会自动跟随重定向，后续跳转不再经过同一
route 检查，事后检查最终 URL 已太迟。现在使用 route.fetch(max_redirects=0)，
301/302/303/307/308 均在返回给浏览器前 abort，其他响应再 fulfill 并释放响应对象。
**同白名单域内的自动重定向也暂时拒绝**；需要明确导航最终 URL。
该路径缓冲单次响应，不承诺支持 SSE/长流页面或网络级内存配额。
公开 URL 下载另有流式逐跳检查，不使用此路径。

21 项浏览器单元测试、3 项真实 Chromium 资源测试通过；后者覆盖交互、一次性打开、
截图、外部子资源及重定向，检查目标测试服务器没有收到越界请求。
测试为本机内容，不代表完整浏览器产品/登录流程兼容性或网络沙箱认证。

安装 `praxile[browser]`，运行 `python -m playwright install chromium`。交互需要 Playwright >=1.48，旧版本缺 WebSocket 拦截能力时失败关闭会话。

在项目配置中显式启用：

```json
{
  "browser": {
    "enabled": true,
    "allow_interactions": true,
    "allowed_hosts": ["localhost", "127.0.0.1"],
    "timeout_ms": 15000
  }
}
```

建议先在无真实账号、无付款/删除副作用的本地测试应用中使用。`allow_interactions` 允许对指定主机执行页面交互，不是逐次付款/提交审批机制；不要把日常登录的敏感站点随意加入名单。会话使用全新 browser context，不加载用户浏览器 profile。

## Runtime Actions

```json
{"type":"browser_navigate","url":"http://localhost:3000"}
{"type":"browser_fill","selector":"input[name=query]","value":"example"}
{"type":"browser_click","selector":"button[type=submit]"}
{"type":"browser_assert_text","selector":"[role=status]","value":"Saved"}
{"type":"browser_capture"}
```

这些 action 已接入 schema、工具分发和 runtime 提示词，不只是独立 API。调用和 Observation 由既有轨迹流程记录。文本断言是精确比较；截图记录当前页面而不重新导航，并明确标记 `visual_quality_verified=false`。旧的 browser_open/browser_screenshot 仍为一次性页面，不与新会话共享状态。

## 安全与生命周期

- 新会话必须有非空主机白名单；每个 HTTP 请求及重定向受名单检查，URL 内嵌凭据被拒绝。
- Service Worker、WebSocket 和自动下载默认关闭；不提供任意 JavaScript evaluate action。
- 不允许将交互动作加入只读并发 batch，避免共享页面的顺序竞态。
- dry-run 会拦截 browser_click/browser_fill，不向浏览器分发这两种修改动作；与文件写入和 shell 共用修改动作集合。导航/截图仍可访问允许的测试服务，dry-run 不是无网络模式。
- 操作错误返回异常类型，不回显可能包含输入值的 Playwright 异常消息。错误后关闭该会话；任务结束关闭 browser/context。
- 主机白名单不是网络沙箱，不校验 DNS 重绑定或每个端口；敏感场景仍需容器/网络策略隔离。
- 用户输入本身属于 action，可能进入既有轨迹；不要通过 browser_fill 传入长期密钥。凭据代理仍是独立未完成项。

## 验证与剩余项

8 项会话单元测试覆盖工具链、默认关闭、请求限制、错误脱敏和清理；真实 Chromium 集成测试覆盖本地页面连续操作、PNG 生成和越界重定向阻断。运行：

```sh
PRAXILE_TEST_BROWSER=1 python -B -m pytest -q -o addopts= \
  tests/resource/test_browser_interactions.py --forked
```

此测试显式标记 resource/integration/browser_resource，默认不要求用户安装 Chromium。截图存在不证明视觉质量；人工接管、受限下载、完整状态断言集合、异步库集成与跨进程恢复尚待实现，R1 浏览器总项不勾选。
