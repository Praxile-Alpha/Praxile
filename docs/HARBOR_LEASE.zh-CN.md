# Harbor 独立容器租约

`praxile.eval.harbor_lease.HarborDockerLease` 提供显式的
prepare/reset/execute/collect/cancel/cleanup 生命周期。它创建自己的 Harbor
Docker environment，不接管 `Trial` 传给 `PraxileAgent` 的环境，不自动替换旧 runner。

## 使用边界

- 可选运行环境固定 Harbor 0.18.0 或 0.22.0；不加入基础安装依赖。
- 镜像必须以 `@sha256:` 固定，内含 Linux/POSIX Python 3 和 shell。
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

本后端不是敌对代码安全沙箱：尚未建立输入只读挂载、网络隔离策略、私有评测
存储隔离或宿主资源配额。Harbor 启动/停止自身没有整体硬期限；Docker daemon
失联、SIGKILL、断电后的跨进程恢复尚未验收。Python 执行输出仍由 Harbor 缓冲，
不能把 collect 的字节上限等同于所有命令的输出上限。正式 runner 接入和完整
benchmark 验收仍待完成，不据此勾选路线图的整个环境协议项。
