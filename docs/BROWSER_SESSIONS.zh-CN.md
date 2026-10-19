# 任务内浏览器交互

R1 增量能力：同一个 AgentRuntime 的浏览器可以保持页面状态，连续导航、填写、点击、断言文本并截图。不表示已实现完整浏览器工作流、下载、人工接管或崩溃恢复。

## 启用

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
