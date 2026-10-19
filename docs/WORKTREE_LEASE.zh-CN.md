# Git Worktree 环境租约

`praxile.eval.worktree_lease.WorktreeLease` 复用现有
`BenchmarkRepositoryPreparer`，提供通用 EnvironmentLease 的本地 Git 后端。
这是控制面管理的独立工作区，不是命令安全沙箱。

```python
from pathlib import Path
from praxile.eval.worktree_lease import WorktreeLease

# task 是已有 EvalTask；在调用方的异步函数中使用。
lease = await WorktreeLease.create(Path("/tmp/eval-state"), task,
                                  source_override=Path("/path/to/source"))
async with lease:
    result = await lease.execute(["python", "-m", "unittest"], timeout=60)
    # 先收集需要留存的产物；reset 会丢弃本租约内的修改和未跟踪文件。
    await lease.reset()
```

## 所有权与状态

- 每次 create 生成不同 lease run ID，即使任务相同也不共享可变 worktree。
- 首次解析出的 commit 固定为后续 reset 的版本，不跟随源分支移动。
- reset 通过原 preparer 重建专属 worktree，不执行用户源仓库的 reset。
- 执行期间拒绝 reset/cleanup；先取消并等待执行协程退出，再释放。
- 创建途中取消时等待后台 Git 操作结束，并删除它创建的 worktree。
- reset 失败或被取消后禁止继续执行；调用 cleanup 释放，再创建新租约。
- cleanup 幂等，只移除租约工作区；镜像缓存保留，源仓库保留。

## 验证与限制

11 项隔离资源测试通过：4 项新 worktree 测试、4 项既有本地租约测试、
3 项旧 Eval Runner 回归。验证真实本地 Git、冻结重置、并行身份隔离、
创建取消回收、重置失败关闭执行入口及既有 manifest/resume。

```sh
python -B -m pytest -q tests/resource/test_worktree_lease.py \
  tests/resource/test_benchmark_lease.py tests/resource/test_eval_v2_runner.py \
  --forked -o addopts=''
```

当前为 POSIX 后端，未提供只读材料挂载、命令权限沙箱或跨进程租约恢复。
旧 Eval Runner 尚未切换到此租约，因而不改变已有 manifest 身份。
Harbor 容器生命周期仍由 Harbor 管理，不在此后端创建第二层环境。
R0-A 的完整环境租约验收仍未完成；这些测试不是实际模型 benchmark 分数。
