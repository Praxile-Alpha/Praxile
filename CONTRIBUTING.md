# Contributing

Praxile is an Alpha-stage local self-evolving agent harness. Contributions should preserve its core promise: every durable self-evolution update remains project-local, auditable, and user-approved.

## Local Setup

为了确保本地环境与 CI 一致，必须安装开发依赖，包括测试所需的各种 pytest 插件：

```bash
python -m pip install -e ".[dev]"
make test-fast
```

如果你在运行测试时遇到 `ModuleNotFoundError: No module named 'pytest_forked'` 或超时未生效等问题，这通常是因为缺少 `.[dev]` 的安装步骤。`pytest-timeout` 和 `pytest-forked` 是防止测试挂起（Hanging）的核心依赖，**必须安装**。

## Testing Philosophy & Guidelines

Praxile 的测试套件被严格分为三个级别：`fast`、`resource` 和 `integration`。这种分离是为了兼顾极速的开发反馈和深度的系统验证。

### 1. 为什么要分 fast / resource / integration？

- **Fast Tests (`make test-fast`)**：核心的单元测试。它们必须在几秒钟内运行完毕，不涉及任何昂贵的系统调用、长事务、真实的 HTTP 网络请求或跨进程通信。这是 TDD（测试驱动开发）的主循环。
- **Resource Tests (`make test-resource`)**：涉及进程级资源（如 `ShellEnv` 启动真实的 Shell 子进程）、网络端口（如 Gateway 服务器）、浏览器实例或深度的 SQLite 事务。这类测试如果发生异常崩溃，极易遗留脏数据、僵尸进程或文件锁。
- **Integration Tests (`make test-integration`)**：真实的端到端（E2E）测试。会操作真实的 Git 仓库，触发大模型的 API 调用，并验证长周期的 Agent 动作循环。

### 2. 为什么 Resource 测试需要 `--forked`？

在 `resource` 级别测试中，常常会涉及到 `asyncio` 事件循环的建立与销毁、守护线程的派生、以及 `sqlite3` 全局缓存的状态。如果让所有 resource 级别的测试在同一个进程内串行运行，前一个失败的测试很容易污染进程状态，导致后续测试出现莫名其妙的 `Database is locked` 或 `asyncio.run() cannot be called from a running event loop` 错误。

通过 `pytest-forked`，每个 resource 测试都在一个独立的子进程中运行，一旦测试结束，整个进程空间被销毁，彻底杜绝了状态污染。

### 3. 如何写不污染全局状态的测试？

如果你在开发新的功能并为其编写测试：
- **总是使用隔离环境**：不要硬编码操作 `.praxile/` 目录，必须通过临时 workspace 生成自动销毁的状态。
- **Mock 外部依赖**：对于 `fast` 级别的测试，必须 `patch` 掉 `subprocess.run`、`urllib.request.urlopen` 和 `builtins.input`。
- **显式标记资源测试**：shell、gateway、browser、terminal、HTTP client、SQLite/store/index、runtime/action loop 相关测试必须放在 `tests/resource/`，并显式加 `@pytest.mark.resource` 以及对应的细分 marker。不要依赖测试名自动分类。

### 4. 如何定位挂起（Hanging）的测试？

如果你在本地执行 `make test-fast-repeat` 或 `make test-resource` 时卡住：
1. **检查 Timeout 插件**：确保 `pytest-timeout` 已安装。当测试卡住时，超时插件会在 10~60 秒后强制 Kill 进程，并打印出挂起时的 Call Stack（调用栈）。
2. **检查悬挂的子进程**：Praxile 的 `ShellEnv` 如果在 Windows 下未正确结束，可能会遗留僵尸进程。通过系统的任务管理器（Task Manager）或 `htop` 查看是否有残留的编辑器进程（如 `vim`、`nano`）或 `git diff` 进程。
3. **检查 File Locks**：进入临时测试目录，查看是否有未释放的 `.lock` 文件。

## Development Rules

- Keep `.praxile/` state project-local.
- Do not add automatic writes to external/global memory.
- Do not bypass proposal approval for memory, skill, eval, routing, frozen-boundary, architecture-gate, or harness-rule updates.
- Keep dangerous command and sensitive-file protections conservative.
- Add or update tests for CLI, state layout, proposal application, rollback, or safety behavior changes.

## Useful Checks

```bash
python -m compileall praxile tests
make test-fast
make test-fast-repeat
make test-resource
make test-integration
python -m pytest -q
python scripts/clean_release.py
python scripts/clean_release.py --check
python -m build
twine check dist/*
praxile init --force
praxile doctor
```

Use `praxile doctor --online` only when model endpoint credentials are configured.

See `docs/contributing-testing.md` for marker rules, forked test commands, and hang-debugging steps.

## Example Coverage

When adding stack-specific behavior, update or add an example under `examples/` and make sure `praxile init` and `praxile doctor` explain what was detected.
