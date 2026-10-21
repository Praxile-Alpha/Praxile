# Harbor 独立容器租约

`praxile.eval.harbor_lease.HarborDockerLease` 提供显式的
prepare/reset/execute/collect/cancel/cleanup 生命周期。它创建自己的 Harbor
Docker environment，不接管 `Trial` 传给 `PraxileAgent` 的环境，不自动替换旧 runner。

## 使用边界

- 可选运行环境固定 Harbor 0.18.0 或 0.22.0；不加入基础安装依赖。
- 镜像必须以 `@sha256:` 固定，内含 Linux/POSIX Python 3 和 shell。
- 可选 `inputs: tuple[InputArtifact, ...]` 与 `input_source_root` 必须同时提供。
  `source_ref` 在此接口仅接受来源根目录下的相对文件路径，不下载 URL。
  显式清单中的文件逐一校验 SHA-256、拒绝软/硬链接，复制为独立快照后以只读
  bind mount 挂到 `/workspace/inputs/<InputArtifact.path>`；来源目录及未声明文件不挂载。
  每文件最多 32 MiB、总计 128 MiB、最多 256 项；拒绝重复和祖先路径冲突。
  reset 重新校验快照的内容、根身份及文件集合，不重新读取可变来源文件。
  输入公开性仍由调用方负责分类；不能自动识别被错误声明为公开输入的 rubric。
- `lease.public_inputs` 返回新建的公开视图，仅含 `inputs/...` 相对路径和摘要，
  不含宿主 source_ref。Praxile Native 的文件工具以 `/workspace` 为根；早期的
  `/inputs` 挂载位于根外，不能通过路径安全检查，因此已迁入工作区。执行器应使用
  公开视图提供的路径，不拼接宿主路径；来源清单的路径不直接等于工具路径。
- 每代环境创建随机 Compose project/session；reset 删除自己的旧环境后创建新代。
- 固定工作目录 `/workspace`；执行 argv 经 shell quoting 传入 Harbor。
- 超时或取消会销毁整个租约容器，而不是尝试保留不确定的命令状态。继续工作需 reset。
- 取消 prepare/reset 会先等待所属操作结束，再删除环境；重复取消不能遗失所有权。
- cleanup 调用 Harbor stop 后查询自己 session 标签下的 container/network/volume。
  只有全部为空才记为 closed；daemon 不可用、查询失败或资源残留都报错。
- collect 在容器内复用 descriptor-relative 读取：拒绝链接和特殊文件、检查根目录
  身份和读取期间变化、限制字节数，以 base64 传输。它不是原子文件系统快照。
- 生命周期保留本地 trial 日志，不自动删除宿主目录；没有加载或传输模型凭据。

```python
from pathlib import Path
from praxile.eval.harbor_lease import HarborDockerLease

# 在调用方的 async 函数内；IMAGE_DIGEST 来自已验证的镜像摘要。
async with HarborDockerLease(Path(".praxile/leases"), image=IMAGE_DIGEST) as lease:
    result = await lease.execute(["python3", "-c", "print('ready')"], timeout=10)
    assert result.returncode == 0
    await lease.reset()
```

## 验证与未完成项

资源测试为 `tests/resource/test_harbor_lease.py`。默认不创建 Docker 环境；真实
测试需显式设置 `PRAXILE_TEST_LEASE_IMAGE`，使用固定版本环境和 `--forked`。
测试覆盖创建、执行、受限采集、reset、超时、取消和释放后的资源查询；fixture
覆盖 Harbor 吞掉清理错误、重复取消和清理重试。
额外真实容器测试传入当前源码包，调用 FileSystemEnv.read_file/list_dir/write_file，
验证输入可见、越界读取被阻止以及输入覆盖/新增触发只读文件系统错误。此验证不
调用模型，不等同于完整 AgentRuntime 或正式 benchmark 验收。

本后端不是敌对代码安全沙箱：只读输入已在显式清单路径实现，但尚未建立完整
下载/私有评测存储隔离、网络隔离策略或宿主资源配额。宿主同权限进程仍可能在
校验后修改快照；只读挂载不等于对恶意宿主的防护。Harbor 启动/停止自身没有整体硬期限；Docker daemon
失联、SIGKILL、断电后的跨进程恢复尚未验收。Python 执行输出仍由 Harbor 缓冲，
不能把 collect 的字节上限等同于所有命令的输出上限。正式 runner 接入和完整
benchmark 验收仍待完成，不据此勾选路线图的整个环境协议项。
